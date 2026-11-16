from ..episode_level_filter.data import subtask_ids
from ..episode_level_filter.schema import pointer


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(result, payload):
    top = {'episode_id', 'review_status', 'annotation_visual_status', 'criterion_status',
           'reason', 'subtask_reviews', 'issues'}
    require(isinstance(result, dict) and set(result) == top, 'Invalid output keys')
    require(result['episode_id'] == payload['episode_id'], 'Wrong episode_id')
    require(result['review_status'] in ('normal', 'annotation_anomaly', 'task_not_met', 'uncertain'), 'Invalid review_status')
    require(result['annotation_visual_status'] in ('consistent', 'inconsistent', 'uncertain'), 'Invalid annotation_visual_status')
    require(result['criterion_status'] in ('met', 'not_met', 'uncertain'), 'Invalid criterion_status')
    require(isinstance(result['reason'], str) and result['reason'].strip(), 'Missing reason')
    final_ids = subtask_ids(payload['final_annotation'])
    frame_refs = {frame['frame_ref'] for frame in payload['visual_frames']}
    reviews = result['subtask_reviews']
    require(isinstance(reviews, list) and len(reviews) == len(final_ids), 'Subtask review coverage mismatch')
    seen = set()
    for review in reviews:
        keys = {'subtask_index', 'annotation_visual_status', 'criterion_status', 'keyframe_quality', 'reason', 'visual_evidence'}
        require(isinstance(review, dict) and set(review) == keys, 'Invalid subtask review keys')
        sid = review['subtask_index']
        require(sid in final_ids and sid not in seen, 'Invalid or duplicate reviewed subtask')
        seen.add(sid)
        require(review['annotation_visual_status'] in ('consistent', 'inconsistent', 'uncertain'), 'Invalid subtask visual status')
        require(review['criterion_status'] in ('met', 'not_met', 'not_applicable', 'uncertain'), 'Invalid subtask criterion status')
        require(review['keyframe_quality'] in ('sufficient', 'insufficient', 'misaligned'), 'Invalid keyframe quality')
        require(isinstance(review['reason'], str) and review['reason'].strip(), 'Missing subtask reason')
        _visual_evidence(review['visual_evidence'], frame_refs)
    require(seen == set(final_ids), 'Missing subtask review')
    require(isinstance(result['issues'], list), 'issues must be a list')
    for issue in result['issues']:
        keys = {'issue_type', 'certainty', 'criterion', 'reason', 'affected_subtasks', 'text_evidence',
                'visual_evidence', 'reannotation_scope', 'video_check'}
        require(isinstance(issue, dict) and set(issue) == keys, 'Invalid issue keys')
        require(issue['issue_type'] in ('annotation_visual_mismatch', 'criterion_not_met',
                                        'keyframe_misalignment', 'insufficient_visual_evidence'), 'Invalid issue_type')
        require(issue['certainty'] in ('confirmed', 'uncertain'), 'Invalid certainty')
        if issue['issue_type'] == 'insufficient_visual_evidence':
            require(issue['certainty'] == 'uncertain', 'Insufficient evidence cannot be confirmed')
        criterion = issue['criterion']
        require(criterion is None or isinstance(criterion, str) and criterion.strip() and
                criterion in payload['review_standard'], 'Invented criterion')
        if issue['issue_type'] == 'criterion_not_met':
            require(criterion is not None, 'criterion_not_met requires criterion')
        affected = issue['affected_subtasks']
        require(isinstance(affected, list) and len(set(affected)) == len(affected) and
                all(item in final_ids for item in affected), 'Invalid affected_subtasks')
        for name in ('reason', 'video_check'):
            require(isinstance(issue[name], str) and issue[name].strip(), 'Missing ' + name)
        require(issue['reannotation_scope'] in ('none', 'partial', 'whole_episode'), 'Invalid reannotation_scope')
        if issue['reannotation_scope'] == 'partial':
            require(bool(affected), 'Partial reannotation requires affected subtasks')
        if issue['issue_type'] == 'criterion_not_met' and issue['reannotation_scope'] != 'none':
            require(any(r['annotation_visual_status'] == 'inconsistent' and r['subtask_index'] in affected
                        for r in reviews), 'Task failure alone must not trigger reannotation')
        _text_evidence(issue['text_evidence'], payload, affected, final_ids)
        _visual_evidence(issue['visual_evidence'], frame_refs)
        if issue['certainty'] == 'confirmed':
            require(issue['text_evidence'] or issue['visual_evidence'], 'Confirmed issue requires evidence')
    if result['review_status'] == 'normal':
        require(result['annotation_visual_status'] == 'consistent' and result['criterion_status'] == 'met' and
                not result['issues'], 'Normal status conflicts with findings')
    elif result['review_status'] == 'annotation_anomaly':
        require(result['annotation_visual_status'] == 'inconsistent' and
                any(issue['reannotation_scope'] != 'none' for issue in result['issues']), 'Missing annotation anomaly')
    elif result['review_status'] == 'task_not_met':
        require(result['annotation_visual_status'] != 'inconsistent' and result['criterion_status'] == 'not_met',
                'task_not_met conflicts with statuses')
    else:
        require(result['annotation_visual_status'] == 'uncertain' or result['criterion_status'] == 'uncertain' or
                any(issue['certainty'] == 'uncertain' for issue in result['issues']), 'Uncertain status lacks uncertainty')


def _visual_evidence(values, frame_refs):
    require(isinstance(values, list), 'visual_evidence must be a list')
    for value in values:
        require(isinstance(value, dict) and set(value) == {'frame_ref', 'observation'}, 'Invalid visual evidence')
        require(value['frame_ref'] in frame_refs, 'Unknown frame_ref')
        require(isinstance(value['observation'], str) and value['observation'].strip(), 'Missing visual observation')


def _text_evidence(values, payload, affected, final_ids):
    require(isinstance(values, list), 'text_evidence must be a list')
    for value in values:
        require(isinstance(value, dict) and set(value) == {'source', 'path', 'quote'}, 'Invalid text evidence')
        require(value['source'] in ('final_annotation', 'keyframe_annotation'), 'Invalid text evidence source')
        text = pointer(payload[value['source']], value['path'])
        require(isinstance(text, str) and isinstance(value['quote'], str) and value['quote'].strip() and
                value['quote'] in text, 'Text evidence is not verbatim')
        if value['source'] == 'final_annotation':
            parts = value['path'].split('/')
            if len(parts) >= 3 and parts[1] == 'subtasks' and parts[2].isdigit():
                require(final_ids[int(parts[2])] in affected, 'Text evidence subtask missing from affected_subtasks')
