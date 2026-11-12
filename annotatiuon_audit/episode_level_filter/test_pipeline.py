import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from .data import prepare, read_json, save_json
from .run import main, review_one, run_jobs
from .schema import validate
from . import prompts


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.job = {'episode_id': 'episode_000000', 'task': 'test', 'review_standard': 'place object',
                    'annotation_path': 'fixture/final.json',
                    'final_annotation': {'subtasks': [{'subtask_index': 9, 'segment_summary': 'placed object'}]}}
        self.api = dict(thinking=False, temperature=0, max_tokens=1000, stream=False,
                        attempts=2, timeout_sec=10, retry_delay_sec=0)
        self.auth = dict(model='mock', base_url='http://unused.invalid', api_key='TEST_SECRET')
        self.cfg = dict(model=self.api, episode_batchsize=2, output_root=str(self.root / 'out'))

    def response(self, messages, cfg, auth):
        payload = json.loads(messages[1]['content'])
        return json.dumps(dict(episode_id=payload['episode_id'], review_status='normal', reason='fixture', issues=[])), 'stop'

    def test_unapproved_prompt_blocks_requests(self):
        self.assertTrue(prompts.PROMPT.strip())
        with patch.object(prompts, 'APPROVED', False), patch('sys.argv', ['run']), \
                patch('annotatiuon_audit.episode_level_filter.run.load_config', return_value=self.cfg), \
                patch('annotatiuon_audit.episode_level_filter.run.prepare') as prepare_mock:
            with self.assertRaises(SystemExit):
                main()
            prepare_mock.assert_not_called()

    def test_cache_and_identity(self):
        folder = self.root / 'one'
        self.assertFalse(review_one(folder, self.job, 'fixture', self.api, self.auth, self.response)[1])
        def forbidden(*args):
            self.fail('Cached review must not request API')
        self.assertTrue(review_one(folder, self.job, 'fixture', self.api, self.auth, forbidden)[1])
        with self.assertRaises(ValueError):
            review_one(folder, self.job, 'changed', self.api, self.auth, forbidden)
        self.assertNotIn('TEST_SECRET', (folder / 'input.json').read_text())

    def test_truncation_retry_and_invalid_cache(self):
        calls = []
        def transport(*args):
            calls.append(1)
            raw, finish = self.response(*args)
            return raw, 'length' if len(calls) == 1 else finish
        folder = self.root / 'one'
        review_one(folder, self.job, 'fixture', self.api, self.auth, transport)
        self.assertEqual(len(calls), 2)
        save_json(folder / 'output.json', {})
        self.assertFalse(review_one(folder, self.job, 'fixture', self.api, self.auth, self.response)[1])
        self.assertEqual(len(list(folder.glob('invalid_output_*.json'))), 1)

    def test_evidence_and_subtask_identity(self):
        issue = dict(issue_type='annotation_conflict', severity='anomaly', criterion=None,
                     reason='fixture', affected_subtasks=[9], video_check='fixture',
                     evidence=[{'path': '/subtasks/0/segment_summary', 'quote': 'placed object'}])
        result = dict(episode_id=self.job['episode_id'], review_status='anomaly', reason='fixture', issues=[issue])
        validate(result, self.job)
        issue['affected_subtasks'] = [0]
        with self.assertRaises(ValueError):
            validate(result, self.job)
        issue['affected_subtasks'] = [9]
        issue['evidence'][0]['quote'] = 'invented'
        with self.assertRaises(ValueError):
            validate(result, self.job)

    def test_prepare_and_missing_standard(self):
        cfg = dict(standards_path=str(self.root / 'standards.json'), annotations_root=str(self.root / 'src'),
                   annotation_pattern='{episode_id}/final.json', episode_ids=[])
        save_json(cfg['standards_path'], {'episodes': {self.job['episode_id']: {'review_standard': 'place object'}}})
        save_json(self.root / 'src' / self.job['episode_id'] / 'final.json', self.job['final_annotation'])
        self.assertEqual(prepare(cfg)[0]['final_annotation'], self.job['final_annotation'])
        cfg['input_workers'] = 3
        for n in range(1, 4):
            save_json(self.root / 'src' / f'episode_{n:06d}' / 'final.json', self.job['final_annotation'])
        rows = {f'episode_{n:06d}': {'review_standard': 'place object'} for n in range(4)}
        save_json(cfg['standards_path'], {'episodes': rows})
        progress = []
        self.assertEqual([j['episode_id'] for j in prepare(cfg, lambda n, total: progress.append((n, total)))], list(rows))
        self.assertEqual(progress[-1], (4, 4))
        save_json(cfg['standards_path'], {'episodes': {self.job['episode_id']: {'review_standard': ''}}})
        with self.assertRaises(ValueError):
            prepare(cfg)

    def test_concurrency_failure_and_resume(self):
        jobs = [dict(copy.deepcopy(self.job), episode_id=f'episode_{n:06d}') for n in range(5)]
        active = 0
        maximum = 0
        guard = threading.Lock()
        def transport(*args):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            try:
                time.sleep(0.02)
                if json.loads(args[0][1]['content'])['episode_id'] == 'episode_000001':
                    raise RuntimeError('TEST_SECRET temporary failure')
                return self.response(*args)
            finally:
                with guard:
                    active -= 1
        progress = run_jobs(jobs, self.cfg, self.auth, 'fixture', transport)
        self.assertEqual((progress['completed'], progress['failed']), (4, 1))
        self.assertEqual(maximum, 2)
        self.assertNotIn('TEST_SECRET', (self.root / 'out' / 'failures.json').read_text())
        progress = run_jobs(jobs, self.cfg, self.auth, 'fixture', self.response)
        self.assertEqual((progress['completed'], progress['cached'], progress['failed']), (5, 4, 0))
        self.assertTrue(read_json(self.root / 'out' / 'progress.json')['all_succeeded'])
        self.assertFalse((self.root / 'out' / '.run.lock').exists())

    def test_changed_run_rejected_before_requests(self):
        run_jobs([self.job], self.cfg, self.auth, 'fixture', self.response)
        changed = dict(self.job, review_standard='new standard')
        with self.assertRaises(ValueError):
            run_jobs([changed], self.cfg, self.auth, 'fixture', lambda *a: self.fail('Unexpected request'))


if __name__ == '__main__':
    unittest.main()
