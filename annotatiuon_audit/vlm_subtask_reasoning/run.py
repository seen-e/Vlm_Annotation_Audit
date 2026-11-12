"""Episode-level text-only reasoning enrichment; source annotations are read-only."""
import argparse
import copy
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml
from openai import OpenAI
from prompts import SYSTEM_PROMPT


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def payload_for(original, eid):
    annotation = copy.deepcopy(original)
    tasks = annotation.get('subtasks')
    if not isinstance(tasks, list) or not tasks or any(not isinstance(t, dict) for t in tasks):
        raise ValueError('Expected a nonempty subtasks list of objects')
    # Do not feed previous generated explanations back as evidence.
    for task in tasks:
        task.pop('reasoning', None)
        task.pop('reasoning_unavailable_reason', None)
    ids = [t.get('subtask_id') for t in tasks if t.get('subtask_id') is not None]
    if len({json.dumps(x, sort_keys=True) for x in ids}) != len(ids):
        raise ValueError('Duplicate subtask IDs')
    return dict(episode_id=eid, annotation=annotation, subtask_mapping=[
        dict(subtask_index=i, subtask_id=t.get('subtask_id')) for i, t in enumerate(tasks)])


def validate(result, payload):
    if not isinstance(result, dict) or set(result) != {'episode_id', 'subtasks'}:
        raise ValueError('Expected only episode_id/subtasks')
    if result['episode_id'] != payload['episode_id']:
        raise ValueError('Episode ID mismatch')
    items = result['subtasks']
    if not isinstance(items, list) or len(items) != len(payload['subtask_mapping']):
        raise ValueError('Subtask count mismatch')
    for item, mapping in zip(items, payload['subtask_mapping']):
        if not isinstance(item, dict) or set(item) != {'subtask_index', 'subtask_id', 'reasoning', 'reasoning_unavailable_reason'}:
            raise ValueError('Unexpected subtask fields')
        if type(item['subtask_index']) is not int or item['subtask_index'] != mapping['subtask_index']:
            raise ValueError('Subtask order mismatch')
        if json.dumps(item['subtask_id']) != json.dumps(mapping['subtask_id']):
            raise ValueError('Subtask ID mismatch')
        a, b = item['reasoning'], item['reasoning_unavailable_reason']
        text = lambda x: isinstance(x, str) and bool(x.strip())
        if not ((text(a) and b is None) or (a is None and text(b))):
            raise ValueError('Exactly one explanation field must be nonempty text; the other must be null')


def merge(original, result):
    out = copy.deepcopy(original)
    for task, item in zip(out['subtasks'], result['subtasks']):
        for key in ('reasoning', 'reasoning_unavailable_reason'):
            task[key] = item[key]
    return out


def credentials(cfg):
    m = cfg['model']
    base = yaml.safe_load(Path(m['credential_config']).read_text())['model'] if m.get('credential_config') else {}
    result = {}
    for key, local in [('base_url', 'base_url'), ('api_key', 'api_key'), ('model', 'name')]:
        result[key] = (os.environ.get(m.get(local + '_env', '')) or m.get(local)
                       or os.environ.get(base.get(key + '_env', '')) or base.get(key))
        if not result[key]:
            raise ValueError('Missing model setting: ' + local)
    return result


def process(source, dest, cfg, api):
    original = read(source)
    payload = payload_for(original, original.get('episode_id', source.parent.name))
    settings = {k: cfg['model'][k] for k in ('thinking', 'temperature', 'max_tokens', 'timeout_sec')}
    signature = digest(dict(source=original, prompt=SYSTEM_PROMPT, payload=payload,
                            settings=settings, model=api['model'], endpoint=api['base_url']))
    marker = dest / 'request_identity.json'
    if marker.exists() and read(marker)['signature'] != signature:
        raise ValueError('Input/prompt/model settings changed: use a new output directory')
    save(marker, dict(signature=signature, source=str(source)))
    save(dest / 'original.json', original)
    response_path = dest / 'reasoning.json'
    cached = False
    result = None
    if response_path.exists():
        try:
            result = read(response_path)
            validate(result, payload)
            cached = True
        except (ValueError, TypeError, KeyError):
            result = None
    if result is None:
        offset = max([int(p.name.split('_')[-1]) for p in dest.glob('attempt_*') if p.name.split('_')[-1].isdigit()], default=0)
        for n in range(cfg['runtime']['attempts']):
            attempt = dest / f'attempt_{offset+n+1:03d}'
            messages = [dict(role='system', content=SYSTEM_PROMPT),
                        dict(role='user', content=json.dumps(payload, ensure_ascii=False))]
            save(attempt / 'input.json', dict(messages=messages, settings=settings, model=api['model']))
            started = time.time()
            try:
                with OpenAI(
                    base_url=api['base_url'], api_key=api['api_key'], timeout=settings['timeout_sec'], max_retries=0) as client:
                    response = client.chat.completions.create(model=api['model'], messages=messages,
                        temperature=settings['temperature'], max_tokens=settings['max_tokens'],
                        extra_body={'chat_template_kwargs': {'enable_thinking': settings['thinking']}})
                save(attempt / 'response.json', response.model_dump())
                choice = response.choices[0]
                if choice.finish_reason != 'stop':
                    raise ValueError('Non-complete response: ' + str(choice.finish_reason))
                text = (choice.message.content or '').strip()
                if text.startswith('```'):
                    text = text.split('\n', 1)[1].rsplit('```', 1)[0]
                result = json.loads(text)
                validate(result, payload)
                save(response_path, result)
                save(attempt / 'status.json', dict(status='accepted', elapsed_sec=time.time()-started))
                break
            except Exception as exc:
                result = None
                error = str(exc).replace(api['api_key'], '[REDACTED]')
                save(attempt / 'error.json', dict(error=error, elapsed_sec=time.time()-started))
                if n + 1 == cfg['runtime']['attempts']:
                    raise RuntimeError(error) from None
                time.sleep(cfg['runtime']['retry_delay_sec'])
    save(dest / 'final.json', merge(original, result))
    status = dict(status='complete', reused=cached, subtasks=len(result['subtasks']),
                  unavailable=sum(t['reasoning'] is None for t in result['subtasks']), input_mode='text_only')
    save(dest / 'status.json', status)
    return status


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', default=str(Path(__file__).with_name('config.ymal')))
    p.add_argument('--run', action='store_true', help='Without this flag, validate/scan only; no API calls')
    p.add_argument('--limit', type=int)
    args = p.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text(encoding='utf-8'))
    root, output = (Path(cfg[k]['root']).resolve() for k in ('input', 'output'))
    if root == output or root in output.parents or output in root.parents:
        raise ValueError('Input and output directory trees must not overlap')
    for key in ('episode_batchsize', 'attempts'):
        if type(cfg['runtime'][key]) is not int or cfg['runtime'][key] < 1:
            raise ValueError(key + ' must be a positive integer')
    if type(cfg['model']['thinking']) is not bool:
        raise ValueError('thinking must be a YAML boolean')
    if cfg['model']['max_tokens'] <= 0 or cfg['model']['timeout_sec'] <= 0 or cfg['runtime']['retry_delay_sec'] < 0:
        raise ValueError('Invalid token, timeout or retry settings')
    files = sorted(f for f in root.glob(cfg['input']['glob']) if f.is_file())
    selection = cfg['input'].get('selection_file')
    if selection:
        ids = read(selection)
        if not isinstance(ids, list) or any(not isinstance(x, str) for x in ids) or len(ids) != len(set(ids)):
            raise ValueError('Selection must be a unique list of episode directory names')
        if set(ids) - {f.parent.name for f in files}:
            raise ValueError('Selected episodes missing')
        files = [f for f in files if f.parent.name in ids]
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError('limit must be positive')
        files = files[:args.limit]
    if not files or len({f.parent.name for f in files}) != len(files):
        raise ValueError('No input files or duplicate episode directory names')
    for f in files:
        obj = read(f)
        payload_for(obj, obj.get('episode_id', f.parent.name))
    print(json.dumps(dict(episodes=len(files), concurrency=cfg['runtime']['episode_batchsize'],
                         run=args.run, thinking=cfg['model']['thinking'])), flush=True)
    if not args.run:
        return
    api = credentials(cfg)
    import fcntl
    output.mkdir(parents=True, exist_ok=True)
    with (output / '.run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started, results = time.time(), []
        def progress():
            data = dict(total=len(files), finished=len(results), complete=sum(x['status']=='complete' for x in results),
                        failed=sum(x['status']=='failed' for x in results), elapsed_sec=time.time()-started,
                        status='finished' if len(results)==len(files) else 'running', results=results)
            save(output / 'progress.json', data)
            print(json.dumps({k:v for k,v in data.items() if k!='results'}), flush=True)
        progress()
        with ThreadPoolExecutor(max_workers=cfg['runtime']['episode_batchsize']) as pool:
            futures = {pool.submit(process, f, output/f.parent.name, cfg, api): f.parent.name for f in files}
            for future in as_completed(futures):
                eid = futures[future]
                try:
                    status = future.result()
                except Exception as exc:
                    status = dict(status='failed', error=str(exc).replace(api['api_key'], '[REDACTED]'))
                    save(output / eid / 'status.json', status)
                results.append(dict(episode=eid, **status))
                progress()
        if any(r['status']=='failed' for r in results):
            raise SystemExit(1)


if __name__ == '__main__':
    main()
