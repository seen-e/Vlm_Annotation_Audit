from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from ..client import complete, credentials
from ..data import prepare
from ..run import call_config, load_config, request_identity
from ..schema import validate as validate_detail
from ...episode_level_filter.data import read_json, save_json, signature
from . import prompts
from .schema import validate_screening
from .selection import compact_job, detail_job


def load_two_round_config(path):
    cfg = load_config(path)
    for round_name in ('round1', 'round2'):
        value = cfg.get(round_name)
        if not isinstance(value, dict) or type(value.get('max_images_per_episode')) is not int or \
                value['max_images_per_episode'] < 1:
            raise ValueError(round_name + '.max_images_per_episode must be a positive integer')
    if type(cfg['round2'].get('include_neighbor_boundaries')) is not bool:
        raise ValueError('round2.include_neighbor_boundaries must be boolean')
    return cfg


def request_one(folder, job, prompt, cfg, auth, validator, transport=complete):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
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
            validator(result, job['payload'])
            return result, True
        except (ValueError, TypeError, KeyError, IndexError):
            output_path.rename(folder / ('invalid_output_' + str(time.time_ns()) + '.json'))
    offset = max([int(path.name[8:]) for path in folder.glob('attempt_*')
                  if path.name[8:].isdigit()] or [0])
    feedback = ''
    for attempt in range(cfg['model']['attempts']):
        directory = folder / f'attempt_{offset + attempt + 1:03d}'
        directory.mkdir(parents=True)
        manifest = {
            'system_prompt': prompt,
            'payload': job['payload'],
            'visual_sequence': [
                {'label_before_image': {key: image.get(key) for key in
                                        ('frame_ref', 'frame_index', 'timestamp_sec', 'references')},
                 'sha256': image['sha256'], 'mime_type': image['mime_type']}
                for image in job['images']
            ],
        }
        if feedback:
            manifest['format_retry'] = feedback
        save_json(directory / 'messages_manifest.json', manifest)
        started = time.monotonic()
        try:
            raw, finish = transport(prompt, job['payload'], job['images'], call_config(cfg), auth)
            (directory / 'raw_response.txt').write_text(raw, encoding='utf-8')
            save_json(directory / 'meta.json', {'finish_reason': finish,
                                                 'elapsed_sec': time.monotonic() - started})
            if finish != 'stop':
                raise ValueError('Incomplete response: ' + str(finish))
            result = json.loads(raw)
            validator(result, job['payload'])
            save_json(output_path, result)
            return result, False
        except Exception as exc:
            feedback = str(exc).replace(auth['api_key'], '[REDACTED]')
            save_json(directory / 'error.json', {'error': feedback,
                                                  'elapsed_sec': time.monotonic() - started})
            if attempt + 1 < cfg['model']['attempts']:
                time.sleep(cfg['model']['retry_delay_sec'])
    raise RuntimeError(feedback)


def process_episode(root, job, cfg, auth, transport=complete):
    episode_root = Path(root) / job['episode_id']
    compact = compact_job(job, cfg['round1']['max_images_per_episode'])
    screening, cached1 = request_one(episode_root / 'round1', compact, prompts.ROUND1_PROMPT,
                                     cfg, auth, validate_screening, transport)
    detailed = screening['screening_status'] != 'pass'
    detail = None
    cached2 = False
    if detailed:
        expanded = detail_job(job, screening, cfg['round2']['max_images_per_episode'],
                              cfg['round2']['include_neighbor_boundaries'])
        detail, cached2 = request_one(episode_root / 'round2', expanded, prompts.ROUND2_PROMPT,
                                      cfg, auth, validate_detail, transport)
    final = {
        'episode_id': job['episode_id'],
        'audit_route': 'round2_completed' if detailed else 'round1_only',
        'final_status': detail['review_status'] if detail else 'screened_pass',
        'round1': screening,
        'round2': detail,
    }
    save_json(episode_root / 'final.json', final)
    return final, cached1, cached2, len(compact['images']), len(expanded['images']) if detailed else 0


def run_jobs(jobs, cfg, auth, transport=complete):
    root = Path(cfg['output_root'])
    root.mkdir(parents=True, exist_ok=True)
    lock = root / '.run.lock'
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError('Output is locked; verify no active writer before removing stale .run.lock') from None
    started = time.monotonic()
    failures, results = {}, {}
    progress = {
        'started_at': datetime.now(timezone.utc).isoformat(), 'total': len(jobs), 'completed': 0,
        'failed': 0, 'screened_pass': 0, 'round2_completed': 0, 'normal': 0,
        'annotation_anomaly': 0, 'task_not_met': 0, 'uncertain': 0,
        'round1_cached': 0, 'round2_cached': 0, 'round1_images': 0, 'round2_images': 0,
        'episode_batchsize': cfg['episode_batchsize'],
    }

    def report(finished=False):
        progress.update(elapsed_sec=round(time.monotonic() - started, 3),
                        updated_at=datetime.now(timezone.utc).isoformat(), finished=finished,
                        all_succeeded=finished and not failures)
        save_json(root / 'progress.json', progress)
        save_json(root / 'failures.json', failures)
        for status in ('screened_pass', 'normal', 'annotation_anomaly', 'task_not_met', 'uncertain'):
            save_json(root / (status + '.json'), [results[key] for key in sorted(results)
                                                  if results[key]['final_status'] == status])
        print(json.dumps(progress, ensure_ascii=False), flush=True)

    try:
        plan = {
            'episodes': [job['episode_id'] for job in jobs],
            'input_signatures': {job['episode_id']: signature({
                'payload': job['payload'],
                'images': [{key: image.get(key) for key in
                            ('frame_ref', 'frame_index', 'timestamp_sec', 'references', 'sha256')}
                           for image in job['images']],
            }) for job in jobs},
            'round1_prompt': signature(prompts.ROUND1_PROMPT),
            'round2_prompt': signature(prompts.ROUND2_PROMPT),
            'selection': {'round1': cfg['round1'], 'round2': cfg['round2']},
        }
        plan_path = root / 'plan.json'
        if plan_path.exists() and read_json(plan_path) != plan:
            raise ValueError('Run selection or inputs changed; choose a new output_root')
        save_json(plan_path, plan)
        public_model = {key: value for key, value in cfg['model'].items() if key != 'credentials_file'}
        save_json(root / 'config_snapshot.json', {**cfg, 'model': public_model})
        save_json(root / 'prompt_snapshot.json', {'round1': prompts.ROUND1_PROMPT,
                                                   'round2': prompts.ROUND2_PROMPT})
        report()
        with ThreadPoolExecutor(max_workers=cfg['episode_batchsize']) as pool:
            pending = {pool.submit(process_episode, root, job, cfg, auth, transport): job['episode_id']
                       for job in jobs}
            for future in as_completed(pending):
                episode_id = pending[future]
                try:
                    result, cached1, cached2, images1, images2 = future.result()
                    results[episode_id] = result
                    progress['completed'] += 1
                    progress['round1_cached'] += int(cached1)
                    progress['round2_cached'] += int(cached2)
                    progress['round1_images'] += images1
                    progress['round2_images'] += images2
                    status = result['final_status']
                    progress[status] += 1
                    if result['audit_route'] == 'round2_completed':
                        progress['round2_completed'] += 1
                except Exception as exc:
                    failures[episode_id] = str(exc).replace(auth['api_key'], '[REDACTED]')
                    progress['failed'] += 1
                report()
        report(True)
        return progress
    finally:
        lock.rmdir()


def main():
    parser = argparse.ArgumentParser(description='Run compact screening followed by targeted visual audit.')
    parser.add_argument('--config', default=str(Path(__file__).with_name('config.yaml')))
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--pilot', action='store_true',
                        help='Allow draft prompts for a bounded explicit episode list')
    args = parser.parse_args()
    cfg = load_two_round_config(args.config)
    if not args.dry_run and (not prompts.APPROVED or not prompts.ROUND1_PROMPT or not prompts.ROUND2_PROMPT):
        selected = cfg.get('episode_ids') or []
        if not args.pilot or not selected or len(selected) > cfg['pilot_max_episodes']:
            parser.error('Draft prompts require --pilot and 1..pilot_max_episodes explicit episode_ids.')
    print(json.dumps({'phase': 'preflight', 'input_workers': cfg['input_workers'],
                      'started_at': datetime.now(timezone.utc).isoformat()}), flush=True)

    def preflight(count, total):
        if count % 100 == 0 or count == total:
            print(json.dumps({'phase': 'preflight', 'validated': count, 'total': total}), flush=True)

    jobs = prepare(cfg, preflight)
    compact = [compact_job(job, cfg['round1']['max_images_per_episode']) for job in jobs]
    if args.dry_run:
        compact_counts = [len(job['images']) for job in compact]
        full_counts = [len(job['images']) for job in jobs]
        print(json.dumps({'episodes': len(jobs), 'round1_images': sum(compact_counts),
                          'round1_min': min(compact_counts), 'round1_max': max(compact_counts),
                          'full_evidence_images': sum(full_counts), 'full_min': min(full_counts),
                          'full_max': max(full_counts), 'api_calls': 0}, ensure_ascii=False))
        return
    auth = credentials(cfg['model'])
    result = run_jobs(jobs, cfg, auth)
    print(json.dumps(result, ensure_ascii=False))
    if result['failed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
