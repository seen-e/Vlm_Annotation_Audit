import hashlib
import json
import mimetypes
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..episode_level_filter.data import read_json, subtask_ids


def safe_episode_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('Invalid episode_id: ' + str(value))
    return value


def standards(path):
    raw = read_json(path)
    rows = raw.get('episodes', raw) if isinstance(raw, dict) else raw
    if isinstance(rows, list):
        mapped = {}
        for row in rows:
            eid = safe_episode_id(row.get('episode_id'))
            if eid in mapped:
                raise ValueError('Duplicate standard: ' + eid)
            mapped[eid] = row
        rows = mapped
    if not isinstance(rows, dict) or not rows:
        raise ValueError('Standards must contain a nonempty episode mapping')
    return rows


def locate(root, pattern, episode_id, **values):
    path = (Path(root) / pattern.format(episode_id=episode_id, **values)).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('Input path escapes configured root')
    return path


def find_stage4_root(cfg, episode_id):
    found = []
    for root in cfg['keyframe_roots']:
        path = locate(root, cfg['keyframe_pattern'], episode_id)
        if path.is_file():
            found.append((Path(root).resolve(), path))
    if len(found) != 1:
        raise ValueError(f'Expected exactly one Stage4 source for {episode_id}, found {len(found)}')
    return found[0]


def add_frame(selected, frame, role, subtask_index):
    if frame is None:
        return
    if isinstance(frame, int):
        frame = {'frame_index': frame}
    if not isinstance(frame, dict) or type(frame.get('frame_index')) is not int or frame['frame_index'] < 0:
        raise ValueError('Invalid keyframe reference')
    index = frame['frame_index']
    row = selected.setdefault(index, {'frame_index': index, 'timestamp_sec': frame.get('timestamp_sec'), 'references': []})
    if row['timestamp_sec'] is None and frame.get('timestamp_sec') is not None:
        row['timestamp_sec'] = frame['timestamp_sec']
    reference = {'subtask_index': subtask_index, 'role': role}
    if reference not in row['references']:
        row['references'].append(reference)


def add_indices(selected, values, role, subtask_index):
    if values is None:
        return
    if not isinstance(values, list) or any(type(value) is not int or value < 0 for value in values):
        raise ValueError('Invalid evidence_frame_indices')
    for value in values:
        add_frame(selected, value, role, subtask_index)


def visual_frames(stage4, manifest, final_ids):
    if not isinstance(stage4, dict) or not isinstance(stage4.get('subtasks'), list):
        raise ValueError('Invalid keyframes.json')
    if len(stage4['subtasks']) != len(final_ids):
        raise ValueError('Stage4/final subtask count mismatch')
    selected = {}
    for position, item in enumerate(stage4['subtasks']):
        if not isinstance(item, dict) or not isinstance(item.get('annotation'), dict):
            raise ValueError('Missing Stage4 subtask annotation')
        sid = final_ids[position]
        annotation = item['annotation']
        add_frame(selected, annotation.get('start_frame'), 'action_start_candidate', sid)
        add_frame(selected, annotation.get('end_frame'), 'action_end_candidate', sid)
        for keyframe in annotation.get('keyframes', []):
            if not isinstance(keyframe, dict):
                raise ValueError('Invalid process keyframe')
            add_frame(selected, keyframe, 'process_keyframe', sid)
            add_indices(selected, keyframe.get('evidence_frame_indices'), 'process_evidence', sid)
        add_frame(selected, annotation.get('first_success_frame'), 'first_success_candidate', sid)
        for name in ('success_review', 'boundary_review'):
            review = annotation.get(name)
            if review is not None:
                if not isinstance(review, dict):
                    raise ValueError('Invalid ' + name)
                add_indices(selected, review.get('evidence_frame_indices'), name + '_evidence', sid)
    fps = manifest.get('fps') if isinstance(manifest, dict) else None
    samples = manifest.get('samples', []) if isinstance(manifest, dict) else []
    timestamps = {row['frame_index']: row.get('timestamp_sec') for row in samples
                  if isinstance(row, dict) and type(row.get('frame_index')) is int}
    for index, row in selected.items():
        if row['timestamp_sec'] is None:
            row['timestamp_sec'] = timestamps.get(index)
        if row['timestamp_sec'] is None and isinstance(fps, (int, float)) and fps > 0:
            row['timestamp_sec'] = index / fps
        row['frame_ref'] = f'frame_{index:08d}'
        row['references'].sort(key=lambda ref: (str(ref['subtask_index']), ref['role']))
    return [selected[index] for index in sorted(selected)]


def image_source(root, cfg, episode_id, frame):
    path = locate(root, cfg['frame_pattern'], episode_id, frame_index=frame['frame_index'])
    raw = path.read_bytes()
    if len(raw) > cfg['max_image_bytes']:
        raise ValueError('Image exceeds max_image_bytes: ' + str(path))
    mime = mimetypes.guess_type(path.name)[0]
    if mime not in ('image/jpeg', 'image/png', 'image/webp'):
        raise ValueError('Unsupported image type: ' + str(path))
    return {'frame_ref': frame['frame_ref'], 'frame_index': frame['frame_index'], 'path': str(path),
            'mime_type': mime, 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def prepare(cfg, on_progress=None):
    rows = standards(cfg['standards_path'])
    selected_ids = cfg.get('episode_ids') or list(rows)
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError('Duplicate configured episode IDs')
    annotation_root = Path(cfg['annotations_root']).resolve()

    def load_episode(episode_id):
        safe_episode_id(episode_id)
        if episode_id not in rows:
            raise ValueError('Missing review standard: ' + episode_id)
        standard_row = rows[episode_id]
        standard = standard_row if isinstance(standard_row, str) else standard_row.get('review_standard')
        if not isinstance(standard, str) or not standard.strip():
            raise ValueError('Empty review standard: ' + episode_id)
        final_path = locate(annotation_root, cfg['annotation_pattern'], episode_id)
        final_annotation = read_json(final_path)
        if not isinstance(final_annotation, dict) or final_annotation.get('episode_id', episode_id) != episode_id:
            raise ValueError('Final annotation episode mismatch: ' + episode_id)
        ids = subtask_ids(final_annotation)
        stage_root, keyframe_path = find_stage4_root(cfg, episode_id)
        stage4 = read_json(keyframe_path)
        if stage4.get('episode_id', episode_id) != episode_id:
            raise ValueError('Stage4 episode mismatch: ' + episode_id)
        manifest_path = locate(stage_root, cfg['sample_manifest_pattern'], episode_id)
        manifest = read_json(manifest_path)
        frames = visual_frames(stage4, manifest, ids)
        if not frames or len(frames) > cfg['max_images_per_episode']:
            raise ValueError(f'Visual frame count {len(frames)} outside 1..{cfg["max_images_per_episode"]}: {episode_id}')
        images = [image_source(stage_root, cfg, episode_id, frame) for frame in frames]
        task = standard_row.get('task') if isinstance(standard_row, dict) else None
        task = task or final_annotation.get('task') or final_annotation.get('instruction')
        payload = {'episode_id': episode_id, 'review_standard': standard, 'task': task,
                   'final_annotation': final_annotation, 'keyframe_annotation': stage4,
                   'visual_frames': frames}
        chars = len(json.dumps(payload, ensure_ascii=False))
        if chars > cfg['max_text_chars']:
            raise ValueError(f'Payload has {chars} chars, cap={cfg["max_text_chars"]}: {episode_id}')
        return {'episode_id': episode_id, 'payload': payload, 'images': images,
                'sources': {'final_annotation': str(final_path), 'keyframe_annotation': str(keyframe_path),
                            'sample_manifest': str(manifest_path)}}

    jobs = {}
    with ThreadPoolExecutor(max_workers=cfg['input_workers']) as pool:
        futures = {pool.submit(load_episode, episode_id): episode_id for episode_id in selected_ids}
        for future in as_completed(futures):
            jobs[futures[future]] = future.result()
            if on_progress:
                on_progress(len(jobs), len(selected_ids))
    return [jobs[episode_id] for episode_id in selected_ids]
