import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import yaml

from .client import complete, credentials
from .data import prepare, read_json, save_json, signature
from . import prompts
from .schema import validate


PARAMETERS = ('thinking', 'temperature', 'max_tokens', 'stream')


def load_config(path):
    path = Path(path).resolve()
    cfg = yaml.safe_load(path.read_text(encoding='utf-8'))
    for name in ('standards_path', 'annotations_root', 'output_root'):
        cfg[name] = str((path.parent / cfg[name]).resolve())
    if cfg['model'].get('credentials_file'):
        cfg['model']['credentials_file'] = str((path.parent / cfg['model']['credentials_file']).resolve())
    for key, value in [('episode_batchsize', cfg['episode_batchsize']), ('attempts', cfg['model']['attempts']),
                       ('input_workers', cfg.get('input_workers', 1)),
                       ('max_tokens', cfg['model']['max_tokens'])]:
        if type(value) is not int or value < 1:
            raise ValueError(key + ' must be a positive integer')
    if cfg['model']['timeout_sec'] <= 0 or cfg['model']['retry_delay_sec'] < 0:
        raise ValueError('Invalid timeout/retry delay')
    if cfg['model']['thinking'] is not None and type(cfg['model']['thinking']) is not bool:
        raise ValueError('thinking must be true, false or null')
    output = Path(cfg['output_root'])
    source = Path(cfg['annotations_root'])
    if output == source or source.is_relative_to(output) or output.is_relative_to(source):
        raise ValueError('Output must be separate from annotation source')
    return cfg


def request_identity(job, prompt, cfg, auth):
    payload = {k: v for k, v in job.items() if k != 'annotation_path'}
    identity = {'prompt': prompt, 'payload': payload,
                'model': auth['model'], 'base_url': auth['base_url'],
                'parameters': {k: cfg[k] for k in PARAMETERS}, 'contract_version': 1}
    return identity, signature(identity)


def review_one(folder, job, prompt, cfg, auth, transport=complete):
    folder = Path(folder)
    identity, digest = request_identity(job, prompt, cfg, auth)
    input_path, output_path = folder / 'input.json', folder / 'output.json'
    if input_path.exists() and read_json(input_path)['signature'] != digest:
        raise ValueError('Inputs changed; choose a new output_root')
    if output_path.exists() and not input_path.exists():
        raise ValueError('Orphan output without input provenance')
    save_json(input_path, dict(signature=digest, annotation_path=job['annotation_path'], **identity))
    feedback = ''
    if output_path.exists():
        try:
            result = read_json(output_path)
            validate(result, identity['payload'])
            return result, True
        except (ValueError, TypeError, KeyError, IndexError):
            archived = folder / ('invalid_output_' + str(time.time_ns()) + '.json')
            output_path.rename(archived)
    offset = max([int(p.name[8:]) for p in folder.glob('attempt_*') if p.name[8:].isdigit()] or [0])
    for attempt in range(cfg['attempts']):
        directory = folder / f'attempt_{offset + attempt + 1:03d}'
        directory.mkdir()
        messages = [{'role': 'system', 'content': prompt},
                    {'role': 'user', 'content': json.dumps(identity['payload'], ensure_ascii=False)}]
        if feedback:
            messages.append({'role': 'user', 'content': '上次响应格式校验失败，请重新输出完整JSON：' + feedback})
        save_json(directory / 'messages.json', messages)
        started = time.monotonic()
        try:
            raw, finish = transport(messages, cfg, auth)
            (directory / 'raw_response.txt').write_text(raw, encoding='utf-8')
            save_json(directory / 'meta.json', {'finish_reason': finish, 'elapsed_sec': time.monotonic() - started})
            if finish != 'stop':
                raise ValueError('Incomplete response: ' + str(finish))
            result = json.loads(raw)
            validate(result, identity['payload'])
            save_json(output_path, result)
            return result, False
        except Exception as exc:
            feedback = str(exc).replace(auth['api_key'], '[REDACTED]')
            save_json(directory / 'error.json', {'error': feedback, 'elapsed_sec': time.monotonic() - started})
            if attempt + 1 < cfg['attempts']:
                time.sleep(cfg['retry_delay_sec'])
    raise RuntimeError(feedback)


def run_jobs(jobs, cfg, auth, prompt, transport=complete):
    root = Path(cfg['output_root'])
    root.mkdir(parents=True, exist_ok=True)
    lock = root / '.run.lock'
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError('Output is locked. Verify no runner is alive before removing stale .run.lock') from None
    started = time.monotonic()
    try:
        # Check all request identities before making any paid calls.
        plan = {j['episode_id']: request_identity(j, prompt, cfg['model'], auth)[1] for j in jobs}
        plan_path = root / 'plan.json'
        if plan_path.exists() and read_json(plan_path) != plan:
            raise ValueError('Run selection or inputs changed; choose a new output_root')
        existing = {p.name for p in root.iterdir() if p.is_dir()}
        for job in jobs:
            if job['episode_id'] not in existing:
                continue
            path = root / job['episode_id'] / 'input.json'
            if path.exists() and read_json(path).get('signature') != plan[job['episode_id']]:
                raise ValueError('Existing request has different inputs: ' + job['episode_id'])
        save_json(plan_path, plan)
        public_cfg = dict(cfg, model={k: v for k, v in cfg['model'].items() if k != 'credentials_file'})
        save_json(root / 'config_snapshot.json', public_cfg)
        save_json(root / 'prompt_snapshot.json', {'prompt': prompt})
        results, failures = {}, {}
        progress = {'started_at': datetime.now(timezone.utc).isoformat(), 'total': len(jobs),
                    'completed': 0, 'cached': 0, 'failed': 0, 'normal': 0, 'anomaly': 0, 'uncertain': 0,
                    'episode_batchsize': cfg['episode_batchsize']}

        def report(done=False):
            progress.update(elapsed_sec=round(time.monotonic() - started, 3),
                            updated_at=datetime.now(timezone.utc).isoformat(), finished=done,
                            all_succeeded=done and not failures)
            save_json(root / 'progress.json', progress)
            save_json(root / 'failures.json', failures)
            save_json(root / 'anomalies.json', [results[k] for k in sorted(results) if results[k]['review_status'] == 'anomaly'])
            save_json(root / 'uncertain.json', [results[k] for k in sorted(results) if results[k]['review_status'] == 'uncertain'])
            with (root / 'run.log').open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(progress, ensure_ascii=False) + '\n')
            print(json.dumps(progress, ensure_ascii=False), flush=True)

        report()
        with ThreadPoolExecutor(max_workers=cfg['episode_batchsize']) as pool:
            futures = {pool.submit(review_one, root / j['episode_id'], j, prompt, cfg['model'], auth, transport): j['episode_id'] for j in jobs}
            for future in as_completed(futures):
                eid = futures[future]
                try:
                    result, cached = future.result()
                    results[eid] = result
                    progress['completed'] += 1
                    progress['cached'] += int(cached)
                    progress[result['review_status']] += 1
                except Exception as exc:
                    failures[eid] = str(exc).replace(auth['api_key'], '[REDACTED]')
                    progress['failed'] += 1
                report()
        report(done=True)
        return progress
    finally:
        lock.rmdir()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=str(Path(__file__).with_name('config.yaml')))
    parser.add_argument('--dry-run', action='store_true', help='Validate real inputs only; no API, no result output')
    args = parser.parse_args()
    cfg = load_config(args.config)
    if not args.dry_run and (not prompts.APPROVED or not prompts.PROMPT.strip()):
        parser.error('Prompt is pending joint review. Only --dry-run is available.')
    print(json.dumps({'phase': 'preflight', 'input_workers': cfg.get('input_workers', 1),
                      'started_at': datetime.now(timezone.utc).isoformat()}), flush=True)
    def report_preflight(count, total):
        if count % 100 == 0 or count == total:
            print(json.dumps({'phase': 'preflight', 'validated': count, 'total': total}), flush=True)
    jobs = prepare(cfg, on_progress=report_preflight)
    if args.dry_run:
        print(json.dumps({'episodes': len(jobs), 'episode_batchsize': cfg['episode_batchsize'],
                          'annotations_root': cfg['annotations_root'], 'api_calls': 0}, ensure_ascii=False))
        return
    auth = credentials(cfg['model'])
    print(json.dumps({'phase': 'predict', 'episodes': len(jobs), 'model': auth['model'],
                      'thinking': cfg['model']['thinking'], 'episode_batchsize': cfg['episode_batchsize']}), flush=True)
    result = run_jobs(jobs, cfg, auth, prompts.PROMPT)
    print(json.dumps(result, ensure_ascii=False))
    if result['failed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
