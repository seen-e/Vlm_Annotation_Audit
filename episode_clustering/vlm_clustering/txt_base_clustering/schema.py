from ..image_base_clustering.schema import validate_round1 as image_round1
from ..image_base_clustering.schema import validate_round2 as image_round2


def mapped(result):
    if not isinstance(result, dict) or set(result) != {'groups'} or not isinstance(result['groups'], list):
        raise ValueError('Expected groups object')
    groups = []
    for row in result['groups']:
        if not isinstance(row, dict) or 'common_result' not in row or 'common_features' in row:
            raise ValueError('Expected common_result, not image features')
        groups.append({('common_features' if key == 'common_result' else key): value for key, value in row.items()})
    return {'groups': groups}


def validate_round1(result, ids, representatives):
    image_round1(mapped(result), ids, representatives)


def validate_round2(result, candidates):
    image_round2(mapped(result), candidates)
    for group in result['groups']:
        refs = group['source_group_ids']
        if any(candidates[key]['status'] == 'uncertain' for key in refs):
            if len(refs) != 1 or group['status'] != 'uncertain':
                raise ValueError('Uncertain candidate must remain isolated and uncertain')
