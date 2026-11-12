"""Prefetched, batched DINOv3 extraction with asynchronous NPZ shards."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import time

import numpy as np
import torch
import yaml

from .model import Extractor
from .preprocess import letterbox, normalize
from .run import sha, save


@torch.inference_mode()
def infer(extractor,pixels,weights):
    extractor.captured=None
    output=extractor.model(pixel_values=pixels.to(extractor.device,non_blocking=True),output_hidden_states=False)
    tokens=output.last_hidden_state if extractor.k==1 else extractor.model.norm(extractor.captured)
    grid=extractor.cfg['image_size']//extractor.patch
    if tokens.shape[1:]!=(1+extractor.registers+grid*grid,extractor.dim):
        raise ValueError('Token shape mismatch')
    patches=tokens[:,1+extractor.registers:].float().reshape(len(tokens),grid,grid,-1)
    weights=weights.to(extractor.device,non_blocking=True)
    denom=weights.sum((-2,-1))
    if (denom<=0).any():raise ValueError('Empty spatial region')
    pooled=torch.einsum('bqhw,bhwd->bqd',weights,patches)/denom[:,:,None]
    # One CPU transfer for the three main feature types.
    joined=torch.cat([normalize(tokens[:,0]),normalize(pooled[:,0]),normalize(pooled[:,1:].flatten(1))],dim=1).cpu().numpy()
    d=extractor.dim
    result=dict(cls=joined[:,:d],patch_mean=joined[:,d:2*d],spatial_2x2=joined[:,2*d:])
    if extractor.cfg['save_patches']:
        result.update(patches=normalize(patches).cpu().numpy(),patch_valid_weights=weights[:,0].cpu().numpy())
    extractor.captured=None
    return result


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--config',default=str(Path(__file__).with_name('config.yaml')))
    ap.add_argument('--batch-size',type=int,default=512)
    ap.add_argument('--io-workers',type=int,default=16)
    ap.add_argument('--layer-from-end',type=int)
    ap.add_argument('--limit-episodes',type=int,default=0)
    ap.add_argument('--output-root',default='/mnt/data/chachaxu/embedding_cluster/robodojo_embeddings_fast')
    ap.add_argument('--force',action='store_true')
    args=ap.parse_args()
    if args.batch_size<1 or args.io_workers<1 or args.limit_episodes<0:raise ValueError('Invalid batch/workers/limit')
    started=time.perf_counter();torch.set_num_threads(1)
    cfg=yaml.safe_load(Path(args.config).read_text());cfg['batch_size']=args.batch_size
    if args.layer_from_end is not None:cfg['layer_from_end']=args.layer_from_end
    e=Extractor(cfg)
    identity=dict(model={p.name:sha(p) for p in sorted(Path(cfg['model_path']).iterdir()) if p.suffix in ('.json','.safetensors')},
        code={p.name:sha(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
        layer_from_end=cfg['layer_from_end'],image_size=cfg['image_size'],save_patches=cfg['save_patches'],
        dtype=e.dtype,device=e.device,batch_size=args.batch_size,torch=torch.__version__,
        normalization='block_plus_final_layernorm;whole_spatial_vector')
    key=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    root=Path(args.output_root)/key[:16];root.mkdir(parents=True,exist_ok=True)
    save(root/'config.json',dict(config=cfg,identity=identity,signature=key,io_workers=args.io_workers))
    print('OUTPUT='+str(root),flush=True)
    rows=json.loads((Path(cfg['cache_root'])/'selection.json').read_text())
    if args.limit_episodes:rows=rows[:args.limit_episodes]
    ids=[r['episode_id'] for r in rows]
    if len(set(ids))!=len(ids) or any(Path(i).name!=i or i in ('.','..') for i in ids):raise ValueError('Invalid IDs')
    jobs=[(r,name) for r in rows for name in ['first','last']]
    batches=[jobs[i:i+args.batch_size] for i in range(0,len(jobs),args.batch_size)]
    failures=[];completed=0;skipped=0;inference_sec=0.;read_sec=0.;write_sec=0.;manifest=[]
    if e.device.startswith('cuda'):torch.cuda.reset_peak_memory_stats()

    def load(job):
        row,name=job;folder=Path(cfg['cache_root'])/row['episode_id']
        try:
            meta=json.loads((folder/'metadata.json').read_text())
            if meta['source']['episode_id']!=row['episode_id']:raise ValueError('Metadata mismatch')
            source=folder/(name+'.png');raw=source.read_bytes();digest=hashlib.sha256(raw).hexdigest()
            fm=next(f for f in meta['frames'] if f['file']==source.name)
            if digest!=fm['sha256']:raise ValueError('Image hash mismatch')
            im,w,geometry=letterbox(io.BytesIO(raw),cfg['image_size'],e.patch,e.processor.image_mean)
            record=dict(episode_id=row['episode_id'],frame=name,source=str(source),source_sha256=digest,
                        frame_metadata=fm,tasks=row['tasks'],preprocess=geometry)
            return (im,w,record),None
        except Exception as exc:return None,dict(episode_id=row['episode_id'],frame=name,error=str(exc))

    def prepare(batch,readers):
        t=time.perf_counter();pairs=list(readers.map(load,batch))
        good=[v for v,err in pairs if v is not None];bad=[err for v,err in pairs if err]
        if not good:return None,None,[],bad,time.perf_counter()-t
        pixels=e.processor(images=[v[0] for v in good],do_resize=False,return_tensors='pt')['pixel_values']
        if pixels.shape[-2:]!=(cfg['image_size'],)*2:raise ValueError('Processor resized image')
        weights=torch.stack([v[1] for v in good])
        if e.device.startswith('cuda'):pixels=pixels.pin_memory();weights=weights.pin_memory()
        return pixels,weights,[v[2] for v in good],bad,time.perf_counter()-t

    def write(index,features,records):
        t=time.perf_counter();folder=root/'shards';folder.mkdir(exist_ok=True)
        path=folder/f'{index:05d}.npz';buf=io.BytesIO();np.savez(buf,**features)
        raw=buf.getvalue();tmp=path.with_suffix('.npz.tmp');tmp.write_bytes(raw);tmp.replace(path)
        descriptor=dict(file=str(path),sha256=hashlib.sha256(raw).hexdigest(),signature=key,records=records)
        save(path.with_suffix('.json'),descriptor)
        return descriptor,time.perf_counter()-t

    def accept(future):
        nonlocal completed,write_sec
        desc,duration=future.result();manifest.append(desc);completed+=len(desc['records']);write_sec+=duration

    def report(status):
        elapsed=time.perf_counter()-started
        obj=dict(status=status,total_images=len(jobs),completed_images=completed,skipped_images=skipped,failed_images=len(failures),
            elapsed_sec=round(elapsed,3),images_per_sec=round(completed/elapsed,3),
            read_preprocess_worker_sec=round(read_sec,3),inference_pool_transfer_sec=round(inference_sec,3),writer_worker_sec=round(write_sec,3),
            timing_note='Stages overlap: worker times must not be summed as wall time',
            peak_allocated_gib=torch.cuda.max_memory_allocated()/2**30 if e.device.startswith('cuda') else 0,
            peak_reserved_gib=torch.cuda.max_memory_reserved()/2**30 if e.device.startswith('cuda') else 0)
        save(root/'progress.json',obj);print(json.dumps(obj),flush=True)

    with ThreadPoolExecutor(max_workers=args.io_workers) as readers, ThreadPoolExecutor(max_workers=1) as prefetch, ThreadPoolExecutor(max_workers=1) as writer:
        upcoming=prefetch.submit(prepare,batches[0],readers) if batches else None
        writes=[]
        for index in range(len(batches)):
            pixels,weights,records,bad,duration=upcoming.result();read_sec+=duration;failures.extend(bad)
            upcoming=prefetch.submit(prepare,batches[index+1],readers) if index+1<len(batches) else None
            if not records:continue
            descriptor=root/'shards'/f'{index:05d}.json'
            if not args.force and descriptor.exists():
                old=json.loads(descriptor.read_text())
                if old.get('signature')==key and old.get('records')==records and Path(old['file']).exists() and sha(old['file'])==old['sha256']:
                    manifest.append(old);completed+=len(records);skipped+=len(records);report('running');continue
            t=time.perf_counter()
            features=infer(e,pixels,weights)
            inference_sec+=time.perf_counter()-t
            writes.append(writer.submit(write,index,features,records))
            if len(writes)>=2:accept(writes.pop(0))
            report('running')
        for future in writes:accept(future)
    manifest.sort(key=lambda d:d['file'])
    save(root/'manifest.json',manifest);save(root/'failures.json',failures)
    report('complete_with_failures' if failures else 'complete')
    if failures:raise SystemExit(1)


if __name__=='__main__':main()
