import unittest

from .schema import validate_screening
from .selection import compact_job, detail_job


def frame(index, references):
    return {'frame_ref': f'frame_{index:08d}', 'frame_index': index,
            'timestamp_sec': index / 10, 'references': references}


class TwoRoundTests(unittest.TestCase):
    def setUp(self):
        refs = lambda sid, role: [{'subtask_index': sid, 'role': role}]
        frames = [
            frame(0, refs(0, 'action_start_candidate')),
            frame(1, refs(0, 'process_keyframe')),
            frame(2, refs(0, 'process_evidence')),
            frame(3, refs(0, 'action_end_candidate') + refs(1, 'action_start_candidate')),
            frame(4, refs(1, 'process_keyframe')),
            frame(5, refs(1, 'process_keyframe')),
            frame(6, refs(1, 'action_end_candidate')),
        ]
        images = [{**value, 'path': '/tmp/x.jpg', 'mime_type': 'image/jpeg',
                   'size': 1, 'sha256': str(value['frame_index'])} for value in frames]
        self.job = {
            'episode_id': 'ep_1',
            'sources': {},
            'payload': {
                'episode_id': 'ep_1', 'review_standard': '完成任务', 'task': 'task',
                'final_annotation': {'subtasks': [{'subtask_index': 0}, {'subtask_index': 1}]},
                'keyframe_annotation': {'subtasks': [
                    {'annotation': {'keyframes': [{}]}}, {'annotation': {'keyframes': [{}, {}]}}]},
                'visual_frames': frames,
            },
            'images': images,
        }

    def test_compact_uses_boundaries_and_one_process_frame(self):
        selected = compact_job(self.job, 10)
        self.assertEqual([value['frame_index'] for value in selected['payload']['visual_frames']],
                         [0, 1, 3, 5, 6])

    def test_detail_keeps_compact_set_and_expands_target(self):
        screening = {
            'episode_id': 'ep_1', 'screening_status': 'needs_detail', 'reason': '存在疑点',
            'target_subtasks': [0], 'concerns': [{
                'concern_type': 'annotation_visual_mismatch', 'subtask_indices': [0],
                'reason': '状态可能不符',
                'visual_evidence': [{'frame_ref': 'frame_00000003', 'observation': '状态不同'}],
                'detail_request': '核对过程变化',
            }],
        }
        selected = detail_job(self.job, screening, 10)
        self.assertEqual([value['frame_index'] for value in selected['payload']['visual_frames']],
                         [0, 1, 2, 3, 5, 6])
        self.assertEqual(selected['payload']['round1_screening'], screening)

    def test_screening_schema_requires_concrete_targets(self):
        payload = self.job['payload']
        passed = {'episode_id': 'ep_1', 'screening_status': 'pass', 'reason': '未见具体疑点',
                  'target_subtasks': [], 'concerns': []}
        validate_screening(passed, payload)
        invalid = {**passed, 'screening_status': 'uncertain'}
        with self.assertRaisesRegex(ValueError, 'requires concrete'):
            validate_screening(invalid, payload)


if __name__ == '__main__':
    unittest.main()
