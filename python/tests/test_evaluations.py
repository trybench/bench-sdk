import io
import json
import unittest
from unittest.mock import patch
from bench_sdk.evaluations import EvaluationClient
from bench_sdk.cli import gate

class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self,*args): self.close()

class EvaluationsTest(unittest.TestCase):
    def test_credentials_and_idempotency_use_expected_transport(self):
        client=EvaluationClient('http://localhost:8080','scoped',7)
        calls=[]
        def open(request,timeout):
            calls.append(request)
            return Response(json.dumps({'id':'stable-run'}).encode())
        with patch.object(client.opener,'open',side_effect=open):
            client.run('plan','start-key');client.resume('run','resume-key');client.cancel('run');client.events('run',12)
        self.assertEqual(calls[0].get_header('Authorization'),'Bearer scoped')
        self.assertEqual(calls[0].get_header('Idempotency-key'),'start-key')
        self.assertEqual(calls[1].get_header('Idempotency-key'),'resume-key')
        self.assertTrue(calls[3].full_url.endswith('/events?after=12'))
        self.assertEqual(calls[2].method,'POST')
    def test_no_remote_plaintext_credentials(self):
        with self.assertRaises(ValueError): EvaluationClient('http://example.com','secret',1)
    def test_gate_reports_decision_separately_from_job_completion(self):
        for status,decision,expected in [('completed','validated_improvement',0),('completed','regression',2),('completed','no_demonstrated_improvement',2),('completed','insufficient_evidence',3),('queued',None,3),('failed',None,1)]:
            self.assertEqual(gate({'status':status,'result':{'decision':decision}}),expected)
    def test_errors_do_not_turn_into_results(self):
        client=EvaluationClient('https://api.example','scoped',7)
        with patch.object(client.opener,'open',side_effect=OSError('offline')):
            with self.assertRaises(OSError): client.status('run')
