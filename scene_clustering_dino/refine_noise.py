"""Cluster only DINO noise with VLM, then display both sets side by side."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import json
from pathlib import Path
import shutil
import time

import yaml

from scene_clustering_vlm.api import credentials, save, signature
from scene_clustering_vlm.round1_prompt import PROMPT as ROUND1
from scene_clustering_vlm.round2_prompt import PROMPT as ROUND2
from scene_clustering_vlm.run import load_tasks, run_task, validate_config


def read_dino(root):
    tasks = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(root.glob('task_*/clusters.json'))]
    if not tasks:
        raise ValueError('No DINO clusters.json found')
    seen = set()
    task_ids = set()
    for t in tasks:
        if t['task_id'] in task_ids:
            raise ValueError('Duplicate DINO task')
        task_ids.add(t['task_id'])
        ids = [e['episode_id'] for e in t['episodes']]
        if len(ids) != len(set(ids)) or seen.intersection(ids):
            raise ValueError('Duplicate DINO episode')
        seen.update(ids)
        if len(ids) != t['episode_count']:
            raise ValueError('Invalid DINO episode count')
        labels = {c['cluster_id']: set(c['episode_ids']) for c in t['clusters']}
        if len(labels) != len(t['clusters']):
            raise ValueError('Duplicate DINO cluster')
        if any(label < 0 for label in labels):
            raise ValueError('Invalid DINO cluster label')
        for label, members in labels.items():
            if members != {e['episode_id'] for e in t['episodes'] if e['cluster_id'] == label}:
                raise ValueError('Inconsistent DINO memberships')
        if any(e['cluster_id'] != -1 and e['cluster_id'] not in labels for e in t['episodes']):
            raise ValueError('Unknown DINO cluster')
    return tasks


def select_noise(tasks, cache_tasks):
    selected = {}
    for t in tasks:
        wanted = {e['episode_id'] for e in t['episodes'] if e['cluster_id'] == -1}
        if not wanted:
            continue
        items = [r for r in cache_tasks.get(t['task_id'], []) if r['episode_id'] in wanted]
        if len(items) != len(wanted) or {r['episode_id'] for r in items} != wanted:
            raise ValueError('DINO noise missing from cached frames')
        if any(r['task'] != t['task'] for r in items):
            raise ValueError('DINO/cache task mismatch')
        selected[t['task_id']] = items
    return selected


def combine_task(dino, vlm=None, candidates=()):
    """Preserve original labels in explicit fields; never merge across sources."""
    t = deepcopy(dino)
    t['dino_cluster_count'] = len(dino['clusters'])
    t['original_noise_count'] = sum(e['cluster_id'] == -1 for e in dino['episodes'])
    t['vlm_cluster_count'] = 0
    t['vlm_uncertain_count'] = 0
    t['hybrid'] = True
    tid = t['task_id']
    for c in t['clusters']:
        c.update(dino_cluster_id=c['cluster_id'], source='dino', status='candidate')
        c['cluster_id'] = f'dino:t{tid:03d}:c{c["dino_cluster_id"]}'
    records = {e['episode_id']: e for e in t['episodes']}
    for e in records.values():
        e['dino_cluster_id'] = e['cluster_id']
        e['source'] = 'dino' if e['cluster_id'] != -1 else 'unresolved'
        if e['cluster_id'] != -1:
            e['cluster_id'] = f'dino:t{tid:03d}:c{e["dino_cluster_id"]}'
    if vlm is not None:
        if vlm['task_id'] != tid or vlm['task'] != dino['task']:
            raise ValueError('VLM task mismatch')
        members = [eid for g in vlm['groups'] for eid in g['member_episode_ids']]
        noise = {eid for eid, e in records.items() if e['dino_cluster_id'] == -1}
        if len(members) != len(set(members)) or set(members) != noise:
            raise ValueError('VLM must cover exactly DINO noise, without changing assigned members')
        representatives = {eid for c in candidates for eid in c['representative_episode_ids']}
        for g in vlm['groups']:
            cid = 'vlm:' + g['cluster_id']
            t['clusters'].append(dict(cluster_id=cid, source='vlm', status=g['status'],
                                      size=len(g['member_episode_ids']), episode_ids=g['member_episode_ids'],
                                      common_features=g['common_features'], reason=g['reason'],
                                      source_group_ids=g['source_group_ids']))
            for eid in g['member_episode_ids']:
                records[eid].update(cluster_id=cid, source='vlm', vlm_status=g['status'],
                                    common_features=g['common_features'], reason=g['reason'],
                                    center=eid in representatives)
                t['vlm_uncertain_count'] += int(g['status'] == 'uncertain')
        t['vlm_cluster_count'] = len(vlm['groups'])
    t['cluster_count'] = len(t['clusters'])
    t['noise_count'] = sum(e['cluster_id'] == -1 for e in records.values())
    t['noise_ratio'] = t['noise_count'] / max(1, t['episode_count'])
    return t


def publish(root, dino_root, tasks, results, errors, cfg, started):
    combined = []
    for t in tasks:
        tid = t['task_id']
        candidate_path = root / 'vlm' / f'task_{tid:03d}' / 'round1_candidates.json'
        candidates = json.loads(candidate_path.read_text()) if tid in results else []
        merged = combine_task(t, results.get(tid), candidates)
        combined.append(merged)
        save(root / f'task_{tid:03d}' / 'combined.json', merged)
    viewer = root / 'viewer'
    (viewer / 'images').mkdir(parents=True, exist_ok=True)
    for t in combined:
        for e in t['episodes']:
            relative = Path(e['image'])
            if relative.parts != ('images', e['episode_id'] + '.jpg'):
                raise ValueError('Unexpected DINO preview image path')
            destination = viewer / relative
            source = dino_root / 'viewer' / relative
            if not source.is_file():
                raise ValueError('Missing DINO preview image: ' + str(source))
            shutil.copyfile(source, destination)
    dino_cfg = json.loads((dino_root / 'run_config.json').read_text())
    summary = dict(hybrid=True, feature_mode=dino_cfg['feature_mode'], vlm_frame_mode=cfg['frame_mode'],
                   status='complete_with_failures' if errors else 'complete', tasks=len(tasks),
                   episodes=sum(t['episode_count'] for t in combined),
                   clusters=sum(t['cluster_count'] for t in combined),
                   dino_clusters=sum(t['dino_cluster_count'] for t in combined),
                   vlm_clusters=sum(t['vlm_cluster_count'] for t in combined),
                   original_noise=sum(t['original_noise_count'] for t in combined),
                   vlm_uncertain=sum(t['vlm_uncertain_count'] for t in combined),
                   noise=sum(t['noise_count'] for t in combined),
                   failed_tasks=len(errors), elapsed_sec=time.time()-started)
    save(root / 'summary.json', summary)
    save(root / 'failures.json', errors)
    (viewer / 'data.js').write_text('window.DATA=' + json.dumps(combined, ensure_ascii=False) + ';\nwindow.SUMMARY=' + json.dumps(summary) + ';', encoding='utf-8')
    shutil.copyfile(Path(__file__).with_name('viewer.html'), viewer / 'index.html')
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dino-result', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--vlm-config', type=Path, default=Path(__file__).parents[1] / 'scene_clustering_vlm/config.yaml')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    started = time.time()
    source, root = args.dino_result.resolve(), args.output.resolve()
    if root == source or source in root.parents or root in source.parents:
        raise ValueError('Use a separate output directory, not the DINO source or its parent/child')
    config_path = args.vlm_config.resolve()
    cfg = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    for key in ('cache_root', 'dataset_root'):
        cfg[key] = str((config_path.parent / cfg[key]).resolve())
    if cfg['api'].get('credentials_file'):
        cfg['api']['credentials_file'] = str((config_path.parent / cfg['api']['credentials_file']).resolve())
    validate_config(cfg)
    tasks = read_dino(source)
    selected_tasks = set(cfg.get('task_ids', []))
    if selected_tasks:
        if selected_tasks - {t['task_id'] for t in tasks}:
            raise ValueError('Configured task is absent from DINO results')
        tasks = [t for t in tasks if t['task_id'] in selected_tasks]
    cfg['task_ids'] = [t['task_id'] for t in tasks if any(e['cluster_id'] == -1 for e in t['episodes'])]
    selected = select_noise(tasks, load_tasks(cfg) if cfg['task_ids'] else {})
    plan = dict(dino_result=str(source), frame_mode=cfg['frame_mode'], episodes_per_batch=cfg['episodes_per_batch'],
                noise_episodes=sum(map(len, selected.values())),
                tasks=[dict(task_id=tid, episode_ids=[r['episode_id'] for r in items],
                            round1_batches=(len(items)+cfg['episodes_per_batch']-1)//cfg['episodes_per_batch'],
                            round2_requests=1) for tid, items in selected.items()])
    if args.dry_run:
        save(root / 'dry_run_plan.json', plan)
        print(json.dumps(dict(api_called=False, noise_episodes=plan['noise_episodes'], tasks=len(selected))))
        return
    auth = credentials(cfg['api']) if selected else None
    public_cfg = dict(cfg, api={k:v for k,v in cfg['api'].items() if k != 'credentials_file'})
    identity = signature(dict(dino_tasks=tasks, inputs=selected, config=public_cfg,
                              api={k:auth[k] for k in ('model', 'base_url')} if auth else {}, prompts=[ROUND1, ROUND2]))
    identity_file = root / 'identity.json'
    if identity_file.exists() and json.loads(identity_file.read_text())['signature'] != identity:
        raise ValueError('Inputs changed; use a new output directory')
    if root.exists() and not identity_file.exists() and any(p.name != 'dry_run_plan.json' for p in root.iterdir()):
        raise ValueError('Output directory already contains another experiment')
    save(identity_file, dict(signature=identity))
    save(root / 'plan.json', plan)
    save(root / 'config_snapshot.json', public_cfg)
    save(root / 'prompt_snapshot.json', dict(round1=ROUND1, round2=ROUND2))
    results, errors = {}, []
    with ThreadPoolExecutor(max_workers=cfg['task_workers']) as pool:
        futures = {pool.submit(run_task, tid, items, cfg, root / 'vlm', auth):tid for tid, items in selected.items()}
        for future in as_completed(futures):
            tid = futures[future]
            try:
                results[tid] = future.result()
            except Exception as exc:
                errors.append(dict(task_id=tid, error=str(exc).replace(auth['api_key'], '[REDACTED]')))
            save(root / 'progress.json', dict(total_tasks=len(selected), completed_tasks=len(results),
                                               failed_tasks=len(errors), elapsed_sec=time.time()-started))
    print(json.dumps(publish(root, source, tasks, results, errors, cfg, started)))
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
