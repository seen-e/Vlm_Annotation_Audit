from ...episode_level_filter.data import subtask_ids


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_screening(result, payload):
    keys = {'episode_id', 'screening_status', 'reason', 'target_subtasks', 'concerns'}
    require(isinstance(result, dict) and set(result) == keys, 'Invalid screening output keys')
    require(result['episode_id'] == payload['episode_id'], 'Wrong episode_id')
    require(result['screening_status'] in ('pass', 'needs_detail', 'uncertain'), 'Invalid screening_status')
    require(isinstance(result['reason'], str) and result['reason'].strip(), 'Missing screening reason')
    valid_ids = subtask_ids(payload['final_annotation'])
    targets = result['target_subtasks']
    require(isinstance(targets, list) and len(targets) == len(set(targets)) and
            all(value in valid_ids for value in targets), 'Invalid target_subtasks')
    require(isinstance(result['concerns'], list), 'concerns must be a list')
    frame_refs = {frame['frame_ref'] for frame in payload['visual_frames']}
    concern_targets = []
    for concern in result['concerns']:
        concern_keys = {'concern_type', 'subtask_indices', 'reason', 'visual_evidence', 'detail_request'}
        require(isinstance(concern, dict) and set(concern) == concern_keys, 'Invalid concern keys')
        require(concern['concern_type'] in ('annotation_visual_mismatch', 'criterion_not_met',
                                             'keyframe_misalignment', 'insufficient_visual_evidence'),
                'Invalid concern_type')
        values = concern['subtask_indices']
        require(isinstance(values, list) and values and len(values) == len(set(values)) and
                all(value in valid_ids for value in values), 'Invalid concern subtask_indices')
        concern_targets.extend(values)
        for name in ('reason', 'detail_request'):
            require(isinstance(concern[name], str) and concern[name].strip(), 'Missing ' + name)
        evidence = concern['visual_evidence']
        require(isinstance(evidence, list), 'visual_evidence must be a list')
        for value in evidence:
            require(isinstance(value, dict) and set(value) == {'frame_ref', 'observation'},
                    'Invalid screening visual evidence')
            require(value['frame_ref'] in frame_refs, 'Unknown screening frame_ref')
            require(isinstance(value['observation'], str) and value['observation'].strip(),
                    'Missing screening observation')
    if result['screening_status'] == 'pass':
        require(not targets and not result['concerns'], 'Pass cannot contain concerns')
    else:
        require(targets and result['concerns'], 'Detailed review requires concrete targets and concerns')
        require(set(targets) == set(concern_targets), 'target_subtasks must equal concern coverage')


__all__ = ['validate_screening']
