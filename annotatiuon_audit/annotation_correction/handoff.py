"""Validate the current two-round audit before selecting correction candidates."""
import hashlib
from pathlib import Path

from ..keyframe_grounded_filter.data import safe_episode_id, standards
from ..keyframe_grounded_filter.two_round.selection import _audit_subtask
from ..keyframe_grounded_filter.two_round.run import merge_reviews
from .correction.io import read

STATUSES = {'normal', 'annotation_anomaly', 'task_not_met', 'uncertain'}


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def select_candidates(audit_cfg, selected_statuses):
    selected_statuses = set(selected_statuses)
    if not selected_statuses or not selected_statuses <= STATUSES - {'normal'}:
        raise ValueError('Correction selection must contain non-normal audit statuses only')
    root = Path(audit_cfg['output_root'])
    plan = read(root / 'plan.json')
    ids = plan['episodes']
    if not ids or len(set(ids)) != len(ids):
        raise ValueError('Audit plan must contain a nonempty unique episode list')
    for eid in ids:
        safe_episode_id(eid)
    expected = audit_cfg.get('episode_ids')
    if expected and set(ids) != set(expected):
        raise ValueError('Audit plan differs from configured episode selection')
    if audit_cfg.get('annotation_pattern', '{episode_id}/final.json') != '{episode_id}/final.json':
        raise ValueError('Correction currently requires <episode>/final.json inputs')
    standard_map = standards(audit_cfg['standards_path'])
    counts = {s: 0 for s in sorted(STATUSES)}
    candidates, records = [], {}
    problems = []
    for eid in ids:
        try:
            directory = root / eid
            final_path = directory / 'final.json'
            result = read(final_path)
            if result.get('episode_id') != eid or result.get('final_status') not in STATUSES:
                raise ValueError('Invalid audit episode/status')
            first_input = read(directory / 'round1/input.json')
            payload = first_input['payload']
            if payload.get('episode_id') != eid:
                raise ValueError('Audit input episode mismatch')
            first = read(directory / 'round1/output.json')
            if result.get('round1') != first:
                raise ValueError('Final audit and saved first-round result differ')
            second = result.get('round2')
            if bool(first.get('visual_requests')) != (second is not None):
                raise ValueError('Audit is missing required visual review')
            if second is not None and second != read(directory / 'round2/output.json'):
                raise ValueError('Final audit and saved second-round result differ')
            merged = merge_reviews(first, second)
            if merged != (result['final_status'], result['annotation_status'], result['criterion_status']):
                raise ValueError('Final audit does not match its round results')
            source = Path(audit_cfg['annotations_root']) / eid / 'final.json'
            annotation = read(source)
            current = {'subtasks': [_audit_subtask(s, i) for i, s in enumerate(annotation['subtasks'])]}
            if payload['final_annotation'] != current:
                raise ValueError('Stage3 changed after audit; rerun audit before correction')
            standard = standard_map[eid]
            text = standard if isinstance(standard, str) else standard['review_standard']
            task = standard.get('task') if isinstance(standard, dict) else None
            task = task or annotation.get('task') or annotation.get('instruction')
            if payload['review_standard'] != text or payload.get('task') != task:
                raise ValueError('Task/review standard changed after audit')
            counts[result['final_status']] += 1
            if result['final_status'] in selected_statuses:
                candidates.append(eid)
                records[eid] = {'audit_status': result['final_status'],
                    'annotation_sha256': file_sha(source), 'audit_sha256': file_sha(final_path)}
        except (ValueError, KeyError, TypeError, IndexError, OSError) as exc:
            problems.append({'episode_id': eid, 'error': str(exc)})
    if problems:
        raise ValueError(f'Audit handoff rejected {len(problems)} episodes: {problems[:5]}')
    return {'schema_version': 'audit-correction-handoff-v1', 'audited_episodes': len(ids),
            'audit_counts': counts, 'selected_episodes': candidates, 'episodes': records,
            'standards_sha256': file_sha(audit_cfg['standards_path']),
            'audit_root': str(root.resolve()),
            'annotations_root': str(Path(audit_cfg['annotations_root']).resolve())}
