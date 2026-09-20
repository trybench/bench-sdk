import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bench_sdk import Bench


class ClientTests(unittest.TestCase):
    def test_shared_privacy_contract_in_outgoing_payload(self):
        fixture = json.loads((Path(__file__).resolve().parent / "fixtures/privacy.json").read_text())
        client = self.client(capture_content=True)
        with client.trace("privacy-contract", input=fixture["input"]):
            pass
        client.shutdown()
        raw = json.loads(self.requests[0][2])["traces"][0]["spans"][0]["input_value"]
        for value in fixture["forbidden"]:
            self.assertNotIn(value, raw)
        for value in fixture["preserved"]:
            self.assertIn(value, raw)
        self.assertEqual(json.loads(raw)["count"], 42)

    def client(self, **options):
        self.requests = []
        def send(url, headers, body, timeout):
            self.requests.append((url, headers, body, timeout))
            return 201
        return Bench(api_key="bench_sk_synthetic_test_key", repository="fixture/python", branch="main",
                     environment="staging", transport=options.pop("transport", send), **options)

    def test_metadata_default_keeps_counts_without_capturing_content(self):
        client = self.client()
        with client.trace("request", input="customer@example.test", attributes={"private_note": "secret", "gen_ai.usage.input_tokens": 42}) as span:
            span.set_output("private answer")
        client.shutdown()
        body = json.loads(self.requests[0][2])
        row = body["traces"][0]["spans"][0]
        self.assertNotIn("input_value", row)
        self.assertNotIn("output_value", row)
        self.assertEqual(row["attributes"]["bench.environment"], "staging")
        self.assertEqual(row["attributes"]["gen_ai.usage.input_tokens"], 42)
        self.assertGreaterEqual(row["attributes"]["bench.duration_ms"], 0)
        self.assertEqual(len(row["attributes"]), 3)
        self.assertNotIn("customer", str(body))

    def test_nested_concurrent_tasks_keep_the_correct_parent(self):
        client = self.client()
        async def child(i):
            with client.trace(f"tool-{i}", kind="TOOL") as span:
                await asyncio.sleep(0)
                span.set_output(i)
        async def run():
            with client.trace("agent", kind="AGENT") as span:
                await asyncio.gather(*(child(i) for i in range(5)))
                span.set_output("done")
            await client.aflush()
        asyncio.run(run())
        traces = json.loads(self.requests[0][2])["traces"]
        self.assertEqual(len({t["trace_id"] for t in traces}), 1)
        rows = [t["spans"][0] for t in traces]
        root = next(r for r in rows if r["name"] == "agent")
        self.assertEqual(len({r["span_id"] for r in rows}), 6)
        self.assertTrue(all(r.get("parent_span_id") == root["span_id"] for r in rows if r is not root))

    def test_sensitive_values_are_removed_before_transport(self):
        client = self.client(capture_content=True)
        with client.trace("request", input={"card_number": 4242424242424242, "nested": '{"full_name":"Private Person"}', "note": "person@example.test +49 151 12345678 192.168.0.1 4242-4242-4242-4242", "count": 3}) as span:
            span.set_output("Bearer abcdefghijklmnop")
        client.flush()
        body = self.requests[0][2].decode()
        for value in ["4242", "Private Person", "person@example.test", "12345678", "192.168.0.1", "abcdefghijklmnop"]:
            self.assertNotIn(value, body)
        self.assertIn("REDACTED", body)
        self.assertEqual(json.loads(json.loads(body)["traces"][0]["spans"][0]["input_value"])["count"], 3)

    def test_failures_keep_app_error_and_retries_keep_event_ids(self):
        bodies = []
        def fail(url, headers, body, timeout):
            bodies.append(body)
            return 503
        client = self.client(transport=fail)
        original = RuntimeError("application failure")
        with self.assertRaises(RuntimeError) as caught:
            with client.trace("request"):
                raise original
        self.assertIs(caught.exception, original)
        client.flush()
        self.assertEqual(len(bodies), 2)
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(json.loads(bodies[0])["traces"][0]["spans"][0]["status"], "error")
        self.assertEqual(client.stats, {"queued": 0, "dropped": 1})

    def test_queue_sampling_and_redactor_failure(self):
        client = self.client(max_queue_size=1)
        for _ in range(3):
            with client.trace("request"):
                pass
        self.assertEqual(client.stats, {"queued": 1, "dropped": 2})
        zero = self.client(sample_rate=0)
        with zero.trace("off"):
            pass
        self.assertEqual(zero.stats["queued"], 0)
        def broken(value):
            raise RuntimeError("redactor private error")
        client = self.client(redact=broken)
        with client.trace("request"):
            result = 42
        self.assertEqual(result, 42)
        self.assertEqual(client.stats["dropped"], 1)

    def test_real_http_delivers_and_refuses_redirect(self):
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append((self.path, self.headers.get("Authorization"), self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(302 if len(seen) == 1 else 201)
                self.send_header("Location", "/must-not-follow")
                self.end_headers()
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = Bench(api_key="bench_sk_synthetic_test_key", repository="fixture/python", branch="main", endpoint=f"http://127.0.0.1:{server.server_port}")
            with client.trace("request"):
                pass
            client.flush()
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0][0], "/api/traces")
            self.assertEqual(client.stats["dropped"], 1)
            with client.trace("accepted"):
                pass
            client.flush()
            self.assertEqual(len(seen), 2)
            self.assertEqual(json.loads(seen[1][2])["repo_full_name"], "fixture/python")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
