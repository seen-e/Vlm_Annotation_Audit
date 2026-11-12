import json
from pathlib import Path
import tempfile
import unittest

from .run import build_jobs, map_episodes, run_jobs, validate
from annotatiuon_audit.episode_level_filter.data import prepare, save_json


class TranslationTests(unittest.TestCase):
    def jobs(self):
        return build_jobs([
            {'episode_id': 'e0', 'tasks': ['Place a cup.']},
            {'episode_id': 'e1', 'tasks': ['Place a cup.']},
            {'episode_id': 'e2', 'tasks': ['Move a bowl.']}], [
            {'task_index': 0, '__index_level_0__': 'Place a cup.'},
            {'task_index': 1, '__index_level_0__': 'Move a bowl.'}])

    def config(self, root):
        return {'output_root': root, 'task_batchsize': 2, 'episode_manifest': 'episodes.json',
                'tasks_path': 'tasks.parquet', 'model': {'thinking': False, 'temperature': 0,
                'max_tokens': 2048, 'stream': True, 'attempts': 2, 'retry_delay_sec': 0}}

    def auth(self):
        return {'model': 'test', 'base_url': 'https://example.invalid', 'api_key': 'secret-test-key'}

    def test_exact_matching_and_deduplication(self):
        jobs = self.jobs()
        self.assertEqual(len(jobs), 2)
        self.assertEqual(sum(len(j['episodes']) for j in jobs), 3)
        with self.assertRaises(ValueError):
            build_jobs([{'episode_id': 'e0', 'tasks': ['place a cup.']}],
                       [{'task_index': 0, '__index_level_0__': 'Place a cup.'}])

    def test_bad_episode_mapping(self):
        task = [{'task_index': 0, '__index_level_0__': 'A'}]
        for rows in ([{'episode_id': '../a', 'tasks': ['A']}],
                     [{'episode_id': 'a', 'tasks': ['A', 'B']}],
                     [{'episode_id': 'a', 'task_id': 9, 'tasks': ['A']}],
                     [{'episode_id': 'a', 'tasks': ['A']}] * 2):
            with self.assertRaises(ValueError):
                build_jobs(rows, task)

    def test_duplicate_text_requires_task_id(self):
        tasks = [{'task_index': i, '__index_level_0__': 'A'} for i in (0, 1)]
        with self.assertRaises(ValueError):
            build_jobs([{'episode_id': 'e', 'tasks': ['A']}], tasks)
        jobs = build_jobs([{'episode_id': f'e{i}', 'task_index': i, 'tasks': ['A']} for i in (0, 1)], tasks)
        self.assertEqual(len(jobs), 1)
        self.assertEqual([e['task_id'] for e in jobs[0]['episodes']], [0, 1])

    def test_output_schema(self):
        for result in ({}, {'instruction_zh': ''}, {'instruction_zh': 'Only English'},
                       {'instruction_zh': '放杯子', 'extra': 1}):
            with self.assertRaises(ValueError):
                validate(result)
        validate({'instruction_zh': '放置杯子。'})

    def test_run_cache_mapping_and_provenance(self):
        calls = []

        def transport(messages, cfg, auth):
            payload = json.loads(messages[1]['content'])
            self.assertEqual(set(payload), {'instruction'})
            calls.append(payload)
            return json.dumps({'instruction_zh': '放置杯子。' if payload['instruction'].startswith('Place') else '移动碗。'}), 'stop'

        with tempfile.TemporaryDirectory() as root:
            cfg = self.config(root)
            self.assertTrue(run_jobs(self.jobs(), cfg, self.auth(), transport)['all_succeeded'])
            self.assertEqual(len(calls), 2)
            data = json.loads((Path(root) / 'episode_review_standard.json').read_text())
            self.assertEqual(data['episode_count'], 3)
            self.assertEqual(data['episodes']['e0']['review_standard'], '放置杯子。')
            self.assertEqual(data['episodes']['e1']['review_standard'], '放置杯子。')
            self.assertEqual(data['episodes']['e2']['review_standard'], '移动碗。')
            self.assertEqual(run_jobs(self.jobs(), cfg, self.auth(), transport)['cached'], 2)
            self.assertEqual(len(calls), 2)
            cfg['model']['thinking'] = True
            with self.assertRaises(ValueError):
                run_jobs(self.jobs(), cfg, self.auth(), transport)
            self.assertEqual(len(calls), 2)
            for path in Path(root).rglob('*.json'):
                self.assertNotIn(self.auth()['api_key'], path.read_text())

    def test_failed_request_retries_and_resume(self):
        jobs = self.jobs()[:1]
        with tempfile.TemporaryDirectory() as root:
            cfg = self.config(root)
            def broken(*args):
                return '{"instruction_zh":"测试"}', 'length'
            result = run_jobs(jobs, cfg, self.auth(), broken)
            self.assertEqual(result['failed'], 1)
            self.assertFalse((Path(root) / 'episode_review_standard.json').exists())
            folder = Path(root) / 'translations' / jobs[0]['translation_id']
            self.assertEqual(len(list(folder.glob('attempt_*'))), 2)
            result = run_jobs(jobs, cfg, self.auth(), lambda *a: ('{"instruction_zh":"测试"}', 'stop'))
            self.assertTrue(result['all_succeeded'])
            self.assertEqual(len(list(folder.glob('attempt_*'))), 3)

    def test_lock_blocks_runner(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / '.run.lock').mkdir()
            with self.assertRaises(RuntimeError):
                run_jobs(self.jobs(), self.config(root), self.auth(), lambda *a: self.fail('API called'))

    def test_missing_translation_never_partially_maps(self):
        with self.assertRaises(KeyError):
            map_episodes(self.jobs(), {})

    def test_existing_filter_reads_generated_mapping(self):
        jobs = self.jobs()
        mapped = map_episodes(jobs, {j['translation_id']: {'instruction_zh': '测试要求。'} for j in jobs})
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            save_json(root / 'standards.json', {'episodes': mapped})
            for eid in mapped:
                save_json(root / 'annotations' / eid / 'final.json',
                          {'episode_id': eid, 'subtasks': [{'segment_summary': '测试动作'}]})
            loaded = prepare({'standards_path': str(root / 'standards.json'),
                              'annotations_root': str(root / 'annotations'),
                              'annotation_pattern': '{episode_id}/final.json', 'input_workers': 2})
            self.assertEqual(len(loaded), 3)
            self.assertTrue(all(j['review_standard'] == '测试要求。' for j in loaded))


if __name__ == '__main__':
    unittest.main()
