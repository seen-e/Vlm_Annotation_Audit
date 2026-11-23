import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from ..correction.client import parse_json_response, recover_fenced_response, request
from ..correction.io import save, read


class JsonResponseTests(unittest.TestCase):
    def test_plain_and_fenced_json(self):
        for text in ['{"x": 1}', '\n\n```json\n{"x": 1}\n```',
                     '```\n{"x": 1}\n```', '```JSON\r\n{"x": 1}\r\n```']:
            self.assertEqual(parse_json_response(text), {'x': 1})

    def test_rejects_prose_truncation_multiple_blocks_and_invalid_json(self):
        for text in ['Explanation\n```json\n{}\n```', '```json\n{}',
                     '```json\n{}\n```\nextra', '```json\n{}\n```\n```json\n{}\n```',
                     '```json\n{"x":}\n```']:
            with self.assertRaises(ValueError):
                parse_json_response(text)

    def seed(self, folder, finish='stop', output='```json\n{"x": 1}\n```', error='JSONDecodeError'):
        save(folder/'input.json', {'request_fingerprint': 'same'})
        save(folder/'attempt_001'/'response.json', {'content': output, 'finish_reason': finish})
        save(folder/'attempt_001'/'error.json', {'error_type': error})

    def test_recovers_with_provenance_and_keeps_raw(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.seed(folder)
            before = (folder/'attempt_001'/'response.json').read_bytes()
            result = recover_fenced_response(folder, 'same', lambda x: None)
            self.assertEqual(result['output'], {'x': 1})
            self.assertFalse(result['recovery']['api_called'])
            self.assertEqual(before, (folder/'attempt_001'/'response.json').read_bytes())

    def test_recovery_still_enforces_schema(self):
        def invalid(value):
            raise ValueError('Missing required fields')
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.seed(folder)
            self.assertIsNone(recover_fenced_response(folder, 'same', invalid))

    def test_recovery_rejects_changed_fingerprint(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.seed(folder)
            with self.assertRaises(ValueError):
                recover_fenced_response(folder, 'different', lambda x: None)

    def test_recovery_rejects_truncation_and_non_parser_errors(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.seed(folder, finish='length')
            self.assertIsNone(recover_fenced_response(folder, 'same', lambda x: None))
            self.seed(folder, error='ValueError')
            self.assertIsNone(recover_fenced_response(folder, 'same', lambda x: None))

    def test_live_request_parses_fence_without_retry(self):
        calls = []
        def complete(*args):
            calls.append(args)
            return '\n\n```json\n{"x": 1}\n```', 'stop'
        client = SimpleNamespace(auth={'model': 'test', 'base_url': 'test'},
                                 cfg={'temperature': 0, 'max_tokens': 100, 'stream': True,
                                      'attempts': 3, 'retry_delay_sec': 0}, complete=complete)
        with TemporaryDirectory() as tmp:
            result = request(client, 'prompt', {}, [], {}, tmp, lambda x: None)
            self.assertEqual(result, {'x': 1})
            self.assertEqual(len(calls), 1)

    def test_request_recovers_without_calling_api(self):
        def complete(*args):
            return '```json\n{"x": 1}\n```', 'stop'
        client = SimpleNamespace(auth={'model': 'test', 'base_url': 'test'},
                                 cfg={'temperature': 0, 'max_tokens': 100, 'stream': True,
                                      'attempts': 1, 'retry_delay_sec': 0}, complete=complete)
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            request(client, 'prompt', {}, [], {}, folder, lambda x: None)
            (folder/'result.json').unlink()
            save(folder/'attempt_001'/'error.json', {'error_type': 'JSONDecodeError'})
            def forbidden(*args):
                raise AssertionError('API must not be called for recovered result')
            client.complete = forbidden
            self.assertEqual(request(client, 'prompt', {}, [], {}, folder, lambda x: None), {'x': 1})
            self.assertFalse(read(folder/'result.json')['recovery']['api_called'])


if __name__ == '__main__':
    unittest.main()
