"""Draft output contract for offline plumbing tests; pending joint review."""

from .data import subtask_ids


def require(condition, message):
    if not condition:
        raise ValueError(message)


def pointer(value, path):
    require(isinstance(path, str) and path.startswith('/'), 'Invalid evidence path')
    for part in path[1:].split('/'):
        part = part.replace('~1', '/').replace('~0', '~')
        if isinstance(value, list):
            require(part.isdigit() and str(int(part)) == part, 'Invalid array index')
            value = value[int(part)]
        else:
            value = value[part]
    return value


def validate(result, payload):
    require(isinstance(result, dict) and set(result) == {'episode_id', 'review_status', 'reason', 'issues'}, 'Invalid output keys')
    require(result['episode_id'] == payload['episode_id'], 'Wrong episode_id')
    require(isinstance(result['reason'], str) and result['reason'].strip(), 'Missing reason')
    require(result['review_status'] in ('normal', 'anomaly', 'uncertain'), 'Invalid review_status')
    require(isinstance(result['issues'], list), 'issues must be a list')
    ids = subtask_ids(payload['final_annotation'])
    for issue in result['issues']:
        require(isinstance(issue, dict) and set(issue) == {'issue_type', 'severity', 'criterion', 'reason', 'affected_subtasks', 'evidence', 'video_check'}, 'Invalid issue keys')
        require(issue['issue_type'] in ('criterion_not_met', 'annotation_conflict', 'insufficient_evidence'), 'Invalid issue_type')
        require(issue['severity'] in ('anomaly', 'uncertain'), 'Invalid severity')
        if issue['issue_type'] == 'insufficient_evidence':
            require(issue['severity'] == 'uncertain', 'Missing information cannot prove anomaly')
        for name in ('reason', 'video_check'):
            require(isinstance(issue[name], str) and issue[name].strip(), 'Missing ' + name)
        criterion = issue['criterion']
        require(criterion is None or isinstance(criterion, str) and bool(criterion.strip()) and criterion in payload['review_standard'], 'Invented criterion')
        if issue['issue_type'] == 'criterion_not_met':
            require(criterion is not None, 'criterion_not_met requires a criterion')
        affected = issue['affected_subtasks']
        require(isinstance(affected, list), 'affected_subtasks must be a list')
        require(all(type(i) in (int, str) and i in ids for i in affected), 'Unknown subtask ID')
        require(len(set(affected)) == len(affected), 'Duplicate affected subtask')
        require(isinstance(issue['evidence'], list), 'evidence must be a list')
        if issue['severity'] == 'anomaly':
            require(bool(issue['evidence']), 'Anomaly requires evidence')
        for evidence in issue['evidence']:
            require(isinstance(evidence, dict) and set(evidence) == {'path', 'quote'}, 'Invalid evidence keys')
            value = pointer(payload['final_annotation'], evidence['path'])
            quote = evidence['quote']
            require(isinstance(value, str) and isinstance(quote, str) and bool(quote.strip()) and quote in value, 'Evidence is not a verbatim substring')
            parts = evidence['path'].split('/')
            if len(parts) >= 3 and parts[1] == 'subtasks':
                require(ids[int(parts[2])] in affected, 'Evidence subtask missing from affected_subtasks')
    status = 'anomaly' if any(i['severity'] == 'anomaly' for i in result['issues']) else 'uncertain' if result['issues'] else 'normal'
    require(result['review_status'] == status, 'Status conflicts with issues')
