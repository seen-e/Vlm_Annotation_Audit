from ...episode_level_filter.data import subtask_ids
from ...episode_level_filter.schema import pointer


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _text_evidence(values, payload):
    require(isinstance(values, list) and values, 'Each subtask review requires text evidence')
    for value in values:
        require(isinstance(value, dict) and set(value) == {'source', 'path', 'quote'},
                'Invalid text evidence')
        require(value['source'] in ('final_annotation', 'keyframe_annotation'),
                'Invalid text evidence source')
        text = pointer(payload[value['source']], value['path'])
        require(isinstance(text, str),
                f'Text evidence path is not a string: {value["source"]}{value["path"]}')
        require(isinstance(value['quote'], str) and value['quote'].strip() and
                value['quote'] in text,
                f'Text evidence quote mismatch at {value["source"]}{value["path"]}; '
                f'copy a continuous substring verbatim from: {text}')


def validate_text_review(result, payload):
    keys = {'episode_id', 'review_status', 'annotation_text_status', 'criterion_text_status',
            'reason', 'subtask_reviews', 'visual_requests'}
    require(isinstance(result, dict) and set(result) == keys, 'Invalid text review output keys')
    require(result['episode_id'] == payload['episode_id'], 'Wrong episode_id')
    require(result['review_status'] in ('normal', 'annotation_anomaly', 'task_not_met', 'needs_visual'),
            'Invalid review_status')
    require(result['annotation_text_status'] in ('consistent', 'inconsistent', 'uncertain'),
            'Invalid annotation_text_status')
    require(result['criterion_text_status'] in ('met', 'not_met', 'uncertain'),
            'Invalid criterion_text_status')
    require(isinstance(result['reason'], str) and result['reason'].strip(), 'Missing reason')

    ids = subtask_ids(payload['final_annotation'])
    reviews = result['subtask_reviews']
    require(isinstance(reviews, list) and len(reviews) == len(ids),
            'Subtask review coverage mismatch')
    needs_visual = []
    inconsistent = False
    for expected, review in zip(ids, reviews):
        review_keys = {'subtask_index', 'text_status', 'claim_under_review', 'reason',
                       'text_evidence', 'visual_question'}
        require(isinstance(review, dict) and set(review) == review_keys,
                'Invalid subtask review keys')
        require(review['subtask_index'] == expected, 'Subtask reviews must preserve input order')
        require(review['text_status'] in ('consistent', 'inconsistent', 'needs_visual'),
                'Invalid text_status')
        for name in ('claim_under_review', 'reason'):
            require(isinstance(review[name], str) and review[name].strip(), 'Missing ' + name)
        _text_evidence(review['text_evidence'], payload)
        question = review['visual_question']
        if review['text_status'] == 'needs_visual':
            require(isinstance(question, str) and question.strip(),
                    'needs_visual requires visual_question')
            needs_visual.append(expected)
        else:
            require(question is None, 'Resolved text review cannot request images')
        inconsistent = inconsistent or review['text_status'] == 'inconsistent'

    requests = result['visual_requests']
    require(isinstance(requests, list), 'visual_requests must be a list')
    request_ids = set()
    request_subtasks = []
    question_by_subtask = {review['subtask_index']: review['visual_question']
                           for review in reviews if review['text_status'] == 'needs_visual'}
    for request in requests:
        request_keys = {'request_id', 'subtask_index', 'dimension', 'question'}
        require(isinstance(request, dict) and set(request) == request_keys,
                'Invalid visual request keys')
        request_id = request['request_id']
        require(isinstance(request_id, str) and request_id.strip() and request_id not in request_ids,
                'Invalid or duplicate request_id')
        request_ids.add(request_id)
        sid = request['subtask_index']
        require(sid in needs_visual, 'Visual request targets a resolved subtask')
        request_subtasks.append(sid)
        require(request['dimension'] in ('annotation', 'criterion'), 'Invalid request dimension')
        require(request['question'] == question_by_subtask[sid],
                'Visual request question must match subtask review')
    require(set(request_subtasks) == set(needs_visual),
            'visual_requests must cover every needs_visual subtask')
    require(len(request_subtasks) == len(set(request_subtasks)),
            'Each needs_visual subtask must have exactly one visual request')

    status = result['review_status']
    if needs_visual:
        require(status == 'needs_visual' and requests,
                'Unresolved text claims must route to visual review')
        require(result['annotation_text_status'] == 'uncertain' or
                result['criterion_text_status'] == 'uncertain',
                'needs_visual requires an uncertain top-level dimension')
        if inconsistent:
            require(result['annotation_text_status'] == 'inconsistent',
                    'Known text inconsistency must be preserved during visual routing')
    else:
        require(status != 'needs_visual' and not requests,
                'Resolved text review cannot contain visual requests')
        if status == 'normal':
            require(not inconsistent and result['annotation_text_status'] == 'consistent' and
                    result['criterion_text_status'] == 'met', 'Normal conflicts with text findings')
        elif status == 'annotation_anomaly':
            require(inconsistent and result['annotation_text_status'] == 'inconsistent',
                    'Annotation anomaly requires an inconsistent subtask')
        elif status == 'task_not_met':
            require(not inconsistent and result['annotation_text_status'] == 'consistent' and
                    result['criterion_text_status'] == 'not_met',
                    'task_not_met conflicts with text findings')


def validate_visual_review(result, payload):
    require(isinstance(result, dict) and set(result) == {'episode_id', 'resolutions'},
            'Invalid visual review output keys')
    require(result['episode_id'] == payload['episode_id'], 'Wrong episode_id')
    requests = payload['round1_text_review']['visual_requests']
    resolutions = result['resolutions']
    require(isinstance(resolutions, list) and len(resolutions) == len(requests),
            'Visual resolution coverage mismatch')
    expected = {request['request_id']: request for request in requests}
    frame_refs = {frame['frame_ref'] for frame in payload['visual_frames']}
    seen = set()
    for resolution in resolutions:
        keys = {'request_id', 'subtask_index', 'question', 'annotation_visual_status',
                'criterion_status', 'reason', 'visual_evidence'}
        require(isinstance(resolution, dict) and set(resolution) == keys,
                'Invalid visual resolution keys')
        request_id = resolution['request_id']
        require(request_id in expected and request_id not in seen,
                'Unknown or duplicate visual request resolution')
        seen.add(request_id)
        request = expected[request_id]
        require(resolution['subtask_index'] == request['subtask_index'] and
                resolution['question'] == request['question'],
                'Visual resolution does not match its request')
        require(resolution['annotation_visual_status'] in ('consistent', 'inconsistent', 'uncertain'),
                'Invalid annotation_visual_status')
        require(resolution['criterion_status'] in ('met', 'not_met', 'not_applicable', 'uncertain'),
                'Invalid criterion_status')
        if request['dimension'] == 'criterion':
            require(resolution['criterion_status'] != 'not_applicable',
                    'Criterion request requires criterion judgement')
        require(isinstance(resolution['reason'], str) and resolution['reason'].strip(),
                'Missing visual resolution reason')
        evidence = resolution['visual_evidence']
        require(isinstance(evidence, list), 'visual_evidence must be a list')
        if resolution['annotation_visual_status'] != 'uncertain' or \
                resolution['criterion_status'] not in ('uncertain', 'not_applicable'):
            require(evidence, 'Resolved visual question requires visual evidence')
        for value in evidence:
            require(isinstance(value, dict) and set(value) == {'frame_ref', 'observation'},
                    'Invalid visual evidence')
            require(value['frame_ref'] in frame_refs, 'Unknown frame_ref')
            require(isinstance(value['observation'], str) and value['observation'].strip(),
                    'Missing visual observation')
    require(seen == set(expected), 'Missing visual request resolution')


__all__ = ['validate_text_review', 'validate_visual_review']
