import unittest

from .run import merge_reviews, normalize_text_review
from .schema import validate_text_review, validate_visual_review
from .selection import detail_job, text_job


def frame(index, references):
    return {'frame_ref': f'frame_{index:08d}', 'frame_index': index,
            'timestamp_sec': index / 10, 'references': references}


class TwoRoundTests(unittest.TestCase):
    def setUp(self):
        refs = lambda sid, role: [{'subtask_index': sid, 'role': role}]
        frames = [
            frame(0, refs(0, 'action_start_candidate')),
            frame(1, refs(0, 'process_keyframe')),
            frame(2, refs(0, 'action_end_candidate') + refs(1, 'action_start_candidate')),
            frame(3, refs(1, 'process_keyframe')),
            frame(4, refs(1, 'action_end_candidate')),
        ]
        images = [{**value, 'path': '/tmp/x.jpg', 'mime_type': 'image/jpeg',
                   'size': 1, 'sha256': str(value['frame_index'])} for value in frames]
        self.job = {
            'episode_id': 'ep_1',
            'sources': {},
            'payload': {
                'episode_id': 'ep_1', 'review_standard': '完成任务', 'task': 'task',
                'final_annotation': {'subtasks': [
                    {'subtask_index': 0, 'start_sample_index': 0,
                     'end_sample_index_exclusive': 2, 'segment_type': 'operation',
                     'arm_1': {'description': '移动红色物体', 'outcome': '进行中'},
                     'arm_2': {'description': '未参与', 'outcome': '未参与'},
                     'segment_summary': '移动红色物体', 'boundary_evidence': 'sample 2'},
                    {'start_sample_index': 2, 'end_sample_index_exclusive': 5,
                     'segment_type': 'operation',
                     'arm_1': {'description': '放置红色物体', 'outcome': '完成'},
                     'arm_2': {'description': '未参与', 'outcome': '未参与'},
                     'segment_summary': '放置红色物体', 'uncertainty': None},
                ]},
                'keyframe_annotation': {'subtasks': [
                    {'annotation': {'completion_criterion': '移动红色物体'}},
                    {'annotation': {'completion_criterion': '放置红色物体'}},
                ]},
                'visual_frames': frames,
            },
            'images': images,
        }

    def text_review(self, second_status='needs_visual'):
        visual = second_status == 'needs_visual'
        return {
            'episode_id': 'ep_1',
            'review_status': 'needs_visual' if visual else 'normal',
            'annotation_text_status': 'uncertain' if visual else 'consistent',
            'criterion_text_status': 'met',
            'reason': '第二段需要图像确认' if visual else '文本自洽',
            'subtask_reviews': [
                {'subtask_index': 0, 'text_status': 'consistent',
                 'claim_under_review': '移动红色物体', 'reason': '两份文本一致',
                 'text_evidence': [{'source': 'final_annotation',
                                    'path': '/subtasks/0/segment_summary',
                                    'quote': '移动红色物体'}], 'visual_question': None},
                {'subtask_index': 1, 'text_status': second_status,
                 'claim_under_review': '红色物体已被放置',
                 'reason': '文本未提供可验证终态' if visual else '两份文本一致',
                 'text_evidence': [{'source': 'final_annotation',
                                    'path': '/subtasks/1/segment_summary',
                                    'quote': '放置红色物体'}],
                 'visual_question': '结束帧中红色物体是否已稳定放置？' if visual else None},
            ],
            'visual_requests': ([{'request_id': 'q1', 'subtask_index': 1,
                                  'dimension': 'annotation',
                                  'question': '结束帧中红色物体是否已稳定放置？'}]
                                if visual else []),
        }

    def test_round1_is_strictly_text_only(self):
        selected = text_job(self.job)
        self.assertEqual(selected['images'], [])
        self.assertNotIn('visual_frames', selected['payload'])
        self.assertEqual(set(selected['payload']['final_annotation']), {'subtasks'})
        self.assertIn('keyframe_annotation', selected['payload'])
        subtasks = selected['payload']['final_annotation']['subtasks']
        self.assertEqual(subtasks[0], {
            'subtask_index': 0, 'segment_type': 'operation',
            'arm_1': {'description': '移动红色物体', 'outcome': '进行中'},
            'arm_2': {'description': '未参与', 'outcome': '未参与'},
            'segment_summary': '移动红色物体',
        })
        self.assertEqual(subtasks[1]['subtask_index'], 1)
        self.assertNotIn('start_sample_index', subtasks[0])
        self.assertNotIn('end_sample_index_exclusive', subtasks[0])
        self.assertNotIn('boundary_evidence', subtasks[0])
        self.assertNotIn('uncertainty', subtasks[1])
        self.assertEqual(selected['payload']['audit_round']['image_count'], 0)

    def test_text_status_aliases_are_normalized(self):
        result = {'annotation_text_status': 'normal', 'criterion_text_status': 'consistent'}
        self.assertEqual(normalize_text_review(result), {
            'annotation_text_status': 'consistent', 'criterion_text_status': 'met'
        })

    def test_text_schema_requires_every_subtask(self):
        payload = text_job(self.job)['payload']
        result = self.text_review()
        validate_text_review(result, payload)
        result['subtask_reviews'].pop()
        with self.assertRaisesRegex(ValueError, 'coverage'):
            validate_text_review(result, payload)

    def test_text_schema_requires_verbatim_evidence(self):
        payload = text_job(self.job)['payload']
        result = self.text_review()
        result['subtask_reviews'][0]['text_evidence'][0]['quote'] = '不存在的原文'
        with self.assertRaisesRegex(ValueError, 'verbatim'):
            validate_text_review(result, payload)

    def test_detail_contains_only_requested_subtask_frames(self):
        selected = detail_job(self.job, self.text_review(), 10)
        self.assertEqual([value['frame_index'] for value in selected['payload']['visual_frames']],
                         [2, 3, 4])
        self.assertEqual([value['frame_index'] for value in selected['images']], [2, 3, 4])
        self.assertEqual(selected['payload']['round1_text_review'], self.text_review())

    def test_visual_schema_resolves_exact_requests(self):
        selected = detail_job(self.job, self.text_review(), 10)
        result = {'episode_id': 'ep_1', 'resolutions': [{
            'request_id': 'q1', 'subtask_index': 1,
            'question': '结束帧中红色物体是否已稳定放置？',
            'annotation_visual_status': 'consistent', 'criterion_status': 'not_applicable',
            'reason': '结束帧可见物体稳定在目标位置',
            'visual_evidence': [{'frame_ref': 'frame_00000004', 'observation': '物体位于桌面'}],
        }]}
        validate_visual_review(result, selected['payload'])

    def test_merge_promotes_visual_mismatch(self):
        visual = {'episode_id': 'ep_1', 'resolutions': [{
            'request_id': 'q1', 'subtask_index': 1, 'question': 'x',
            'annotation_visual_status': 'inconsistent', 'criterion_status': 'not_applicable',
            'reason': 'x', 'visual_evidence': [],
        }]}
        status, annotation, criterion = merge_reviews(self.text_review(), visual)
        self.assertEqual((status, annotation, criterion),
                         ('annotation_anomaly', 'inconsistent', 'met'))


if __name__ == '__main__':
    unittest.main()
