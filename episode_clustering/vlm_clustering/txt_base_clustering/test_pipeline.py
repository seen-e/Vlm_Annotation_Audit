import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from ..image_base_clustering.api import request, save
from ..run import commands
from .data import prepare
from .run import preflight_plan, run_group, run_jobs
from .schema import validate_round1, validate_round2


def response(payload):
    if payload['round'] == 1:
        ids = [e['episode_id'] for e in payload['episodes']]
        return {'groups': [{'local_group_id': 'g1', 'status': 'coherent' if len(ids) > 1 else 'singleton',
                            'member_episode_ids': ids,
                            'representative_episode_ids': ids[:payload['representatives_per_cluster']],
                            'common_result': 'Object inside container', 'distinguishing_features': 'Same final relation'}]}
    candidates = payload['candidate_groups']
    return {'groups': [{'final_group_id': 'f1', 'source_group_ids': [c['group_id'] for c in candidates],
                        'status': 'coherent', 'common_result': 'Object inside container', 'reason': 'Same final relation'}]}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = yaml.safe_load(Path(__file__).with_name('config.yaml').read_text(encoding='utf-8'))
        self.cfg.update(image_results_root=str(self.root / 'images'), annotations_root=str(self.root / 'annotations'),
                        output_root=str(self.root / 'output'), episodes_per_batch=2, representatives_per_cluster=1)
        self.cfg['api'].update(attempts=2, retry_delay_sec=0)
        self.auth = {'base_url': 'https://example.invalid/v1', 'model': 'fake', 'api_key': 'test-secret'}
        self.calls = []
        for tid in (0, 1):
            groups, assignments = [], []
            for number, size in enumerate((2, 3)):
                gid = f't{tid:03d}_c{number:03d}'
                ids = [f'ep_{tid}_{number}_{i}' for i in range(size)]
                groups.append({'cluster_id': gid, 'member_episode_ids': ids, 'status': 'coherent', 'common_features': 'scene'})
                assignments.extend({'episode_id': eid, 'task_id': tid, 'cluster_id': gid} for eid in ids)
                if size == 3:
                    for eid in ids:
                        save(self.root / 'annotations' / eid / 'final.json',
                             {'terminal_summary': 'object in box', 'episode_success': True, 'review': 'PASS',
                              'subtasks': [{'segment_summary': 'Move object into box',
                                            'arm_1': {'description': 'Carry object', 'outcome': 'done'},
                                            'boundary_evidence': 'excluded'}]})
            save(self.root / 'images' / f'task_{tid:03d}' / 'final.json',
                 {'task_id': tid, 'task': 'put object in box', 'groups': groups, 'assignments': assignments,
                  'episode_count': 5, 'final_group_count': 2})
        save(self.root / 'images' / 'summary.json',
             {'status': 'complete', 'tasks': 2, 'completed_tasks': 2, 'failed_tasks': 0, 'episodes': 10, 'groups': 4})

    def caller(self, folder, prompt, payload, images, cfg, auth, validator):
        self.assertEqual(images, [])
        self.calls.append(copy.deepcopy(payload))
        result = response(payload)
        validator(result)
        return result

    def test_threshold_and_missing_skipped_annotations(self):
        jobs = prepare(self.cfg)
        self.assertEqual([j['skip'] for j in jobs], [True, False, True, False])
        self.assertEqual(sum(len(j['episode_ids']) for j in jobs), 10)
        out = run_group(jobs[0], self.cfg, self.root / 'out', {}, self.caller)
        self.assertEqual(len(self.calls), 0)
        self.assertEqual(out['groups'][0]['cluster_id'], jobs[0]['parent_scene_group_id'])
        self.assertEqual(out['groups'][0]['text_status'], 'not_evaluated')

    def test_two_rounds_and_parent_isolation(self):
        jobs = prepare(self.cfg)
        result = run_jobs(jobs, self.cfg, self.auth, self.caller)
        self.assertEqual(result['episodes'], 10)
        self.assertEqual(result['skipped_small_groups'], 2)
        self.assertEqual(len(self.calls), 6)
        for payload in self.calls:
            parent = payload['parent_scene_group_id']
            candidates = payload.get('candidate_groups', [])
            episodes = payload.get('episodes', []) + [e for c in candidates for e in c['representative_episodes']]
            expected = next(j['episode_ids'] for j in jobs if j['parent_scene_group_id'] == parent)
            self.assertTrue(all(e['episode_id'] in expected for e in episodes))
            self.assertTrue(all(set(e['annotation']) == {'terminal_summary', 'subtasks'} for e in episodes))
        groups = json.loads((self.root / 'output/final.json').read_text())['scene_groups']
        self.assertTrue(all(set(g['representative_episode_ids']) <= set(g['member_episode_ids']) for r in groups for g in r['groups']))

    def test_real_request_cache_and_text_only_transport(self):
        calls = []
        def transport(messages, auth, config):
            self.assertEqual([p['type'] for p in messages[1]['content']], ['text'])
            payload = json.loads(messages[1]['content'][0]['text'])
            calls.append(payload)
            return json.dumps(response(payload)), 'stop'
        def caller(*args):
            return request(*args, transport=transport)
        job = prepare(self.cfg)[1]
        run_group(job, self.cfg, self.root / 'cache', self.auth, caller)
        run_group(job, self.cfg, self.root / 'cache', self.auth, caller)
        self.assertEqual(len(calls), 3)

    def test_failed_request_retried_on_resume(self):
        job = prepare(self.cfg)[1]
        count = []
        def broken(*args):
            count.append(1)
            return '{}', 'length'
        with self.assertRaises(RuntimeError):
            run_group(job, self.cfg, self.root / 'cache', self.auth,
                      lambda *args: request(*args, transport=broken))
        self.assertEqual(len(count), 2)
        run_group(job, self.cfg, self.root / 'cache', self.auth,
                  lambda *args: request(*args, transport=lambda messages, *_: (json.dumps(response(json.loads(messages[1]['content'][0]['text']))), 'stop')))
        self.assertTrue((self.root / 'cache/task_000/scene_groups/t000_c001/final.json').exists())

    def test_schema_rejects_foreign_duplicate_and_bad_representative(self):
        obj = response({'round': 1, 'episodes': [{'episode_id': 'a'}, {'episode_id': 'b'}], 'representatives_per_cluster': 1})
        for key, values in [('member_episode_ids', ['a', 'x']), ('member_episode_ids', ['a', 'a']), ('representative_episode_ids', ['x'])]:
            bad = copy.deepcopy(obj)
            bad['groups'][0][key] = values
            with self.assertRaises(ValueError):
                validate_round1(bad, ['a', 'b'], 1)

    def test_uncertain_cannot_be_merged_or_promoted(self):
        candidates = {'g1': {'status': 'uncertain', 'member_episode_ids': ['a']},
                      'g2': {'status': 'singleton', 'member_episode_ids': ['b']}}
        bad = {'groups': [{'final_group_id': 'f', 'source_group_ids': ['g1', 'g2'],
                           'status': 'coherent', 'common_result': 'same', 'reason': 'same'}]}
        with self.assertRaises(ValueError):
            validate_round2(bad, candidates)
        bad['groups'][0].update(source_group_ids=['g1'], status='singleton')
        with self.assertRaises(ValueError):
            validate_round2(bad, {'g1': candidates['g1']})

    def test_invalid_source_assignments_fail_before_calls(self):
        path = self.root / 'images/task_000/final.json'
        data = json.loads(path.read_text())
        data['assignments'][0]['cluster_id'] = 'unknown'
        save(path, data)
        with self.assertRaises(ValueError):
            prepare(self.cfg)
        self.assertFalse(self.calls)

    def test_missing_eligible_annotation_fails(self):
        (self.root / 'annotations/ep_0_1_0/final.json').unlink()
        with self.assertRaises(FileNotFoundError):
            prepare(self.cfg)

    def test_preflight_has_no_output_or_api(self):
        plan = preflight_plan(prepare(self.cfg), self.cfg)
        self.assertEqual(sum(p['round1_requests'] for p in plan), 4)
        self.assertEqual(sum(p['round2_requests'] for p in plan), 2)
        self.assertFalse(Path(self.cfg['output_root']).exists())
        self.assertFalse(self.calls)

    def test_identity_rejects_changed_configuration(self):
        jobs = prepare(self.cfg)
        run_jobs(jobs, self.cfg, self.auth, self.caller)
        count = len(self.calls)
        self.cfg['shuffle_seed'] += 1
        with self.assertRaisesRegex(ValueError, 'changed'):
            run_jobs(prepare(self.cfg), self.cfg, self.auth, self.caller)
        self.assertEqual(count, len(self.calls))
        self.assertFalse((self.root / 'output/.run.lock').exists())

    def test_failure_does_not_drop_episodes_silently(self):
        def caller(*args):
            if args[2]['task_id'] == 1:
                raise RuntimeError('test failure')
            return self.caller(*args)
        summary = run_jobs(prepare(self.cfg), self.cfg, self.auth, caller)
        self.assertEqual(summary['status'], 'complete_with_failures')
        self.assertEqual(summary['episodes'], 7)
        self.assertEqual(summary['expected_episodes'], 10)
        self.assertEqual(summary['failed_parent_groups'], 1)

    def test_round2_limit_fails_without_extra_rounds(self):
        job = prepare(self.cfg)[1]
        def oversized(*args):
            obj = self.caller(*args)
            obj['groups'][0]['common_result'] = 'x' * self.cfg['max_request_chars']
            return obj
        with self.assertRaisesRegex(ValueError, 'Round 2 payload'):
            run_group(job, self.cfg, self.root / 'out', self.auth, oversized)
        self.assertEqual(len(self.calls), 2)

    def test_wrapper_uses_current_image_output(self):
        save(self.root / 'image.yaml', {'output_root': 'new-image-results'})
        save(self.root / 'pipeline.yaml', {'image_config': 'image.yaml', 'text_config': 'text.yaml'})
        result = commands(self.root / 'pipeline.yaml', 'all')
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1][-2:], ['--image-results-root', str(self.root / 'new-image-results')])
        self.assertNotIn('--image-results-root', commands(self.root / 'pipeline.yaml', 'text')[0])

    def test_wrapper_stops_after_image_failure(self):
        from ..run import main
        save(self.root / 'image.yaml', {'output_root': 'new-image-results'})
        save(self.root / 'pipeline.yaml', {'image_config': 'image.yaml', 'text_config': 'text.yaml'})
        with patch('sys.argv', ['run', '--config', str(self.root / 'pipeline.yaml')]), \
             patch('episode_clustering.vlm_clustering.run.subprocess.run') as call:
            call.return_value.returncode = 2
            with self.assertRaises(SystemExit) as exc:
                main()
            self.assertEqual(exc.exception.code, 2)
            self.assertEqual(call.call_count, 1)

    def test_scope_change_detects_modified_text(self):
        run_jobs(prepare(self.cfg), self.cfg, self.auth, self.caller)
        path = self.root / 'annotations/ep_0_1_0/final.json'
        data = json.loads(path.read_text())
        data['terminal_summary'] = 'Object outside container'
        save(path, data)
        with self.assertRaisesRegex(ValueError, 'changed'):
            run_jobs(prepare(self.cfg), self.cfg, self.auth, self.caller)


if __name__ == '__main__':
    unittest.main()
