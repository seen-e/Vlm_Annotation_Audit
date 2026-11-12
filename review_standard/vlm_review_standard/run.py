import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time

import yaml

from annotatiuon_audit.episode_level_filter.client import complete, credentials
from annotatiuon_audit.episode_level_filter.data import read_json, save_json, signature
from .prompts import PROMPT


def build_jobs(episodes, tasks):
    """Match exact source text, never infer a task from an episode number."""
    by_text, ids = {}, set()
    for row in tasks:
        tid, text = row['task_index'], row['__index_level_0__']
        if type(tid) is not int or tid < 0 or tid in ids:
            raise ValueError('Invalid or duplicate task ID')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Empty instruction')
        ids.add(tid)
        job = by_text.setdefault(text, {'instruction': text, 'task_ids': [], 'episodes': []})
        job['task_ids'].append(tid)
    seen = set()
    for row in episodes:
        eid, labels = row['episode_id'], row['tasks']
        if not isinstance(eid, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', eid) or eid in seen:
            raise ValueError('Invalid or duplicate episode ID')
        if not isinstance(labels, list) or len(labels) != 1 or labels[0] not in by_text:
            raise ValueError(f'{eid}: expected exactly one known original instruction')
        job = by_text[labels[0]]
        tid = row.get('task_index', row.get('task_id'))
        if tid is None:
            if len(job['task_ids']) != 1:
                raise ValueError(f'{eid}: ambiguous task IDs; provide task_index in manifest')
            tid = job['task_ids'][0]
        if type(tid) is not int or tid not in job['task_ids']:
            raise ValueError(f'{eid}: task ID and original instruction disagree')
        seen.add(eid)
        job['episodes'].append({'episode_id': eid, 'task_id': tid})
    jobs = [j for j in by_text.values() if j['episodes']]
    if not jobs:
        raise ValueError('No episodes selected')
    for job in jobs:
        job['task_ids'].sort()
        job['episodes'].sort(key=lambda e: e['episode_id'])
        job['translation_id'] = signature(job['instruction'])
    return sorted(jobs, key=lambda j: j['translation_id'])


def load_config(path):
    path = Path(path).resolve()
    cfg = yaml.safe_load(path.read_text(encoding='utf-8'))
    for name in ('episode_manifest', 'tasks_path', 'output_root'):
        cfg[name] = str((path.parent / cfg[name]).resolve())
    model = cfg['model']
    if model.get('credentials_file'):
        model['credentials_file'] = str((path.parent / model['credentials_file']).resolve())
    for value in (cfg['task_batchsize'], model['attempts'], model['max_tokens']):
        if type(value) is not int or value < 1:
            raise ValueError('Batchsize, attempts and max_tokens must be positive integers')
    if model['thinking'] is not None and type(model['thinking']) is not bool:
        raise ValueError('thinking must be true, false or null')
    if model['timeout_sec'] <= 0 or model['retry_delay_sec'] < 0:
        raise ValueError('Invalid timeout/retry delay')
    output = Path(cfg['output_root'])
    for name in ('episode_manifest', 'tasks_path'):
        source = Path(cfg[name])
        if source == output or source.is_relative_to(output):
            raise ValueError('Output root must not contain source files')
    return cfg


def validate(result):
    if not isinstance(result, dict) or set(result) != {'instruction_zh'}:
        raise ValueError('Expected only instruction_zh')
    text = result['instruction_zh']
    if not isinstance(text, str) or not text.strip() or not re.search(r'[\u3400-\u9fff]', text):
        raise ValueError('instruction_zh must be nonempty Chinese text')


def translate(folder, identity, model, auth, transport):
    digest = signature(identity)
    input_path, output = folder / 'input.json', folder / 'output.json'
    if input_path.exists() and read_json(input_path)['signature'] != digest:
        raise ValueError('Request changed; use a new output_root')
    if output.exists() and not input_path.exists():
        raise ValueError('Output has no input provenance')
    save_json(input_path, dict(signature=digest, **identity))
    if output.exists():
        try:
            result = read_json(output)
            validate(result)
            return result, True
        except (ValueError, TypeError):
            output.rename(folder / f'invalid_output_{time.time_ns()}.json')
    offset = max([int(p.name[8:]) for p in folder.glob('attempt_*') if p.name[8:].isdigit()] or [0])
    feedback = ''
    for attempt in range(model['attempts']):
        dest = folder / f'attempt_{offset + attempt + 1:03d}'
        messages = [{'role': 'system', 'content': identity['prompt']},
                    {'role': 'user', 'content': json.dumps(identity['payload'], ensure_ascii=False)}]
        if feedback:
            messages.append({'role': 'user', 'content': '请重新输出完整JSON。上次失败：' + feedback})
        save_json(dest / 'messages.json', messages)
        started = time.monotonic()
        try:
            raw, finish = transport(messages, model, auth)
            (dest / 'raw_response.txt').write_text(raw, encoding='utf-8')
            save_json(dest / 'meta.json', {'finish_reason': finish, 'elapsed_sec': time.monotonic() - started})
            if finish != 'stop':
                raise ValueError('Incomplete response: ' + str(finish))
            result = json.loads(raw)
            validate(result)
            save_json(output, result)
            return result, False
        except Exception as exc:
            feedback = str(exc).replace(auth['api_key'], '[REDACTED]')
            save_json(dest / 'error.json', {'error': feedback})
            if attempt + 1 < model['attempts']:
                time.sleep(model['retry_delay_sec'])
    raise RuntimeError(feedback)


def map_episodes(jobs, translations):
    episodes = {}
    for job in jobs:
        result = translations[job['translation_id']]
        validate(result)
        for row in job['episodes']:
            if row['episode_id'] in episodes:
                raise ValueError('Duplicate episode in mapping')
            episodes[row['episode_id']] = {
                'task_id': row['task_id'], 'task': job['instruction'],
                'instruction_zh': result['instruction_zh'],
                'standard_source': 'instruction_translation',
                'review_standard': result['instruction_zh'],
                'translation_id': job['translation_id']}
    return dict(sorted(episodes.items()))


def run_jobs(jobs, cfg, auth, transport=complete):
    root = Path(cfg['output_root'])
    root.mkdir(parents=True, exist_ok=True)
    lock = root / '.run.lock'
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError('Output locked; check runner before removing stale .run.lock') from None
    try:
        model = cfg['model']
        identities = {j['translation_id']: {
            'prompt': PROMPT, 'payload': {'instruction': j['instruction']},
            'model': auth['model'], 'base_url': auth['base_url'], 'contract_version': 1,
            'parameters': {k: model[k] for k in ('thinking', 'temperature', 'max_tokens', 'stream')}
        } for j in jobs}
        plan = {'jobs': jobs, 'requests': {k: signature(v) for k, v in identities.items()}}
        if (root / 'plan.json').exists() and read_json(root / 'plan.json') != plan:
            raise ValueError('Inputs, prompt or model changed; choose a new output_root')
        for key, identity in identities.items():
            folder = root / 'translations' / key
            if (folder / 'input.json').exists() and read_json(folder / 'input.json')['signature'] != signature(identity):
                raise ValueError('Existing input differs: ' + key)
            if (folder / 'output.json').exists() and not (folder / 'input.json').exists():
                raise ValueError('Orphan translation: ' + key)
        save_json(root / 'plan.json', plan)
        results, failures = {}, {}
        started = time.monotonic()
        progress = {'total': len(jobs), 'completed': 0, 'cached': 0, 'failed': 0,
                    'episode_count': sum(len(j['episodes']) for j in jobs)}

        def report(finished=False):
            progress.update(finished=finished, elapsed_sec=round(time.monotonic() - started, 3),
                            all_succeeded=finished and not failures,
                            updated_at=datetime.now(timezone.utc).isoformat())
            save_json(root / 'progress.json', progress)
            save_json(root / 'failures.json', failures)
            print(json.dumps(progress), flush=True)

        report()
        with ThreadPoolExecutor(max_workers=cfg['task_batchsize']) as pool:
            pending = {pool.submit(translate, root / 'translations' / k, identity, model, auth, transport): k
                       for k, identity in identities.items()}
            for future in as_completed(pending):
                key = pending[future]
                try:
                    results[key], cached = future.result()
                    progress['completed'] += 1
                    progress['cached'] += int(cached)
                except Exception as exc:
                    failures[key] = str(exc).replace(auth['api_key'], '[REDACTED]')
                    progress['failed'] += 1
                report()
        if not failures:
            episodes = map_episodes(jobs, results)
            save_json(root / 'task_translations.json', [dict(
                translation_id=j['translation_id'], task_ids=j['task_ids'],
                instruction_en=j['instruction'], **results[j['translation_id']]) for j in jobs])
            save_json(root / 'episode_review_standard.json', {
                'schema_version': 1, 'generated_at': datetime.now(timezone.utc).isoformat(),
                'source_episode_manifest': cfg['episode_manifest'], 'source_tasks': cfg['tasks_path'],
                'standard_type': 'original_instruction_translation',
                'task_count': len({e['task_id'] for e in episodes.values()}),
                'translation_count': len(jobs), 'episode_count': len(episodes), 'episodes': episodes})
        elif (root / 'episode_review_standard.json').exists():
            (root / 'episode_review_standard.json').rename(root / f'previous_episode_review_standard_{time.time_ns()}.json')
        report(True)
        return progress
    finally:
        lock.rmdir()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=str(Path(__file__).with_name('config.yaml')))
    parser.add_argument('--dry-run', action='store_true', help='Validate task/episode mapping without API calls')
    args = parser.parse_args()
    cfg = load_config(args.config)
    import pyarrow.parquet as pq
    jobs = build_jobs(read_json(cfg['episode_manifest']), pq.read_table(cfg['tasks_path']).to_pylist())
    if args.dry_run:
        print(json.dumps({'unique_instructions': len(jobs), 'episodes': sum(len(j['episodes']) for j in jobs),
                          'task_ids': sorted({e['task_id'] for j in jobs for e in j['episodes']}), 'api_calls': 0}))
        return
    result = run_jobs(jobs, cfg, credentials(cfg['model']))
    if result['failed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
