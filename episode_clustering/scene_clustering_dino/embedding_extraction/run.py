"""Run from framework root: python -m embedding_extraction.run --config ..."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import time
import traceback

import numpy as np
import torch
import transformers
import yaml

from .model import Extractor
from .preprocess import letterbox


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
    return h.hexdigest()


def save(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2));tmp.replace(path)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--config',default=str(Path(__file__).with_name('config.yaml')))
    for name in ['layer-from-end','image-size','batch-size','limit-episodes']:
        ap.add_argument('--'+name,type=int)
    for name in ['model-path','cache-root','output-root','device','dtype']:
        ap.add_argument('--'+name)
    ap.add_argument('--force',action='store_true',default=None)
    args=vars(ap.parse_args());cfg=yaml.safe_load(Path(args.pop('config')).read_text())
    cfg.update({k:v for k,v in args.items() if v is not None})
    if cfg['batch_size']<1 or cfg['image_size']<16 or cfg['limit_episodes']<0:raise ValueError('Invalid size/limit')
    started=time.time(); cache=Path(cfg['cache_root']);rows=json.loads((cache/'selection.json').read_text())
    ids=[r['episode_id'] for r in rows]
    if len(ids)!=len(set(ids)) or any(Path(i).name!=i or i in ('.','..') for i in ids):raise ValueError('Invalid episode IDs')
    if cfg['limit_episodes']:rows=rows[:cfg['limit_episodes']]
    extractor=Extractor(cfg)
    model_files=sorted(p for p in Path(cfg['model_path']).iterdir() if p.suffix in ('.json','.safetensors'))
    signature=dict(model={p.name:sha(p) for p in model_files},code={p.name:sha(p) for p in Path(__file__).parent.glob('*.py')},
        settings={k:cfg[k] for k in ['layer_from_end','image_size','save_patches']},
        actual_dtype=extractor.dtype,device=extractor.device,torch=torch.__version__,transformers=transformers.__version__,
        token_normalization='selected_block_then_model_final_layernorm',spatial_normalization='whole_flattened_vector')
    key=hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest()
    root=Path(cfg['output_root'])/key[:16];root.mkdir(parents=True,exist_ok=True)
    save(root/'run_config.json',dict(config=cfg,signature=signature,signature_sha256=key,block_index_one_based=extractor.depth-cfg['layer_from_end']+1))
    print('OUTPUT='+str(root),flush=True)
    failures=[];success=[];pending=[]; skipped=0

    def progress(status='running'):
        save(root/'progress.json',dict(status=status,total_images=len(rows)*2,completed_images=len(success),skipped_images=skipped,
             failed_images=len(failures),elapsed_sec=round(time.time()-started,2)))

    def process(items):
        if not items:return
        try:
            values=extractor.extract([i['image'] for i in items],[i['weights'] for i in items])
        except Exception as exc:
            if len(items)>1:
                if torch.cuda.is_available():torch.cuda.empty_cache()
                middle=len(items)//2;process(items[:middle]);process(items[middle:]);return
            failures.append(dict(episode_id=items[0]['record']['episode_id'],frame=items[0]['record']['frame'],error=str(exc)));return
        for item,features in zip(items,values):
            try:
                dest=item['dest'];dest.parent.mkdir(parents=True,exist_ok=True)
                tmp=dest.with_suffix('.npz.tmp')
                buffer=io.BytesIO()
                np.savez(buffer,**features)
                tmp.write_bytes(buffer.getvalue())
                tmp.replace(dest)
                record=item['record'];record.update(output=str(dest),output_sha256=sha(dest),preprocess=item['prep'])
                save(dest.with_suffix('.json'),record);success.append(record)
            except Exception as exc:failures.append(dict(episode_id=item['record']['episode_id'],frame=item['record']['frame'],error=str(exc),traceback=traceback.format_exc()))

    for row in rows:
        eid=row['episode_id']
        for frame in ['first','last']:
            try:
                folder=cache/eid;meta=json.loads((folder/'metadata.json').read_text())
                if meta['source']['episode_id']!=eid:raise ValueError('Metadata identity mismatch')
                source=folder/(frame+'.png'); digest=sha(source)
                fm=next(f for f in meta['frames'] if f['file']==source.name)
                if fm['sha256']!=digest:raise ValueError('Input image hash mismatch')
                dest=root/eid/(frame+'.npz')
                record=dict(episode_id=eid,frame=frame,source=str(source),source_sha256=digest,signature_sha256=key,frame_metadata=fm,tasks=row['tasks'])
                if not cfg['force'] and dest.exists() and dest.with_suffix('.json').exists():
                    prior=json.loads(dest.with_suffix('.json').read_text())
                    if all(prior.get(k)==v for k,v in record.items()) and prior['output_sha256']==sha(dest):
                        success.append(prior);skipped+=1;continue
                im,w,prep=letterbox(source,cfg['image_size'],extractor.patch,extractor.processor.image_mean)
                pending.append(dict(image=im,weights=w,prep=prep,dest=dest,record=record))
                if len(pending)>=cfg['batch_size']:process(pending);pending=[];progress()
            except Exception as exc:failures.append(dict(episode_id=eid,frame=frame,error=str(exc)))
    process(pending)
    save(root/'manifest.json',success);save(root/'failures.json',failures)
    progress('complete_with_failures' if failures else 'complete')
    print((root/'progress.json').read_text(),flush=True)
    if failures:raise SystemExit(1)


if __name__=='__main__':main()
