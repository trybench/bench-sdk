import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from bench_sdk import BenchPlatform, PlatformError

class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.status, self.response, self.content_type = 200, b'{"ok":true}', 'application/json'
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def run_request(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                owner.calls.append((self.command, self.path, dict(self.headers), body))
                self.send_response(owner.status)
                self.send_header('Content-Type', owner.content_type)
                if owner.status == 302:
                    self.send_header('Location', 'http://127.0.0.1:1/secret')
                self.end_headers()
                self.wfile.write(owner.response)
            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = run_request
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = BenchPlatform('bench_sk_fixture', endpoint='http://127.0.0.1:' + str(self.server.server_port))
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
    def test_all_operations_route_with_correct_authority(self):
        for op in self.client.operations()['operations']:
            with self.subTest(op=op['id']):
                path = {key: '42' for key in op['path_parameters']}
                self.assertEqual(self.client.call(op['id'], path=path), {'ok': True})
                method, url, headers, _ = self.calls[-1]
                expected = op['path']
                for key in path:
                    expected = expected.replace('{' + key + '}', '42')
                self.assertEqual((method, url), (op['method'], expected))
                self.assertEqual(headers.get('Authorization'), None if op['auth'] == 'public' else 'Bearer bench_sk_fixture')
    def test_upload_and_query(self):
        self.client.call('upload_dataset', path={'id': 1}, form={'content_consent': True, 'mapping': {'input': 'q'}}, files=[{'name':'cases.xlsx', 'content':b'\x00\xff\x80'}])
        body = self.calls[-1][3]
        self.assertIn(b'\x00\xff\x80', body)
        self.assertIn(b'{"input":"q"}', body)
        self.client.call('get_scan', path={'owner':'test', 'repo':'repo'}, query={'branch':'feature/a&b'})
        self.assertIn('branch=feature%2Fa%26b', self.calls[-1][1])
    def test_structured_error_no_retry(self):
        self.status, self.response = 403, b'{"error":{"code":"upgrade_required","message":"Growth required","reference":"BENCH-TEST","pricing_url":"https://stg.usebench.ai/plans","upgrade_url":"https://stg.usebench.ai/plans","payment_confirmation_required":true}}'
        with self.assertRaises(PlatformError) as caught:
            self.client.call('whoami')
        self.assertEqual(caught.exception.code, 'upgrade_required')
        self.assertEqual(caught.exception.reference, 'BENCH-TEST')
        self.assertEqual(caught.exception.details['upgrade_url'], 'https://stg.usebench.ai/plans')
        self.assertTrue(caught.exception.details['payment_confirmation_required'])
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(len(self.calls), 1)
    def test_stream_and_stream_error(self):
        self.content_type, self.response = 'application/x-ndjson', b'{"type":"progress"}\n{"type":"done"}\n'
        self.assertEqual(len(self.client.call('whoami')), 2)
        self.response = b'{"type":"error","message":"failed"}\n'
        with self.assertRaises(PlatformError):
            self.client.call('whoami')
    def test_redirect_is_not_followed(self):
        self.status = 302
        with self.assertRaises(PlatformError):
            self.client.call('whoami')
        self.assertEqual(len(self.calls), 1)
    def test_invalid_inputs(self):
        for value in ['..', '%2fadmin', 'a/b', 'a\\b', 'a\n']:
            with self.assertRaises(ValueError):
                self.client.call('get_system', path={'id': value})
        with self.assertRaises(ValueError):
            self.client.call('no_such_operation')
        self.assertEqual(self.calls, [])
        for endpoint in ['http://external.invalid', 'https://key:secret@example.com', 'https://api.example.com/?key=secret']:
            with self.assertRaises(ValueError):
                BenchPlatform(endpoint=endpoint)
