import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from ..image_base_clustering.api import credentials, request, save, signature
from .data import load_config, prepare, validate_config
from .round1_prompt import PROMPT as ROUND1
from .round2_prompt import PROMPT as ROUND2
from .schema import validate_round1, validate_round2


def batches(job, cfg):
    size = cfg['episodes_per_batch']
    for index, start in enumerate(range(0, len(job['episodes']), size)):
        yield index, {'round': 1, 'task_id': job['task_id'], 'task': job['task'],
                      'parent_scene_group_id': job['parent_scene_group_id'],
                      'representatives_per_cluster': cfg['representatives_per_cluster'],
                      'episodes': job['episodes'][start:start + size]}


def check_size(payload, cfg):
    count = len(json.dumps(payload, ensure_ascii=False))
    if count > cfg['max_request_chars']:
        raise ValueError(f'Round {payload["round"]} payload has {count} chars, cap={cfg["max_request_chars"]}; no truncation or extra round')
    return count


def select_representatives(candidates, refs, limit):
    chosen = []
    for row in zip_longest_representatives(candidates, refs):
        for eid in row:
            if eid not in chosen:
                chosen.append(eid)
                if len(chosen) == limit:
                    return chosen
    return chosen


def zip_longest_representatives(candidates, refs):
    # Round-robin preserves coverage of merged candidate groups before extra samples.
    lists = [candidates[gid]['representative_episode_ids'] for gid in refs]
    for index in range(max(map(len, lists))):
        yield [values[index] for values in lists if index < len(values)]


def run_group(job, cfg, root, auth, caller=request):
    parent = job['parent_scene_group_id']
    folder = Path(root) / f'task_{job["task_id"]:03d}' / 'scene_groups' / parent
    reps = cfg['representatives_per_cluster']
    base = {key: job[key] for key in ('task_id', 'task', 'parent_scene_group_id', 'parent_scene_status', 'parent_scene_description')}
    groups = []
    if job['skip']:
        groups = [{'cluster_id': parent, 'parent_scene_group_id': parent,
                   'status': 'skipped_small_group', 'text_status': 'not_evaluated',
                   'member_episode_ids': sorted(job['episode_ids']), 'common_result': None,
                   'reason': 'Group size is below text_clustering_min_group_size',
                   'representative_episode_ids': sorted(job['episode_ids']),
                   'representative_selection': 'all_members_small_group', 'source_group_ids': []}]
        candidates = []
    else:
        candidates, lookup = [], {e['episode_id']: e for e in job['episodes']}
        # Reuse the proven API/cache layer with zero images and a text-only payload.
        call_cfg = {'api': cfg['api'], 'max_images_per_request': 0, 'max_image_edge': 640, 'jpeg_quality': 90}
        for index, payload in batches(job, cfg):
            check_size(payload, cfg)
            ids = [e['episode_id'] for e in payload['episodes']]
            validator = lambda obj, ids=ids: validate_round1(obj, ids, reps)
            result = caller(folder / 'round1' / f'batch_{index:03d}', ROUND1, payload, [], call_cfg, auth, validator)
            validator(result)
            for i, group in enumerate(result['groups']):
                candidates.append(dict(group, group_id=f'{parent}_b{index:03d}_g{i:03d}'))
        save(folder / 'round1_candidates.json', candidates)
        by_id = {g['group_id']: g for g in candidates}
        descriptions = []
        for group in candidates:
            descriptions.append({key: group[key] for key in ('group_id', 'status', 'common_result', 'distinguishing_features')})
            descriptions[-1].update(member_count=len(group['member_episode_ids']),
                                    representative_episodes=[lookup[eid] for eid in group['representative_episode_ids']])
        payload = {'round': 2, 'task_id': job['task_id'], 'task': job['task'],
                   'parent_scene_group_id': parent, 'candidate_groups': descriptions}
        check_size(payload, cfg)
        result = caller(folder / 'round2', ROUND2, payload, [], call_cfg, auth,
                        lambda obj: validate_round2(obj, by_id))
        validate_round2(result, by_id)
        for index, group in enumerate(result['groups']):
            refs = group['source_group_ids']
            ids = [eid for gid in refs for eid in by_id[gid]['member_episode_ids']]
            groups.append(dict(group, cluster_id=f'{parent}_txt{index:03d}', parent_scene_group_id=parent,
                               member_episode_ids=ids,
                               representative_episode_ids=select_representatives(by_id, refs, min(reps, len(ids))),
                               representative_selection='round_robin_round1_representatives', text_status='clustered'))
    ids = [eid for group in groups for eid in group['member_episode_ids']]
    if len(ids) != len(set(ids)) or set(ids) != set(job['episode_ids']):
        raise ValueError('Final parent-group coverage mismatch')
    output = dict(base, status='skipped_small_group' if job['skip'] else 'complete',
                  group_size=len(ids), threshold=cfg['text_clustering_min_group_size'],
                  round1_group_count=len(candidates), round2_requests=0 if job['skip'] else 1,
                  groups=groups, audit_status='not_reviewed')
    save(folder / 'final.json', output)
    return output


def preflight_plan(jobs, cfg):
    rows = []
    for job in jobs:
        sizes = [check_size(payload, cfg) for _, payload in batches(job, cfg)] if not job['skip'] else []
        rows.append({key: job[key] for key in ('task_id', 'parent_scene_group_id', 'parent_scene_status',
                                              'episode_ids', 'image_final_sha256', 'annotation_sources', 'skip')})
        rows[-1].update(round1_requests=len(sizes), round2_requests=0 if job['skip'] else 1,
                        round1_payload_chars=sizes)
    return rows


def run_jobs(jobs, cfg, auth, caller=request):
    validate_config(cfg)
    plan = preflight_plan(jobs, cfg)
    public = dict(cfg, api={k: cfg['api'][k] for k in ('thinking', 'temperature', 'max_tokens', 'stream', 'attempts', 'timeout_sec', 'retry_delay_sec')})
    identity = {'config': public, 'plan': plan, 'prompts': [ROUND1, ROUND2],
                'model': auth.get('model'), 'base_url': auth.get('base_url'), 'contract_version': 1}
    digest = signature(identity)
    root = Path(cfg['output_root'])
    root.mkdir(parents=True, exist_ok=True)
    lock = root / '.run.lock'
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError('Output locked; verify no active writer before removing stale .run.lock') from None
    try:
        if (root / 'identity.json').exists():
            old = json.loads((root / 'identity.json').read_text())
            if old['signature'] != digest:
                raise ValueError('Source groups, texts, prompt or configuration changed; choose a new output_root')
        elif any(p.name != '.run.lock' for p in root.iterdir()):
            raise ValueError('Nonempty output has no provenance; choose a new output_root')
        save(root / 'identity.json', {'signature': digest, **identity})
        save(root / 'plan.json', plan)
        save(root / 'config_snapshot.json', public)
        save(root / 'prompt_snapshot.json', {'round1': ROUND1, 'round2': ROUND2})
        started = time.monotonic()
        results, failures = [], []

        def report(finished=False):
            progress = {'total_parent_groups': len(jobs), 'completed_parent_groups': len(results),
                        'failed_parent_groups': len(failures),
                        'skipped_small_groups': sum(r['status'] == 'skipped_small_group' for r in results),
                        'completed_episodes': sum(r['group_size'] for r in results),
                        'expected_episodes': sum(len(j['episode_ids']) for j in jobs),
                        'elapsed_sec': round(time.monotonic() - started, 3), 'finished': finished,
                        'updated_at': datetime.now(timezone.utc).isoformat()}
            save(root / 'progress.json', progress)
            save(root / 'failures.json', failures)
            print(json.dumps(progress), flush=True)

        report()
        with ThreadPoolExecutor(max_workers=cfg['group_workers']) as pool:
            pending = {pool.submit(run_group, job, cfg, root, auth, caller): job for job in jobs}
            for future in as_completed(pending):
                job = pending[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    error = str(exc)
                    if auth.get('api_key'):
                        error = error.replace(auth['api_key'], '[REDACTED]')
                    failures.append({'task_id': job['task_id'], 'parent_scene_group_id': job['parent_scene_group_id'], 'error': error})
                report()
        results.sort(key=lambda r: (r['task_id'], r['parent_scene_group_id']))
        assignments, samples = [], []
        for result in results:
            for group in result['groups']:
                for eid in group['member_episode_ids']:
                    assignments.append({'episode_id': eid, 'task_id': result['task_id'],
                                        'parent_scene_group_id': result['parent_scene_group_id'],
                                        'cluster_id': group['cluster_id'], 'status': group['status'],
                                        'audit_status': 'not_reviewed'})
                samples.append({'task_id': result['task_id'], 'parent_scene_group_id': result['parent_scene_group_id'],
                                'cluster_id': group['cluster_id'], 'episode_ids': group['representative_episode_ids'],
                                'selection': group['representative_selection'], 'audit_status': 'not_reviewed'})
        if len(assignments) != len({a['episode_id'] for a in assignments}):
            raise ValueError('Duplicate episode across final groups')
        summary = {'status': 'complete_with_failures' if failures else 'complete',
                   'parent_groups': len(jobs), 'completed_parent_groups': len(results),
                   'failed_parent_groups': len(failures), 'groups': sum(len(r['groups']) for r in results),
                   'episodes': len(assignments), 'expected_episodes': sum(len(j['episode_ids']) for j in jobs),
                   'skipped_small_groups': sum(r['status'] == 'skipped_small_group' for r in results)}
        if not failures and summary['episodes'] != summary['expected_episodes']:
            raise ValueError('Missing episodes in complete run')
        save(root / 'final.json', {'summary': summary, 'scene_groups': results})
        save(root / 'assignments.json', assignments)
        save(root / 'representative_samples.json', samples)
        save(root / 'summary.json', summary)
        report(True)
        return summary
    finally:
        lock.rmdir()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=str(Path(__file__).with_name('config.yaml')))
    parser.add_argument('--image-results-root')
    parser.add_argument('--output-root')
    parser.add_argument('--dry-run', action='store_true', help='Read/validate all inputs and report plan; no API or result writes')
    args = parser.parse_args()
    cfg = load_config(args.config)
    for key in ('image_results_root', 'output_root'):
        if getattr(args, key):
            cfg[key] = str(Path(getattr(args, key)).resolve())
    jobs = prepare(cfg)
    plan = preflight_plan(jobs, cfg)
    if args.dry_run:
        print(json.dumps({'tasks': len({j['task_id'] for j in jobs}), 'parent_scene_groups': len(jobs),
                          'episodes': sum(len(j['episode_ids']) for j in jobs),
                          'threshold': cfg['text_clustering_min_group_size'],
                          'skipped_small_groups': sum(j['skip'] for j in jobs),
                          'round1_requests': sum(p['round1_requests'] for p in plan),
                          'round2_requests': sum(p['round2_requests'] for p in plan), 'api_calls': 0}))
        return
    auth = credentials(cfg['api']) if any(not j['skip'] for j in jobs) else {}
    summary = run_jobs(jobs, cfg, auth)
    if summary['failed_parent_groups']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
