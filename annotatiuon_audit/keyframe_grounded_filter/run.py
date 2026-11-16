import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import yaml

from .client import complete, credentials
from .data import prepare
from .schema import validate
from . import prompts
from ..episode_level_filter.data import read_json, save_json, signature


MODEL_PARAMETERS = ('thinking', 'temperature', 'max_tokens', 'stream')


def load_config(path):
    path = Path(path).resolve()
    cfg = yaml.safe_load(path.read_text(encoding='utf-8'))
    for name in ('standards_path', 'annotations_root', 'output_root'):
        cfg[name] = str((path.parent / cfg[name]).resolve())
    cfg['keyframe_roots'] = [str((path.parent / value).resolve()) for value in cfg['keyframe_roots']]
    if cfg['model'].get('credentials_file'):
        cfg['model']['credentials_file'] = str((path.parent / cfg['model']['credentials_file']).resolve())
    for key in ('episode_batchsize', 'input_workers', 'max_images_per_episode', 'max_image_bytes',
                'max_text_chars', 'pilot_max_episodes'):
        if type(cfg[key]) is not int or cfg[key] < 1:
            raise ValueError(key + ' must be a positive integer')
    for key in ('attempts', 'max_tokens'):
        if type(cfg['model'][key]) is not int or cfg['model'][key] < 1:
            raise ValueError('model.' + key + ' must be a positive integer')
    if not isinstance(cfg['keyframe_roots'], list) or not cfg['keyframe_roots']:
        raise ValueError('keyframe_roots must be a nonempty list')
    if cfg['image_detail'] not in ('auto', 'low', 'high'):
        raise ValueError('image_detail must be auto, low or high')
    if cfg['model']['thinking'] is not None and type(cfg['model']['thinking']) is not bool:
        raise ValueError('thinking must be true, false or null')
    if cfg['model']['timeout_sec'] <= 0 or cfg['model']['retry_delay_sec'] < 0:
        raise ValueError('Invalid timeout/retry delay')
    output = Path(cfg['output_root'])
    sources = [Path(cfg['annotations_root']), Path(cfg['standards_path']), *map(Path, cfg['keyframe_roots'])]
    if any(output == source or output.is_relative_to(source) or source.is_relative_to(output) for source in sources):
        raise ValueError('Output must be separate from every input source')
    return cfg


def call_config(cfg):
    return {**cfg['model'], 'image_detail': cfg['image_detail']}


def request_identity(job, prompt, cfg, auth):
    images = [{key: image[key] for key in ('frame_ref', 'frame_index', 'mime_type', 'size', 'sha256')}
              for image in job['images']]
    model_cfg = call_config(cfg)
    identity = {'prompt': prompt, 'payload': job['payload'], 'images': images,
                'model': auth['model'], 'base_url': auth['base_url'],
                'parameters': {key: model_cfg[key] for key in (*MODEL_PARAMETERS, 'image_detail')},
                'contract_version': 1}
    return identity, signature(identity)


def review_one(folder, job, prompt, cfg, auth, transport=complete):
    folder = Path(folder)
    identity, digest = request_identity(job, prompt, cfg, auth)
    input_path, output_path = folder / 'input.json', folder / 'output.json'
    if input_path.exists() and read_json(input_path)['signature'] != digest:
        raise ValueError('Inputs changed; choose a new output_root')
    if output_path.exists() and not input_path.exists():
        raise ValueError('Orphan output without provenance')
    save_json(input_path, {'signature': digest, 'sources': job['sources'], **identity})
    if output_path.exists():
        try:
            result = read_json(output_path)
            validate(result, job['payload'])
            return result, True
        except (ValueError, TypeError, KeyError, IndexError):
            output_path.rename(folder / ('invalid_output_' + str(time.time_ns()) + '.json'))
    offset = max([int(path.name[8:]) for path in folder.glob('attempt_*') if path.name[8:].isdigit()] or [0])
    feedback = ''
    for attempt in range(cfg['model']['attempts']):
        directory = folder / f'attempt_{offset + attempt + 1:03d}'
        directory.mkdir(parents=True)
        manifest = {'system_prompt': prompt, 'payload': job['payload'],
                    'visual_sequence': [{'label_before_image': {'frame_ref': image['frame_ref'],
                                                                'frame_index': image['frame_index']},
                                         'sha256': image['sha256'], 'mime_type': image['mime_type']}
                                        for image in job['images']]}
        if feedback:
            manifest['format_retry'] = feedback
        save_json(directory / 'messages_manifest.json', manifest)
        started = time.monotonic()
        try:
            raw, finish = transport(prompt, job['payload'], job['images'], call_config(cfg), auth)
            (directory / 'raw_response.txt').write_text(raw, encoding='utf-8')
            save_json(directory / 'meta.json', {'finish_reason': finish, 'elapsed_sec': time.monotonic() - started})
            if finish != 'stop':
                raise ValueError('Incomplete response: ' + str(finish))
            result = json.loads(raw)
            validate(result, job['payload'])
            save_json(output_path, result)
            return result, False
        except Exception as exc:
            feedback = str(exc).replace(auth['api_key'], '[REDACTED]')
            save_json(directory / 'error.json', {'error': feedback, 'elapsed_sec': time.monotonic() - started})
            if attempt + 1 < cfg['model']['attempts']:
                time.sleep(cfg['model']['retry_delay_sec'])
    raise RuntimeError(feedback)


def run_jobs(jobs, cfg, auth, prompt, transport=complete):
    root = Path(cfg['output_root'])
    root.mkdir(parents=True, exist_ok=True)
    lock = root / '.run.lock'
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError('Output is locked; verify no active writer before removing stale .run.lock') from None
    started = time.monotonic()
    try:
        plan = {job['episode_id']: request_identity(job, prompt, cfg, auth)[1] for job in jobs}
        plan_path = root / 'plan.json'
        if plan_path.exists() and read_json(plan_path) != plan:
            raise ValueError('Run selection or inputs changed; choose a new output_root')
        save_json(plan_path, plan)
        public_model = {key: value for key, value in cfg['model'].items() if key != 'credentials_file'}
        save_json(root / 'config_snapshot.json', {**cfg, 'model': public_model})
        save_json(root / 'prompt_snapshot.json', {'prompt': prompt})
        results, failures = {}, {}
        progress = {'started_at': datetime.now(timezone.utc).isoformat(), 'total': len(jobs), 'completed': 0,
                    'cached': 0, 'failed': 0, 'normal': 0, 'annotation_anomaly': 0,
                    'task_not_met': 0, 'uncertain': 0, 'episode_batchsize': cfg['episode_batchsize']}

        def report(finished=False):
            progress.update(elapsed_sec=round(time.monotonic() - started, 3),
                            updated_at=datetime.now(timezone.utc).isoformat(), finished=finished,
                            all_succeeded=finished and not failures)
            save_json(root / 'progress.json', progress)
            save_json(root / 'failures.json', failures)
            for status in ('annotation_anomaly', 'task_not_met', 'uncertain'):
                save_json(root / (status + '.json'), [results[key] for key in sorted(results)
                                                       if results[key]['review_status'] == status])
            print(json.dumps(progress, ensure_ascii=False), flush=True)

        report()
        with ThreadPoolExecutor(max_workers=cfg['episode_batchsize']) as pool:
            pending = {pool.submit(review_one, root / job['episode_id'], job, prompt, cfg, auth, transport):
                       job['episode_id'] for job in jobs}
            for future in as_completed(pending):
                episode_id = pending[future]
                try:
                    result, cached = future.result()
                    results[episode_id] = result
                    progress['completed'] += 1
                    progress['cached'] += int(cached)
                    progress[result['review_status']] += 1
                except Exception as exc:
                    failures[episode_id] = str(exc).replace(auth['api_key'], '[REDACTED]')
                    progress['failed'] += 1
                report()
        report(True)
        return progress
    finally:
        lock.rmdir()


def main():
    parser = argparse.ArgumentParser(description='Audit final annotations against Stage4 keyframe images.')
    parser.add_argument('--config', default=str(Path(__file__).with_name('config.yaml')))
    parser.add_argument('--dry-run', action='store_true', help='Validate text, keyframes and image coverage without API calls')
    parser.add_argument('--pilot', action='store_true', help='Allow the draft prompt only for a bounded explicit episode list')
    args = parser.parse_args()
    cfg = load_config(args.config)
    if not args.dry_run and (not prompts.APPROVED or not prompts.PROMPT.strip()):
        selected = cfg.get('episode_ids') or []
        if not args.pilot or not prompts.PROMPT.strip() or not selected or len(selected) > cfg['pilot_max_episodes']:
            parser.error('Draft prompt requires --pilot and 1..pilot_max_episodes explicit episode_ids.')
    print(json.dumps({'phase': 'preflight', 'input_workers': cfg['input_workers'],
                      'started_at': datetime.now(timezone.utc).isoformat()}), flush=True)

    def report_preflight(count, total):
        if count % 100 == 0 or count == total:
            print(json.dumps({'phase': 'preflight', 'validated': count, 'total': total}), flush=True)

    jobs = prepare(cfg, report_preflight)
    if args.dry_run:
        counts = [len(job['images']) for job in jobs]
        print(json.dumps({'episodes': len(jobs), 'images': sum(counts), 'min_images': min(counts),
                          'max_images': max(counts), 'api_calls': 0}, ensure_ascii=False))
        return
    auth = credentials(cfg['model'])
    result = run_jobs(jobs, cfg, auth, prompts.PROMPT)
    print(json.dumps(result, ensure_ascii=False))
    if result['failed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
