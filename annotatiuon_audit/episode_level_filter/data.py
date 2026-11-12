import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def subtask_ids(annotation):
    subtasks = annotation.get('subtasks')
    if not isinstance(subtasks, list) or not subtasks:
        raise ValueError('final_annotation.subtasks must be a nonempty list')
    ids = []
    for index, item in enumerate(subtasks):
        if not isinstance(item, dict):
            raise ValueError('Each subtask must be an object')
        sid = item.get('subtask_index', index)
        if type(sid) not in (int, str) or sid in ids:
            raise ValueError('Invalid or duplicate subtask_index')
        ids.append(sid)
    return ids


def prepare(cfg, on_progress=None):
    raw = read_json(cfg['standards_path'])
    # Canonical expanded export, direct episode mapping, or explicit episode list.
    rows = raw.get('episodes', raw) if isinstance(raw, dict) else raw
    if isinstance(rows, list):
        mapped = {}
        for row in rows:
            eid = row['episode_id']
            if eid in mapped:
                raise ValueError('Duplicate standard: ' + eid)
            mapped[eid] = row
        rows = mapped
    if not isinstance(rows, dict) or not rows:
        raise ValueError('Standards must contain a nonempty episode mapping')
    selected = cfg.get('episode_ids') or list(rows)
    if len(selected) != len(set(selected)):
        raise ValueError('Duplicate configured episode IDs')
    jobs = {}
    root = Path(cfg['annotations_root']).resolve()
    def load_episode(eid):
        if not isinstance(eid, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', eid):
            raise ValueError('Invalid episode_id')
        row = rows[eid]
        standard = row if isinstance(row, str) else row['review_standard']
        if not isinstance(standard, str) or not standard.strip():
            raise ValueError('Empty review standard: ' + eid)
        path = (root / cfg['annotation_pattern'].format(episode_id=eid)).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Annotation path escapes annotations_root')
        annotation = read_json(path)
        if not isinstance(annotation, dict):
            raise ValueError('Final annotation must be an object: ' + eid)
        if 'episode_id' in annotation and annotation['episode_id'] != eid:
            raise ValueError('Annotation episode_id mismatch: ' + eid)
        if isinstance(row, dict) and 'episode_id' in row and row['episode_id'] != eid:
            raise ValueError('Standard episode_id mismatch: ' + eid)
        subtask_ids(annotation)
        task = row.get('task') if isinstance(row, dict) else None
        task = task or annotation.get('task') or annotation.get('instruction')
        return {'episode_id': eid, 'review_standard': standard, 'task': task,
                'final_annotation': annotation, 'annotation_path': str(path)}
    workers = cfg.get('input_workers', 1)
    if type(workers) is not int or workers < 1:
        raise ValueError('input_workers must be a positive integer')
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(load_episode, eid): eid for eid in selected}
        for future in as_completed(futures):
            jobs[futures[future]] = future.result()
            if on_progress:
                on_progress(len(jobs), len(selected))
    return [jobs[eid] for eid in selected]
