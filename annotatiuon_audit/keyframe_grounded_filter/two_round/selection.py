from __future__ import annotations

import copy

from ...episode_level_filter.data import subtask_ids


def _references(frame, subtask_id=None, roles=None):
    values = frame.get('references') or []
    return [value for value in values
            if (subtask_id is None or value.get('subtask_index') == subtask_id)
            and (roles is None or value.get('role') in roles)]


def _selected_job(job, frame_refs, round_info, extra_payload=None, maximum=None):
    ordered = [frame for frame in job['payload']['visual_frames'] if frame['frame_ref'] in frame_refs]
    if not ordered:
        raise ValueError('Frame selection is empty: ' + job['episode_id'])
    if maximum is not None and len(ordered) > maximum:
        raise ValueError(f'{round_info["name"]} selected {len(ordered)} images, cap={maximum}: {job["episode_id"]}')
    image_map = {image['frame_ref']: image for image in job['images']}
    payload = copy.deepcopy(job['payload'])
    payload['visual_frames'] = ordered
    payload['audit_round'] = round_info
    if extra_payload:
        payload.update(copy.deepcopy(extra_payload))
    return {**job, 'payload': payload, 'images': [image_map[frame['frame_ref']] for frame in ordered]}


def compact_job(job, maximum):
    frames = job['payload']['visual_frames']
    ids = subtask_ids(job['payload']['final_annotation'])
    chosen = {frames[0]['frame_ref'], frames[-1]['frame_ref']}
    selection = {}
    stage_items = job['payload']['keyframe_annotation']['subtasks']
    for position, subtask_id in enumerate(ids):
        selected_for_subtask = []
        for role in ('action_start_candidate', 'action_end_candidate'):
            candidates = [frame for frame in frames if _references(frame, subtask_id, {role})]
            for frame in candidates:
                chosen.add(frame['frame_ref'])
                selected_for_subtask.append({'frame_ref': frame['frame_ref'], 'role': role})
        process = [frame for frame in frames if _references(frame, subtask_id, {'process_keyframe'})]
        representative_role = 'process_keyframe'
        if not process:
            annotation = stage_items[position].get('annotation') or {}
            fallback = (annotation.get('frame_fill_postprocess') or {}).get(
                'first_success_frame_is_boundary_fallback') is True
            if not fallback:
                process = [frame for frame in frames
                           if _references(frame, subtask_id, {'first_success_candidate'})]
                representative_role = 'first_success_candidate'
        if process:
            representative = process[len(process) // 2]
            chosen.add(representative['frame_ref'])
            selected_for_subtask.append({'frame_ref': representative['frame_ref'],
                                         'role': 'representative_' + representative_role})
        selection[str(subtask_id)] = selected_for_subtask
    info = {'name': 'round1_compact_screening', 'purpose': 'locate concrete concerns only',
            'selection': selection}
    return _selected_job(job, chosen, info, maximum=maximum)


def detail_job(job, screening, maximum, neighbor_context=True):
    frames = job['payload']['visual_frames']
    ids = subtask_ids(job['payload']['final_annotation'])
    targets = screening['target_subtasks']
    compact = compact_job(job, len(frames))
    chosen = {frame['frame_ref'] for frame in compact['payload']['visual_frames']}
    for frame in frames:
        if any(_references(frame, target) for target in targets):
            chosen.add(frame['frame_ref'])
    neighbors = []
    if neighbor_context:
        positions = {value: index for index, value in enumerate(ids)}
        for target in targets:
            position = positions[target]
            for other in (position - 1, position + 1):
                if 0 <= other < len(ids) and ids[other] not in targets:
                    neighbors.append(ids[other])
        neighbors = list(dict.fromkeys(neighbors))
        boundary_roles = {'action_start_candidate', 'action_end_candidate'}
        for frame in frames:
            if any(_references(frame, neighbor, boundary_roles) for neighbor in neighbors):
                chosen.add(frame['frame_ref'])
    info = {'name': 'round2_targeted_detail', 'purpose': 'verify round1 concerns with expanded evidence',
            'target_subtasks': targets, 'neighbor_context_subtasks': neighbors}
    return _selected_job(job, chosen, info, {'round1_screening': screening}, maximum)


__all__ = ['compact_job', 'detail_job']
