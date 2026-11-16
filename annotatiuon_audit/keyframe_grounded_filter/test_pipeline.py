import copy
import json
from pathlib import Path
import tempfile
import unittest

import yaml

from .client import messages
from .data import prepare
from .run import run_jobs
from .schema import validate
from ..episode_level_filter.data import save_json


def normal_result(episode_id='ep_001'):
    return {'episode_id': episode_id, 'review_status': 'normal',
            'annotation_visual_status': 'consistent', 'criterion_status': 'met',
            'reason': '标注与关键帧一致且满足标准',
            'subtask_reviews': [{'subtask_index': 0, 'annotation_visual_status': 'consistent',
                                 'criterion_status': 'met', 'keyframe_quality': 'sufficient',
                                 'reason': '物体已移动到目标位置',
                                 'visual_evidence': [{'frame_ref': 'frame_00000010',
                                                      'observation': '物体位于目标位置'}]}],
            'issues': []}


class KeyframeGroundedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage = self.root / 'stage'
        self.cfg = yaml.safe_load(Path(__file__).with_name('config.yaml').read_text(encoding='utf-8'))
        self.cfg.update(standards_path=str(self.root / 'standards.json'),
                        annotations_root=str(self.root / 'annotations'), keyframe_roots=[str(self.stage)],
                        output_root=str(self.root / 'output'), episode_ids=['ep_001'], episode_batchsize=2,
                        input_workers=2, max_images_per_episode=10, max_image_bytes=1000, max_text_chars=100000)
        self.cfg['model'].update(attempts=2, retry_delay_sec=0)
        save_json(self.root / 'standards.json', {'episodes': {'ep_001': {
            'task': 'move object', 'review_standard': '将物体移动到目标位置'}}})
        save_json(self.root / 'annotations/ep_001/final.json', {
            'terminal_summary': '物体位于目标位置',
            'subtasks': [{'subtask_index': 0, 'segment_summary': '机械臂将物体移动到目标位置'}]})
        annotation = {'start_frame': {'frame_index': 0, 'timestamp_sec': 0.0},
                      'end_frame': {'frame_index': 10, 'timestamp_sec': 0.4},
                      'keyframes': [{'frame_index': 5, 'timestamp_sec': 0.2, 'event': '搬运',
                                     'description': '物体移动', 'evidence_frame_indices': [0, 5, 10]}],
                      'first_success_frame': {'frame_index': 10, 'timestamp_sec': 0.4},
                      'success_review': {'evidence_frame_indices': [10], 'judgement': 'success'},
                      'boundary_review': {'evidence_frame_indices': [0, 10], 'judgement': 'consistent'}}
        save_json(self.stage / 'ep_001/keyframes.json', {'episode_id': 'ep_001', 'subtasks': [
            {'subtask_id': 'ep_001:subtask:0', 'status': 'accepted', 'annotation': annotation}]})
        save_json(self.stage / 'ep_001/sample_manifest.json', {'fps': 25, 'samples': [
            {'frame_index': index, 'timestamp_sec': index / 25} for index in (0, 5, 10)]})
        for index in (0, 5, 10):
            path = self.stage / f'ep_001/stage4_keyframes/frames/{index:08d}.jpg'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'\xff\xd8\xff' + bytes([index]) + b'fake-jpeg')
        self.auth = {'base_url': 'https://example.invalid/v1', 'api_key': 'secret', 'model': 'fake'}

    def test_prepare_deduplicates_and_labels_visual_frames(self):
        job = prepare(self.cfg)[0]
        self.assertEqual([row['frame_index'] for row in job['payload']['visual_frames']], [0, 5, 10])
        self.assertEqual(len(job['images']), 3)
        end = job['payload']['visual_frames'][-1]
        self.assertEqual({ref['role'] for ref in end['references']},
                         {'action_end_candidate', 'first_success_candidate', 'process_evidence',
                          'success_review_evidence', 'boundary_review_evidence'})

    def test_multimodal_message_labels_each_image_before_bytes(self):
        job = prepare(self.cfg)[0]
        built = messages('prompt', job['payload'], job['images'], 'high')
        content = built[1]['content']
        self.assertEqual(len(content), 1 + 2 * len(job['images']))
        for index in range(1, len(content), 2):
            self.assertEqual(content[index]['type'], 'text')
            self.assertTrue(content[index]['text'].startswith('VISUAL_FRAME '))
            label = json.loads(content[index]['text'].removeprefix('VISUAL_FRAME '))
            self.assertIn('timestamp_sec', label)
            self.assertIn('references', label)
            self.assertEqual(content[index + 1]['type'], 'image_url')

    def test_schema_separates_task_failure_from_annotation_error(self):
        payload = prepare(self.cfg)[0]['payload']
        task_failure = normal_result()
        task_failure.update(review_status='task_not_met', criterion_status='not_met', reason='实际结果不满足标准')
        task_failure['subtask_reviews'][0].update(criterion_status='not_met')
        task_failure['issues'] = [{
            'issue_type': 'criterion_not_met', 'certainty': 'confirmed', 'criterion': '移动到目标位置',
            'reason': '目标关系未形成', 'affected_subtasks': [0],
            'text_evidence': [{'source': 'final_annotation', 'path': '/subtasks/0/segment_summary',
                               'quote': '移动到目标位置'}],
            'visual_evidence': [{'frame_ref': 'frame_00000010', 'observation': '物体不在目标位置'}],
            'reannotation_scope': 'none', 'video_check': '核实最终物体位置'}]
        validate(task_failure, payload)
        bad = copy.deepcopy(task_failure)
        bad['issues'][0]['reannotation_scope'] = 'partial'
        with self.assertRaisesRegex(ValueError, 'Task failure alone'):
            validate(bad, payload)

    def test_run_and_cache(self):
        calls = []
        def transport(prompt, payload, images, cfg, auth):
            calls.append((prompt, len(images)))
            return json.dumps(normal_result(), ensure_ascii=False), 'stop'
        jobs = prepare(self.cfg)
        first = run_jobs(jobs, self.cfg, self.auth, 'prompt', transport)
        second = run_jobs(jobs, self.cfg, self.auth, 'prompt', transport)
        self.assertEqual(first['normal'], 1)
        self.assertEqual(second['cached'], 1)
        self.assertEqual(len(calls), 1)

    def test_duplicate_stage4_source_is_rejected(self):
        duplicate = self.root / 'duplicate'
        source = self.stage / 'ep_001/keyframes.json'
        save_json(duplicate / 'ep_001/keyframes.json', json.loads(source.read_text()))
        self.cfg['keyframe_roots'].append(str(duplicate))
        with self.assertRaisesRegex(ValueError, 'exactly one Stage4 source'):
            prepare(self.cfg)


if __name__ == '__main__':
    unittest.main()
