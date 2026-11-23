import copy
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

import yaml

from ..correction.io import read, save
from ..correction.data import load_episode
from ..handoff import select_candidates
from ..workflow import build_correction_config
from .. import workflow
from ...keyframe_grounded_filter.two_round.selection import _audit_subtask


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.audit = self.root / 'audit'
        self.annotations = self.root / 'annotations'
        self.standards = self.root / 'standards.json'
        self.ids = ['ep_normal', 'ep_anomaly', 'ep_unmet', 'ep_uncertain']
        statuses = ['normal', 'annotation_anomaly', 'task_not_met', 'uncertain']
        save(self.audit / 'plan.json', {'episodes': self.ids})
        save(self.standards, {'episodes': {eid: {'task': 'Place the cup', 'review_standard': 'Cup is placed'}
                                          for eid in self.ids}})
        for eid, status in zip(self.ids, statuses):
            sub = {'segment_type': 'operation', 'segment_summary': 'Place the cup',
                   'arm_1': {'description': 'Place the cup', 'outcome': '完成'},
                   'arm_2': {'description': 'Idle', 'outcome': '未参与'}}
            save(self.annotations / eid / 'final.json', {'subtasks': [sub]})
            annotation = 'inconsistent' if status == 'annotation_anomaly' else 'consistent'
            criterion = 'not_met' if status == 'task_not_met' else 'uncertain' if status == 'uncertain' else 'met'
            first = {'review_status': status, 'annotation_text_status': annotation,
                     'criterion_text_status': criterion, 'visual_requests': []}
            save(self.audit / eid / 'round1/input.json', {'payload': {'episode_id': eid,
                 'task': 'Place the cup', 'review_standard': 'Cup is placed',
                 'final_annotation': {'subtasks': [_audit_subtask(sub, 0)]}}})
            save(self.audit / eid / 'round1/output.json', first)
            save(self.audit / eid / 'final.json', {'episode_id': eid, 'final_status': status,
                 'annotation_status': annotation, 'criterion_status': criterion,
                 'round1': first, 'round2': None})
        self.cfg = {'output_root': str(self.audit), 'annotations_root': str(self.annotations),
                    'standards_path': str(self.standards), 'episode_ids': self.ids}
        self.selected = ['annotation_anomaly', 'task_not_met', 'uncertain']

    def test_three_non_normal_categories(self):
        value = select_candidates(self.cfg, self.selected)
        self.assertEqual(value['selected_episodes'], self.ids[1:])
        self.assertEqual(value['audit_counts']['normal'], 1)

    def test_normal_cannot_be_requested(self):
        with self.assertRaisesRegex(ValueError, 'non-normal'):
            select_candidates(self.cfg, ['normal'])

    def test_missing_episode_blocks_partial_handoff(self):
        (self.audit / self.ids[2] / 'final.json').unlink()
        with self.assertRaisesRegex(ValueError, 'handoff rejected'):
            select_candidates(self.cfg, self.selected)

    def test_changed_stage3_rejected(self):
        p = self.annotations / self.ids[1] / 'final.json'
        value = read(p); value['subtasks'][0]['segment_summary'] = 'Different operation'
        save(p, value)
        with self.assertRaisesRegex(ValueError, 'Stage3 changed'):
            select_candidates(self.cfg, self.selected)

    def test_changed_standard_rejected(self):
        value = read(self.standards)
        value['episodes'][self.ids[1]]['review_standard'] = 'Different requirement'
        save(self.standards, value)
        with self.assertRaisesRegex(ValueError, 'standard changed'):
            select_candidates(self.cfg, self.selected)

    def test_original_backup_not_compared_instead_of_current(self):
        p = self.annotations / self.ids[1] / 'final.json'
        value = read(p); old = copy.deepcopy(value)
        value['subtasks'][0]['segment_summary'] = 'Corrected operation'; value['old'] = old
        save(p, value)
        with self.assertRaisesRegex(ValueError, 'Stage3 changed'):
            select_candidates(self.cfg, self.selected)

    def test_round_result_must_match_final(self):
        p = self.audit / self.ids[1] / 'round1/output.json'
        value = read(p); value['review_status'] = 'normal'; save(p, value)
        with self.assertRaisesRegex(ValueError, 'saved first-round'):
            select_candidates(self.cfg, self.selected)

    def test_required_visual_review_must_exist(self):
        eid = self.ids[1]
        p = self.audit / eid / 'round1/output.json'
        value = read(p); value['visual_requests'] = [{'request_id': 'r1'}]; save(p, value)
        p = self.audit / eid / 'final.json'
        final = read(p); final['round1'] = value; save(p, final)
        with self.assertRaisesRegex(ValueError, 'missing required visual'):
            select_candidates(self.cfg, self.selected)

    def test_config_wires_exact_selection(self):
        handoff = select_candidates(self.cfg, self.selected)
        cfg = build_correction_config({'output_root': str(self.root / 'corrected'),
                    'video_metadata_root': str(self.root / 'metadata')}, self.cfg, handoff, self.root / 'work')
        self.assertEqual(cfg['episode_ids'], self.ids[1:])
        self.assertEqual(cfg['audit_root'], str(self.audit))
        self.assertIsNone(cfg['episode_ids_file'])

    def test_output_overlap_rejected(self):
        with self.assertRaisesRegex(ValueError, 'separate'):
            build_correction_config({'output_root': str(self.annotations / 'new'),
                    'video_metadata_root': str(self.root / 'metadata')}, self.cfg,
                    {'selected_episodes': self.ids[1:]}, self.root / 'work')

    def test_input_changed_after_handoff_rejected(self):
        handoff = select_candidates(self.cfg, self.selected)
        manifest = self.root / 'handoff.json'; save(manifest, handoff)
        p = self.annotations / self.ids[1] / 'final.json'
        value = read(p); value['subtasks'][0]['start_time_sec'] = 0.5; save(p, value)
        cfg = {'handoff_manifest': str(manifest), 'annotations_root': str(self.annotations),
               'audit_root': str(self.audit), 'standards_path': str(self.standards)}
        with self.assertRaisesRegex(ValueError, 'handoff input changed'):
            load_episode(cfg, self.ids[1])

    def workflow_fixture(self):
        base = {'output_root': str(self.root / 'corrected'),
                'video_metadata_root': str(self.root / 'metadata'), 'audit_statuses': self.selected}
        path = self.root / 'correction.yaml'
        path.write_text(yaml.safe_dump(base), encoding='utf-8')
        return {'audit_config': str(self.root / 'audit.yaml'), 'correction_config': str(path),
                'output_root': str(self.root / 'work')}

    def test_workflow_runs_exact_candidates_without_publishing(self):
        cfg = self.workflow_fixture()
        originals = {p: p.read_bytes() for p in self.annotations.glob('*/final.json')}
        def fake_run(command, **kwargs):
            self.assertIn('annotatiuon_audit.annotation_correction', command)
            config = yaml.safe_load(Path(command[-1]).read_text())
            self.assertEqual(config['episode_ids'], self.ids[1:])
            save(Path(config['output_root']) / 'progress.json', {'finished': 3, 'failed': 0})
            return SimpleNamespace(returncode=0)
        with patch.object(workflow, 'resolve_config', return_value=cfg), \
             patch.object(workflow, 'load_two_round_config', return_value=self.cfg), \
             patch.object(workflow.subprocess, 'run', side_effect=fake_run) as child:
            self.assertEqual(workflow.main(['--mode', 'correction']), 0)
            self.assertEqual(child.call_count, 1)
        state = read(self.root / 'work/status.json')
        self.assertFalse(state['published'])
        self.assertFalse(state['stage4_rerun'])
        for path, content in originals.items():
            self.assertEqual(path.read_bytes(), content)

    def test_workflow_dry_run_never_starts_children(self):
        cfg = self.workflow_fixture()
        with patch.object(workflow, 'resolve_config', return_value=cfg), \
             patch.object(workflow, 'load_two_round_config', return_value=self.cfg), \
             patch.object(workflow.subprocess, 'run') as child:
            self.assertEqual(workflow.main(['--mode', 'correction', '--dry-run']), 0)
            self.assertEqual(workflow.main(['--mode', 'all', '--dry-run']), 0)
            child.assert_not_called()
        self.assertFalse((self.root / 'work').exists())

    def test_zero_candidates_never_starts_correction(self):
        cfg = self.workflow_fixture()
        self.cfg['episode_ids'] = self.ids[:1]
        save(self.audit / 'plan.json', {'episodes': self.ids[:1]})
        with patch.object(workflow, 'resolve_config', return_value=cfg), \
             patch.object(workflow, 'load_two_round_config', return_value=self.cfg), \
             patch.object(workflow.subprocess, 'run') as child:
            self.assertEqual(workflow.main(['--mode', 'correction']), 0)
            child.assert_not_called()
        self.assertEqual(read(self.root / 'work/status.json')['candidates'], 0)


if __name__ == '__main__':
    unittest.main()
