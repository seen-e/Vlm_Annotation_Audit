import argparse
from collections import defaultdict,Counter
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import time

import numpy as np
import pyarrow.parquet as pq
from PIL import Image
import sklearn
from sklearn.cluster import HDBSCAN


def save(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2))


def norm(x):
    x=np.asarray(x,dtype=np.float32);n=np.linalg.norm(x,axis=-1,keepdims=True)
    if not np.isfinite(x).all() or np.any(n<1e-12):raise ValueError('Invalid vector')
    return x/n


def combine(first,last):return norm(np.concatenate([norm(first)*np.sqrt(.5),norm(last)*np.sqrt(.5)],axis=-1))


def embedding(first,last,mode):
    if mode=='last_cls':return norm(last)
    if mode=='dual_cls':return combine(first,last)
    raise ValueError('Unknown feature mode')


def cluster(x,min_size=5,min_samples=2):
    if len(x)==1:return np.array([0]),[None],'insufficient_samples'
    if len(x)<8:return np.full(len(x),-1),[None]*len(x),'insufficient_samples'
    model=HDBSCAN(min_cluster_size=min_size if len(x)>=30 else min(3,min_size),min_samples=min_samples,
                  metric='euclidean',cluster_selection_method='eom',allow_single_cluster=True)
    labels=model.fit_predict(x)
    count=len(set(labels)-{-1})
    state='multiple_scenes_candidate' if count>1 else 'single_scene_candidate' if count==1 and not (labels==-1).any() else 'uncertain'
    return labels,model.probabilities_.tolist(),state


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--features',default='/mnt/data/chachaxu/embedding_cluster/robodojo_embeddings_fast/b82d9eb5438dcf56')
    ap.add_argument('--dataset',default='/mnt/data/chengyezhao/RoboDojo_ee_lerobot_v30_video')
    ap.add_argument('--output',required=True)
    ap.add_argument('--min-cluster-size',type=int,default=5)
    ap.add_argument('--min-samples',type=int,default=2)
    ap.add_argument('--feature-mode',choices=['dual_cls','last_cls'],default='dual_cls')
    args=ap.parse_args();start=time.time();root=Path(args.output)
    root.mkdir(parents=True,exist_ok=False)
    save(root/'run_config.json',dict(**vars(args),feature='L2(last_cls)' if args.feature_mode=='last_cls' else 'concat(sqrt(0.5)*first_cls,sqrt(0.5)*last_cls), then L2',
         sklearn_version=sklearn.__version__,metric='euclidean',allow_single_cluster=True,cluster_selection_method='eom',
         meaning='Final-state candidates' if args.feature_mode=='last_cls' else 'Initial scene plus final state candidates, not initial-scene ground truth'))
    feature_root=Path(args.features)
    shutil.copyfile(feature_root/'config.json',root/'feature_config.json')
    task_rows=pq.read_table(Path(args.dataset)/'meta/tasks.parquet').to_pylist()
    task_map={r['__index_level_0__']:int(r['task_index']) for r in task_rows}
    if len(task_map)!=len(task_rows):raise ValueError('Duplicate task texts')
    episodes=defaultdict(dict)
    for shard in json.loads((feature_root/'manifest.json').read_text()):
        raw=Path(shard['file']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=shard['sha256']:raise ValueError('Feature shard hash mismatch')
        with np.load(io.BytesIO(raw),allow_pickle=False) as arrays:
            vectors=arrays['cls'];assert len(vectors)==len(shard['records'])
            for r,v in zip(shard['records'],vectors):
                if r['frame'] not in ('first','last') or r['frame'] in episodes[r['episode_id']]:raise ValueError('Invalid or duplicate frame')
                episodes[r['episode_id']][r['frame']]=(r,v.copy())
    groups=defaultdict(list);failures=[]
    for eid,frames in sorted(episodes.items()):
        if set(frames)!= {'first','last'}:
            failures.append(dict(episode_id=eid,error='Missing paired frame'));continue
        a,av=frames['first'];b,bv=frames['last']
        if a['tasks']!=b['tasks'] or len(a['tasks'])!=1 or a['tasks'][0] not in task_map:
            raise ValueError('Task mapping ambiguous: '+eid)
        groups[task_map[a['tasks'][0]]].append(dict(episode_id=eid,task=a['tasks'][0],first=a,last=b,vector=embedding(av,bv,args.feature_mode)))
    viewer=root/'viewer';(viewer/'images').mkdir(parents=True)
    tasks=[];all_csv=[];image_jobs=[]
    for tid,items in sorted(groups.items()):
        x=np.stack([r['vector'] for r in items]);labels,prob,state=cluster(x,args.min_cluster_size,args.min_samples)
        sim=np.clip(x@x.T,-1,1);clusters=[];records=[];lookup={}
        for label in sorted(set(labels)-{-1}):
            indices=np.flatnonzero(labels==label);inside=sim[np.ix_(indices,indices)]
            medoid=int(indices[np.argmax(inside.mean(1))]);ordered=sorted(indices,key=lambda i:(-float(sim[i,medoid]),items[i]['episode_id']))
            center=[int(i) for i in ordered[:8]];boundary=[int(i) for i in ordered[::-1][:5]]
            pair=inside[np.triu_indices(len(indices),1)]
            clusters.append(dict(cluster_id=int(label),size=len(indices),medoid_episode_id=items[medoid]['episode_id'],
                representative_episode_ids=[items[i]['episode_id'] for i in center],boundary_episode_ids=[items[i]['episode_id'] for i in boundary],
                intra_cosine_median=float(np.median(pair)) if len(pair) else None,intra_cosine_p05=float(np.quantile(pair,.05)) if len(pair) else None,
                episode_ids=[items[i]['episode_id'] for i in indices]))
            for i in indices:lookup[int(i)]=(float(sim[i,medoid]),int(i) in center,int(i) in boundary,int(i)==medoid)
        cross=sorted(((float(sim[i,j]),i,j) for i in range(len(items)) for j in range(i+1,len(items)) if labels[i]>=0 and labels[j]>=0 and labels[i]!=labels[j]),reverse=True)[:10]
        cross_ids={i for _,i,j in cross}|{j for _,i,j in cross}
        for i,item in enumerate(items):
            s,center,boundary,medoid=lookup.get(i,(None,False,False,False))
            record=dict(episode_id=item['episode_id'],cluster_id=int(labels[i]),membership_strength=prob[i],medoid_similarity=s,
                center=center,boundary=boundary,medoid=medoid,cross=i in cross_ids,image='images/'+item['episode_id']+'.jpg',
                start_frame_path=item['first']['source'],end_frame_path=item['last']['source'])
            records.append(record);image_jobs.append(item)
            all_csv.append(dict(task_id=tid,**record,group_status=state,review_flag=True,review_reason='Candidate grouping; human review pending'))
        task=dict(task_id=tid,task=items[0]['task'],episode_count=len(items),cluster_count=len(clusters),noise_count=int(sum(labels==-1)),
            noise_ratio=float(np.mean(labels==-1)),group_status=state,clusters=clusters,episodes=records,
            cross_pairs=[dict(a=items[i]['episode_id'],b=items[j]['episode_id'],cosine=s) for s,i,j in cross])
        tasks.append(task);folder=root/f'task_{tid:03d}'
        save(folder/'clusters.json',task)
        save(folder/'metrics.json',{k:task[k] for k in ['episode_count','cluster_count','noise_count','noise_ratio','group_status','cross_pairs']})
        buf=io.BytesIO();np.savez(buf,episode_ids=np.array([r['episode_id'] for r in items]),embedding=x,labels=labels)
        (folder/'episode_embeddings.npz').write_bytes(buf.getvalue())
        print(json.dumps(dict(task_id=tid,clusters=len(clusters),noise=task['noise_count'])),flush=True)

    def thumbnail(item):
        canvas=Image.new('RGB',(640,240))
        for j,name in enumerate(['first','last']):
            record=item[name];raw=Path(record['source']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=record['source_sha256']:raise ValueError('Source frame changed')
            with Image.open(io.BytesIO(raw)) as im:
                im=im.convert('RGB');im.thumbnail((320,240),Image.Resampling.LANCZOS)
                canvas.paste(im,(j*320+(320-im.width)//2,(240-im.height)//2))
        buffer=io.BytesIO();canvas.save(buffer,format='JPEG',quality=90)
        (viewer/'images'/(item['episode_id']+'.jpg')).write_bytes(buffer.getvalue())
    with ThreadPoolExecutor(max_workers=16) as pool:list(pool.map(thumbnail,image_jobs))
    with (root/'episode_clusters.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(all_csv[0]));writer.writeheader();writer.writerows(all_csv)
    summary=dict(feature_mode=args.feature_mode,tasks=len(tasks),episodes=len(all_csv),clusters=sum(t['cluster_count'] for t in tasks),noise=sum(t['noise_count'] for t in tasks),
                 task_states=dict(Counter(t['group_status'] for t in tasks)),elapsed_sec=time.time()-start,failed_episodes=len(failures))
    save(root/'summary.json',summary);save(root/'failures.json',failures)
    (viewer/'data.js').write_text('window.DATA='+json.dumps(tasks,ensure_ascii=False)+';\nwindow.SUMMARY='+json.dumps(summary)+';',encoding='utf-8')
    shutil.copyfile(Path(__file__).with_name('viewer.html'),viewer/'index.html')
    print(json.dumps(summary),flush=True)


if __name__=='__main__':main()
