import base64
import hashlib
import io
import json
import os
from pathlib import Path
import time

from PIL import Image
import yaml


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)


def signature(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def credentials(cfg):
    if cfg.get('credentials_file'):
        raw=yaml.safe_load(Path(cfg['credentials_file']).read_text(encoding='utf-8'))
        model=raw['model'] if isinstance(raw.get('model'),dict) else raw
        values={k:model[k] for k in ['base_url','api_key','model']}
    else:values={k:os.environ.get(cfg[k+'_env'],'') for k in ['base_url','api_key','model']}
    if any(not isinstance(v,str) or not v.strip() for v in values.values()):
        raise ValueError('Configure credentials_file or VLM_CLUSTER_BASE_URL / VLM_CLUSTER_API_KEY / VLM_CLUSTER_MODEL')
    return values


def image_bytes(path,expected_hash,max_edge,quality):
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_hash:raise ValueError('Cached image hash mismatch: '+str(path))
    with Image.open(io.BytesIO(raw)) as im:
        im=im.convert('RGB');im.thumbnail((max_edge,max_edge),Image.Resampling.LANCZOS)
        output=io.BytesIO();im.save(output,format='JPEG',quality=quality)
        return output.getvalue(),list(im.size)


def request(folder,prompt,payload,images,cfg,auth,validator,transport=None):
    folder=Path(folder)
    if len(images)>cfg['max_images_per_request']:raise ValueError('Image budget exceeded; no automatic extra clustering round')
    folder.mkdir(parents=True,exist_ok=True);image_meta=[];prepared=[];parts=[{'type':'text','text':json.dumps(payload,ensure_ascii=False)}]
    for i,item in enumerate(images):
        raw,size=image_bytes(item['path'],item['sha256'],cfg['max_image_edge'],cfg['jpeg_quality'])
        path=folder/'images'/f'{i:04d}.jpg';prepared.append((path,raw))
        label={k:v for k,v in item.items() if k not in ('path','sha256')}
        image_meta.append(dict(**item,sent_path=str(path),sent_sha256=hashlib.sha256(raw).hexdigest(),sent_size=size))
        parts.extend([{'type':'text','text':json.dumps(label,ensure_ascii=False)},
                      {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(raw).decode()}}])
    params={k:cfg['api'][k] for k in ['thinking','temperature','max_tokens','stream']}
    identity=dict(prompt=prompt,payload=payload,images=image_meta,parameters=params,model=auth['model'],base_url=auth['base_url'])
    digest=signature(identity)
    saved=folder/'input.json'
    if saved.exists() and json.loads(saved.read_text())['signature']!=digest:raise ValueError('Request inputs changed; use a new output directory')
    for path,raw in prepared:
        path.parent.mkdir(exist_ok=True);path.write_bytes(raw)
    save(saved,dict(signature=digest,**identity))
    error=''
    if (folder/'output.json').exists():
        try:
            result=json.loads((folder/'output.json').read_text());validator(result);return result
        except (ValueError,KeyError,TypeError) as exc:
            error=str(exc).replace(auth['api_key'],'[REDACTED]')
    offset=len(list(folder.glob('attempt_*')))
    for attempt in range(cfg['api']['attempts']):
        attempt_dir=folder/f'attempt_{offset+attempt+1:03d}';attempt_dir.mkdir()
        started=time.time()
        retry='\n上次响应未通过格式或成员覆盖校验，请完整重新输出。'+error if error else ''
        messages=[{'role':'system','content':prompt+retry},{'role':'user','content':parts}]
        save(attempt_dir/'request_meta.json',dict(input_signature=digest,validation_feedback=error))
        try:
            if transport is not None:raw,finish=transport(messages,auth,cfg['api'])
            else:
                from openai import OpenAI
                with OpenAI(**{k:auth[k] for k in ['api_key','base_url']},timeout=cfg['api']['timeout_sec'],max_retries=0) as client:
                    kwargs=dict(model=auth['model'],messages=messages,temperature=params['temperature'],max_tokens=params['max_tokens'],stream=params['stream'])
                    if params['thinking'] is not None:kwargs['extra_body']={'chat_template_kwargs':{'enable_thinking':params['thinking']}}
                    response=client.chat.completions.create(**kwargs)
                    if params['stream']:
                        chunks=[];finish=None
                        with response:
                            for chunk in response:
                                if chunk.choices:
                                    choice=chunk.choices[0]
                                    if choice.delta.content:chunks.append(choice.delta.content)
                                    if choice.finish_reason:finish=choice.finish_reason
                        raw=''.join(chunks)
                    else:raw=response.choices[0].message.content or '';finish=response.choices[0].finish_reason
            (attempt_dir/'raw_response.txt').write_text(raw,encoding='utf-8')
            save(attempt_dir/'meta.json',dict(finish_reason=finish,elapsed_sec=time.time()-started))
            if finish!='stop':raise ValueError('Response incomplete: '+str(finish))
            result=json.loads(raw);validator(result);save(folder/'output.json',result);return result
        except Exception as exc:
            error=str(exc).replace(auth['api_key'],'[REDACTED]')
            save(attempt_dir/'error.json',dict(error=error,elapsed_sec=time.time()-started))
            if attempt+1<cfg['api']['attempts']:time.sleep(cfg['api']['retry_delay_sec'])
    raise RuntimeError(error)
