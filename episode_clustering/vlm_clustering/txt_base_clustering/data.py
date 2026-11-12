from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import random
import re

import yaml


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('Invalid ID: ' + str(value))
    return value


def load_config(path):
    path = Path(path).resolve()
    cfg = yaml.safe_load(path.read_text(encoding='utf-8'))
    for key in ('image_results_root', 'annotations_root', 'output_root'):
        cfg[key] = str((path.parent / cfg[key]).resolve())
    if cfg['api'].get('credentials_file'):
        cfg['api']['credentials_file'] = str((path.parent / cfg['api']['credentials_file']).resolve())
    validate_config(cfg)
    return cfg


def validate_config(cfg):
    for key in ('text_clustering_min_group_size', 'episodes_per_batch', 'representatives_per_cluster',
                'group_workers', 'input_workers', 'max_request_chars'):
        if type(cfg[key]) is not int or cfg[key] < 1:
            raise ValueError(key + ' must be a positive integer')
    if type(cfg['shuffle_seed']) is not int:
        raise ValueError('shuffle_seed must be an integer')
    selected = cfg['task_ids']
    if not isinstance(selected, list) or any(type(x) is not int or x < 0 for x in selected) or len(set(selected)) != len(selected):
        raise ValueError('task_ids must be unique nonnegative integers')
    api = cfg['api']
    for key in ('attempts', 'max_tokens'):
        if type(api[key]) is not int or api[key] < 1:
            raise ValueError('Invalid API ' + key)
    if api['thinking'] is not None and type(api['thinking']) is not bool:
        raise ValueError('thinking must be true, false or null')
    if api['timeout_sec'] <= 0 or api['retry_delay_sec'] < 0 or type(api['stream']) is not bool:
        raise ValueError('Invalid timeout, retry delay or stream')
    out = Path(cfg['output_root']).resolve()
    for key in ('image_results_root', 'annotations_root'):
        src = Path(cfg[key]).resolve()
        if src == out or src.is_relative_to(out) or out.is_relative_to(src):
            raise ValueError('Text output must be separate from both input roots')


def annotation_text(path, eid):
    raw = path.read_bytes()
    value = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(value, dict) or value.get('episode_id', eid) != eid:
        raise ValueError('Annotation episode mismatch: ' + eid)
    subtasks = value.get('subtasks')
    if not isinstance(subtasks, list) or not subtasks:
        raise ValueError('Missing subtasks: ' + eid)
    records = []
    for index, subtask in enumerate(subtasks):
        if not isinstance(subtask, dict):
            raise ValueError('Invalid subtask: ' + eid)
        record = {'subtask_index': subtask.get('subtask_index', index)}
        for key in ('segment_summary', 'uncertainty'):
            if key in subtask:
                if subtask[key] is not None and not isinstance(subtask[key], str):
                    raise ValueError('Invalid subtask text: ' + eid)
                record[key] = subtask[key]
        for key in ('arm_1', 'arm_2'):
            if key in subtask:
                arm = subtask[key]
                if not isinstance(arm, dict):
                    raise ValueError('Invalid arm record: ' + eid)
                record[key] = {k: arm[k] for k in ('description', 'outcome') if k in arm}
                if any(v is not None and not isinstance(v, str) for v in record[key].values()):
                    raise ValueError('Invalid arm text: ' + eid)
        records.append(record)
    terminal = value.get('terminal_summary')
    if terminal is not None and not isinstance(terminal, str):
        raise ValueError('Invalid terminal_summary: ' + eid)
    if not terminal and not any(r.get('segment_summary') or any(r.get(a, {}).get('description') for a in ('arm_1', 'arm_2')) for r in records):
        raise ValueError('No usable result text: ' + eid)
    # Exclude old reviewer opinions and generated instruction/reasoning fields.
    return {'episode_id': eid, 'annotation': {'terminal_summary': terminal, 'subtasks': records}}, hashlib.sha256(raw).hexdigest()


def prepare(cfg):
    validate_config(cfg)
    root = Path(cfg['image_results_root'])
    summary = read(root / 'summary.json')
    if summary.get('status') != 'complete' or summary.get('failed_tasks', 0):
        raise ValueError('Image stage must be complete before text refinement')
    files = sorted(root.glob('task_*/final.json'))
    if len(files) != summary.get('tasks') or summary.get('completed_tasks') != len(files):
        raise ValueError('Image task coverage mismatch')
    selected = set(cfg['task_ids'])
    seen_tasks, seen_episodes, jobs = set(), set(), []
    total_groups = 0
    for path in files:
        raw = path.read_bytes()
        task = json.loads(raw)
        tid = task['task_id']
        if type(tid) is not int or tid < 0 or tid in seen_tasks or path.parent.name != f'task_{tid:03d}':
            raise ValueError('Invalid image task ID')
        seen_tasks.add(tid)
        if not isinstance(task['task'], str) or not task['task'].strip():
            raise ValueError('Empty task text')
        local, groups = {}, task['groups']
        if not isinstance(groups, list) or not groups or len(groups) != task['final_group_count']:
            raise ValueError('Image group coverage mismatch')
        total_groups += len(groups)
        parents = set()
        for group in groups:
            gid = safe_id(group['cluster_id'])
            if gid in parents:
                raise ValueError('Duplicate parent scene group')
            parents.add(gid)
            ids = group['member_episode_ids']
            if not isinstance(ids, list) or not ids:
                raise ValueError('Empty scene group')
            for eid in ids:
                safe_id(eid)
                if eid in seen_episodes:
                    raise ValueError('Duplicate image-stage episode: ' + eid)
                seen_episodes.add(eid)
                local[eid] = gid
            if group['status'] not in ('coherent', 'singleton', 'uncertain'):
                raise ValueError('Invalid parent scene status')
            if selected and tid not in selected:
                continue
            shuffled = sorted(ids)
            random.Random(f'{cfg["shuffle_seed"]}:{tid}:{gid}').shuffle(shuffled)
            jobs.append({'task_id': tid, 'task': task['task'], 'parent_scene_group_id': gid,
                         'parent_scene_status': group['status'],
                         'parent_scene_description': group.get('common_features', ''),
                         'image_final_path': str(path), 'image_final_sha256': hashlib.sha256(raw).hexdigest(),
                         'episode_ids': shuffled, 'episodes': [], 'annotation_sources': [],
                         'skip': len(ids) < cfg['text_clustering_min_group_size']})
        if len(local) != task['episode_count']:
            raise ValueError('Image episode count mismatch')
        assignments = task['assignments']
        if len(assignments) != len(local) or len({a['episode_id'] for a in assignments}) != len(local):
            raise ValueError('Image assignments incomplete or duplicated')
        if any(a['task_id'] != tid or local.get(a['episode_id']) != a['cluster_id'] for a in assignments):
            raise ValueError('Image group and assignment disagree')
    if len(seen_episodes) != summary['episodes'] or total_groups != summary['groups']:
        raise ValueError('Image summary counts disagree')
    if selected - seen_tasks or not jobs:
        raise ValueError('Requested tasks not found')
    annotation_root = Path(cfg['annotations_root']).resolve()

    def load(eid):
        path = (annotation_root / cfg['annotation_pattern'].format(episode_id=eid)).resolve()
        if not path.is_relative_to(annotation_root):
            raise ValueError('Annotation path escapes root')
        payload, digest = annotation_text(path, eid)
        return payload, {'episode_id': eid, 'path': str(path), 'sha256': digest}

    wanted = [eid for job in jobs if not job['skip'] for eid in job['episode_ids']]
    loaded = {}
    with ThreadPoolExecutor(max_workers=cfg['input_workers']) as pool:
        futures = {pool.submit(load, eid): eid for eid in wanted}
        for future in as_completed(futures):
            loaded[futures[future]] = future.result()
    for job in jobs:
        if not job['skip']:
            job['episodes'] = [loaded[eid][0] for eid in job['episode_ids']]
            job['annotation_sources'] = [loaded[eid][1] for eid in job['episode_ids']]
    return jobs
