import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from run import payload_for, validate, merge, process, save, read


class Tests(unittest.TestCase):
    def setUp(self):
        self.original = dict(episode_id='e1', task='Put cup under tap', subtasks=[
            dict(subtask_id='s1', segment_summary='Move cup', start_time_sec=0),
            dict(subtask_id='s2', segment_summary='Wait', start_time_sec=1)])
        self.payload = payload_for(self.original, 'e1')
        self.result = dict(episode_id='e1', subtasks=[
            dict(subtask_index=0, subtask_id='s1', reasoning='Position cup for filling.', reasoning_unavailable_reason=None),
            dict(subtask_index=1, subtask_id='s2', reasoning=None, reasoning_unavailable_reason='Waiting trigger missing.')])

    def test_valid_and_preserve(self):
        validate(self.result, self.payload)
        merged = merge(self.original, self.result)
        for t in merged['subtasks']:
            t.pop('reasoning')
            t.pop('reasoning_unavailable_reason')
        self.assertEqual(merged, self.original)

    def test_reject_misalignment(self):
        for field, value in [('subtask_index', 1), ('subtask_id', 'wrong'), ('reasoning', None), ('reasoning_unavailable_reason', 'also present')]:
            r = copy.deepcopy(self.result)
            r['subtasks'][0][field] = value
            with self.assertRaises(ValueError):
                validate(r, self.payload)

    def test_count(self):
        self.result['subtasks'].pop()
        with self.assertRaises(ValueError):
            validate(self.result, self.payload)

    def test_old_reason_removed(self):
        self.original['subtasks'][0]['reasoning'] = 'old'
        self.assertNotIn('reasoning', payload_for(self.original, 'e1')['annotation']['subtasks'][0])

    def test_missing_id(self):
        self.original['subtasks'][0].pop('subtask_id')
        self.result['subtasks'][0]['subtask_id'] = None
        validate(self.result, payload_for(self.original, 'e1'))

    def test_process_resume_retry_and_identity(self):
        cfg = dict(model=dict(thinking=True, temperature=0, max_tokens=8192, timeout_sec=10),
                   runtime=dict(attempts=2, retry_delay_sec=0))
        api = dict(base_url='http://localhost', api_key='secret', model='mock')
        response = SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
            message=SimpleNamespace(content=json.dumps(self.result)))], model_dump=lambda: {'mock': True})
        with tempfile.TemporaryDirectory() as tmp, patch('run.OpenAI') as client:
            source, dest = Path(tmp)/'input.json', Path(tmp)/'out'
            save(source, self.original)
            call = client.return_value.__enter__.return_value.chat.completions.create
            call.side_effect = [RuntimeError('timeout secret'), response]
            self.assertFalse(process(source, dest, cfg, api)['reused'])
            self.assertEqual(call.call_count, 2)
            self.assertEqual(read(source), self.original)
            self.assertNotIn('secret', (dest/'attempt_001/error.json').read_text())
            self.assertTrue(process(source, dest, cfg, api)['reused'])
            self.assertEqual(call.call_count, 2)
            cfg['model']['thinking'] = False
            with self.assertRaises(ValueError):
                process(source, dest, cfg, api)


if __name__ == '__main__':
    unittest.main()
