import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
import json
from pathlib import Path
import random
import time

import pyarrow.parquet as pq
import yaml

from .api import credentials,request,save,signature
from .round1_prompt import PROMPT as FIRST_PROMPT
from .round2_prompt import PROMPT as SECOND_PROMPT
from .schema import validate_round1,validate_round2


def roles(mode):
    if mode not in ('first','last','first_last'):raise ValueError('Invalid frame_mode')
    return ['first','last'] if mode=='first_last' else [mode]


def validate_config(cfg):
    roles(cfg['frame_mode'])
    for key in ['episodes_per_batch','representatives_per_cluster','task_workers','max_images_per_request','max_image_edge']:
        if type(cfg[key]) is not int or cfg[key]<1:raise ValueError('Invalid '+key)
    if not 1<=cfg['jpeg_quality']<=100:raise ValueError('Invalid JPEG quality')
    if cfg['api']['attempts']<1:raise ValueError('No API attempts configured')
    if cfg['episodes_per_batch']*len(roles(cfg['frame_mode']))>cfg['max_images_per_request']:
        raise ValueError('Round-one batch exceeds image budget')


def load_tasks(cfg):
    table=pq.read_table(Path(cfg['dataset_root'])/'meta/tasks.parquet').to_pylist()
    mapping={r['__index_level_0__']:int(r['task_index']) for r in table}
    if len(mapping)!=len(table):raise ValueError('Duplicate task mapping')
    rows=json.loads((Path(cfg['cache_root'])/'selection.json').read_text());groups=defaultdict(list);seen=set()
    selected=set(cfg['task_ids'])
    for row in rows:
        eid=row['episode_id'];tasks=row['tasks']
        if Path(eid).name!=eid or eid in ('.','..') or eid in seen:raise ValueError('Invalid episode ID')
        seen.add(eid)
        if len(tasks)!=1 or tasks[0] not in mapping:raise ValueError('Ambiguous task mapping')
        tid=mapping[tasks[0]]
        if selected and tid not in selected:continue
        folder=Path(cfg['cache_root'])/eid;meta=json.loads((folder/'metadata.json').read_text())
        if meta['source']['episode_id']!=eid or meta['source']['tasks']!=tasks:raise ValueError('Cache metadata mismatch')
        images=[]
        for role in roles(cfg['frame_mode']):
            frame=next(f for f in meta['frames'] if f['file']==role+'.png');path=folder/frame['file']
            if not path.is_file():raise ValueError('Missing image: '+str(path))
            images.append(dict(episode_id=eid,frame_role=role,path=str(path),sha256=frame['sha256'],
                               episode_frame_index=frame['episode_frame_index'],timestamp_sec=frame['timestamp_sec']))
        groups[tid].append(dict(episode_id=eid,task=tasks[0],images=images))
    if selected-set(groups):raise ValueError('Requested tasks not found')
    if not groups:raise ValueError('No episodes selected')
    for tid,items in groups.items():
        items.sort(key=lambda r:r['episode_id']);random.Random(cfg['shuffle_seed']+tid).shuffle(items)
    return dict(sorted(groups.items()))


def run_task(tid,items,cfg,root,auth,caller=request):
    folder=Path(root)/f'task_{tid:03d}';candidates=[];lookup={r['episode_id']:r for r in items}
    size=cfg['episodes_per_batch'];r=cfg['representatives_per_cluster']
    for batch_index,start in enumerate(range(0,len(items),size)):
        batch=items[start:start+size];ids=[i['episode_id'] for i in batch]
        payload=dict(round=1,task_id=tid,task=items[0]['task'],frame_mode=cfg['frame_mode'],
                     representatives_per_cluster=r,episodes=[dict(episode_id=i) for i in ids])
        result=caller(folder/'round1'/f'batch_{batch_index:03d}',FIRST_PROMPT,payload,
                      [im for item in batch for im in item['images']],cfg,auth,
                      lambda obj:validate_round1(obj,ids,r))
        for group_index,g in enumerate(result['groups']):
            candidates.append(dict(**g,group_id=f't{tid:03d}_b{batch_index:03d}_g{group_index:03d}',batch_index=batch_index))
    save(folder/'round1_candidates.json',candidates)
    images=[];descriptions=[]
    for g in candidates:
        descriptions.append({k:g[k] for k in ['group_id','status','common_features','distinguishing_features','representative_episode_ids']})
        descriptions[-1]['member_count']=len(g['member_episode_ids'])
        for eid in g['representative_episode_ids']:
            images.extend(dict(im,group_id=g['group_id']) for im in lookup[eid]['images'])
    if len(images)>cfg['max_images_per_request']:
        raise ValueError(f'Round two requires {len(images)} images, cap={cfg["max_images_per_request"]}; round one retained, no automatic third round')
    by_id={g['group_id']:g for g in candidates}
    payload=dict(round=2,task_id=tid,task=items[0]['task'],frame_mode=cfg['frame_mode'],candidate_groups=descriptions)
    result=caller(folder/'round2',SECOND_PROMPT,payload,images,cfg,auth,lambda obj:validate_round2(obj,by_id))
    final=[];assignments=[]
    for index,g in enumerate(result['groups']):
        members=[eid for gid in g['source_group_ids'] for eid in by_id[gid]['member_episode_ids']]
        cluster_id=f't{tid:03d}_c{index:03d}'
        final.append(dict(**g,cluster_id=cluster_id,member_episode_ids=members))
        for gid in g['source_group_ids']:
            for eid in by_id[gid]['member_episode_ids']:
                assignments.append(dict(episode_id=eid,task_id=tid,round1_group_id=gid,cluster_id=cluster_id,status=g['status']))
    assigned=[a['episode_id'] for a in assignments]
    if len(set(assigned))!=len(items) or set(assigned)!=set(lookup):raise ValueError('Final coverage mismatch')
    output=dict(task_id=tid,task=items[0]['task'],frame_mode=cfg['frame_mode'],episode_count=len(items),
                round1_group_count=len(candidates),final_group_count=len(final),groups=final,assignments=assignments)
    save(folder/'final.json',output);return output


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--config',default=str(Path(__file__).with_name('config.yaml')))
    ap.add_argument('--frame-mode',choices=['first','last','first_last'])
    ap.add_argument('--episodes-per-batch',type=int)
    ap.add_argument('--representatives-per-cluster',type=int)
    ap.add_argument('--task-id',type=int,action='append')
    ap.add_argument('--output-root')
    ap.add_argument('--dry-run',action='store_true')
    args=ap.parse_args();cfg_path=Path(args.config).resolve();cfg=yaml.safe_load(cfg_path.read_text())
    for key in ['frame_mode','episodes_per_batch','representatives_per_cluster','output_root']:
        value=getattr(args,key)
        if value is not None:cfg[key]=value
    if args.task_id is not None:cfg['task_ids']=args.task_id
    for key in ['cache_root','dataset_root','output_root']:
        cfg[key]=str((cfg_path.parent/Path(cfg[key])).resolve())
    if cfg['api'].get('credentials_file'):
        cfg['api']['credentials_file']=str((cfg_path.parent/Path(cfg['api']['credentials_file'])).resolve())
    validate_config(cfg);tasks=load_tasks(cfg);root=Path(cfg['output_root'])
    plan=[dict(task_id=tid,task=items[0]['task'],episode_ids=[r['episode_id'] for r in items],
               round1_batches=(len(items)+cfg['episodes_per_batch']-1)//cfg['episodes_per_batch'],
               round2_requests=1,round2_max_images=len(items)*len(roles(cfg['frame_mode']))) for tid,items in tasks.items()]
    if args.dry_run:
        save(root/'dry_run_plan.json',dict(config=cfg,tasks=plan,api_called=False))
        print(json.dumps(dict(tasks=len(tasks),episodes=sum(len(i) for i in tasks.values()),api_called=False,plan=str(root/'dry_run_plan.json'))));return
    auth=credentials(cfg['api'])
    public_cfg=dict(cfg,api={k:v for k,v in cfg['api'].items() if k!='credentials_file'})
    identity=dict(config=public_cfg,model=auth['model'],base_url=auth['base_url'],tasks=tasks,prompts=[FIRST_PROMPT,SECOND_PROMPT])
    key=signature(identity)
    if (root/'identity.json').exists() and json.loads((root/'identity.json').read_text())['signature']!=key:
        raise ValueError('Run configuration changed; use a new output directory')
    save(root/'identity.json',dict(signature=key));save(root/'config_snapshot.json',public_cfg);save(root/'plan.json',plan)
    save(root/'prompt_snapshot.json',dict(round1=FIRST_PROMPT,round2=SECOND_PROMPT))
    done=[];failures=[];started=time.time()
    with ThreadPoolExecutor(max_workers=cfg['task_workers']) as pool:
        futures={pool.submit(run_task,tid,items,cfg,root,auth):tid for tid,items in tasks.items()}
        for f in as_completed(futures):
            try:done.append(f.result())
            except Exception as exc:failures.append(dict(task_id=futures[f],error=str(exc).replace(auth['api_key'],'[REDACTED]')))
            save(root/'progress.json',dict(total_tasks=len(tasks),completed_tasks=len(done),failed_tasks=len(failures),elapsed_sec=time.time()-started))
    done.sort(key=lambda t:t['task_id'])
    save(root/'assignments.json',[a for t in done for a in t['assignments']]);save(root/'failures.json',failures)
    summary=dict(status='complete_with_failures' if failures else 'complete',tasks=len(tasks),completed_tasks=len(done),failed_tasks=len(failures),
                 episodes=sum(t['episode_count'] for t in done),groups=sum(t['final_group_count'] for t in done),elapsed_sec=time.time()-started)
    save(root/'summary.json',summary);print(json.dumps(summary))
    if failures:raise SystemExit(1)


if __name__=='__main__':main()
