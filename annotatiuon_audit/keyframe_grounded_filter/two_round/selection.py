from __future__ import annotations

import copy


def _audit_subtask(item, fallback_index):
    """Keep only the semantic fields needed by the text audit."""
    return {
        'subtask_index': copy.deepcopy(item.get('subtask_index', fallback_index)),
        'segment_type': copy.deepcopy(item.get('segment_type')),
        'arm_1': {
            'description': copy.deepcopy((item.get('arm_1') or {}).get('description')),
            'outcome': copy.deepcopy((item.get('arm_1') or {}).get('outcome')),
        },
        'arm_2': {
            'description': copy.deepcopy((item.get('arm_2') or {}).get('description')),
            'outcome': copy.deepcopy((item.get('arm_2') or {}).get('outcome')),
        },
        'segment_summary': copy.deepcopy(item.get('segment_summary')),
    }


def _references(frame, subtask_id=None, roles=None):
    values = frame.get('references') or []
    return [value for value in values
            if (subtask_id is None or value.get('subtask_index') == subtask_id)
            and (roles is None or value.get('role') in roles)]


def text_job(job):
    """Build a provenance-preserving first-round job with no image content or frame metadata."""
    payload = {key: copy.deepcopy(job['payload'][key]) for key in
               ('episode_id', 'review_standard', 'task', 'keyframe_annotation')}
    payload['final_annotation'] = {
        'subtasks': [
            _audit_subtask(item, index)
            for index, item in enumerate(job['payload']['final_annotation']['subtasks'])
        ]
    }
    payload['audit_round'] = {'name': 'round1_text_review', 'image_count': 0,
                              'rule': 'Text first; request images only for unresolved core claims.'}
    return {**job, 'payload': payload, 'images': []}


def detail_job(job, text_review, maximum, neighbor_context=False):
    frames = job['payload']['visual_frames']
    targets = list(dict.fromkeys(request['subtask_index']
                                 for request in text_review['visual_requests']))
    chosen = {frame['frame_ref'] for frame in frames
              if any(_references(frame, target) for target in targets)}
    neighbors = []
    if neighbor_context:
        ids = [item.get('subtask_index', index)
               for index, item in enumerate(job['payload']['final_annotation']['subtasks'])]
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
    ordered = [frame for frame in frames if frame['frame_ref'] in chosen]
    if not ordered:
        raise ValueError('Visual request selected no frames: ' + job['episode_id'])
    if len(ordered) > maximum:
        raise ValueError(f'Round2 selected {len(ordered)} images, cap={maximum}: {job["episode_id"]}')
    image_map = {image['frame_ref']: image for image in job['images']}
    payload = text_job(job)['payload']
    payload['visual_frames'] = ordered
    payload['round1_text_review'] = copy.deepcopy(text_review)
    payload['audit_round'] = {
        'name': 'round2_targeted_visual_resolution',
        'target_subtasks': targets,
        'neighbor_context_subtasks': neighbors,
        'rule': 'Use images only to resolve round1 visual_requests.',
    }
    return {**job, 'payload': payload,
            'images': [image_map[frame['frame_ref']] for frame in ordered]}


__all__ = ['text_job', 'detail_job']
