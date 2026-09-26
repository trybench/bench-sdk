import json
import unittest
from types import SimpleNamespace

from bench_sdk import Bench, BenchSpanExporter, infer_span_kind
from bench_sdk.otel import span_to_bench


def fake_span(trace_id, span_id, name, attributes, parent=None, error=False):
    return SimpleNamespace(
        name=name,
        attributes=attributes,
        parent=SimpleNamespace(span_id=parent) if parent else None,
        start_time=1_758_000_000_000_000_000,
        end_time=1_758_000_000_500_000_000,
        status=SimpleNamespace(status_code=SimpleNamespace(name="ERROR" if error else "OK")),
        get_span_context=lambda: SimpleNamespace(trace_id=trace_id, span_id=span_id),
    )


class OpenTelemetryBridgeTest(unittest.TestCase):
    def setUp(self):
        self.bodies = []

        def transport(url, headers, body, timeout):
            self.bodies.append((url, json.loads(body)))
            return 201

        self.bench = Bench(api_key="bench_sk_test", repository="acme/support", branch="main", system_name="Customer support",
                           endpoint="http://127.0.0.1:55021", transport=transport)

    def test_kind_inference_follows_genai_conventions(self):
        self.assertEqual(infer_span_kind({"gen_ai.operation.name": "invoke_agent"}), "AGENT")
        self.assertEqual(infer_span_kind({"gen_ai.agent.name": "Triage"}), "AGENT")
        self.assertEqual(infer_span_kind({"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "lookup"}), "TOOL")
        self.assertEqual(infer_span_kind({"gen_ai.operation.name": "chat", "gen_ai.request.model": "gpt-4.1"}), "LLM")
        self.assertEqual(infer_span_kind({"gen_ai.request.model": "claude-sonnet-4-5"}), "LLM")
        self.assertEqual(infer_span_kind({"gen_ai.operation.name": "embeddings"}), "EMBEDDING")
        self.assertEqual(infer_span_kind({"http.method": "GET"}), "UNKNOWN")
        # Pydantic AI stamps the agent name on model and tool spans too.
        self.assertEqual(infer_span_kind({"gen_ai.operation.name": "chat", "gen_ai.agent.name": "support_agent", "gen_ai.request.model": "claude"}), "LLM")
        self.assertEqual(infer_span_kind({"gen_ai.operation.name": "execute_tool", "gen_ai.agent.name": "support_agent", "gen_ai.tool.name": "balance"}), "TOOL")
        self.assertEqual(infer_span_kind({"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "a", "gen_ai.request.model": "claude"}), "AGENT")

    def test_framework_spans_arrive_as_one_tree_with_metadata_only(self):
        trace = 0x0000000000000000000000000000ab01
        exporter = BenchSpanExporter(self.bench)
        result = exporter.export([
            fake_span(trace, 0x1, "invoke_agent Triage", {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "Triage agent", "customer.email": "x@example.com"}),
            fake_span(trace, 0x2, "chat gpt-4.1-mini", {"gen_ai.operation.name": "chat", "gen_ai.request.model": "gpt-4.1-mini", "gen_ai.provider.name": "openai", "gen_ai.usage.input_tokens": 12, "prompt": "secret content"}, parent=0x1),
            fake_span(trace, 0x3, "execute_tool lookup_order", {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "lookup_order"}, parent=0x1, error=True),
        ])
        self.assertEqual(getattr(result, "value", result), 0)
        self.bench.flush()
        self.assertEqual(len(self.bodies), 1)
        url, body = self.bodies[0]
        self.assertEqual(url, "http://127.0.0.1:55021/api/traces")
        self.assertEqual(body["system_name"], "Customer support")
        self.assertFalse(body["capture_content"])
        spans = {s["span_id"]: s for t in body["traces"] for s in t["spans"]}
        self.assertEqual({t["trace_id"] for t in body["traces"]}, {"0000000000000000000000000000ab01"})
        self.assertEqual(spans["0000000000000001"]["kind"], "AGENT")
        self.assertNotIn("parent_span_id", spans["0000000000000001"])
        self.assertEqual(spans["0000000000000001"]["attributes"]["gen_ai.agent.name"], "Triage agent")
        self.assertNotIn("customer.email", spans["0000000000000001"]["attributes"])
        self.assertEqual(spans["0000000000000002"]["kind"], "LLM")
        self.assertEqual(spans["0000000000000002"]["parent_span_id"], "0000000000000001")
        self.assertEqual(spans["0000000000000002"]["model_name"], "gpt-4.1-mini")
        self.assertEqual(spans["0000000000000002"]["attributes"]["gen_ai.usage.input_tokens"], 12)
        self.assertNotIn("prompt", spans["0000000000000002"]["attributes"])
        self.assertNotIn("input_value", spans["0000000000000002"])
        self.assertEqual(spans["0000000000000003"]["kind"], "TOOL")
        self.assertEqual(spans["0000000000000003"]["status"], "error")
        self.assertEqual(spans["0000000000000003"]["started_at"], "2025-09-16T05:20:00Z")

    def test_invalid_external_identifiers_are_dropped_not_raised(self):
        self.bench.record_external_span(trace_id="not-hex", span_id="0000000000000001", name="x", started_at="2026-01-01T00:00:00Z", ended_at="2026-01-01T00:00:01Z")
        self.assertEqual(self.bench.stats["dropped"], 1)
        self.assertEqual(self.bench.stats["queued"], 0)

    def test_explicit_bench_kind_attribute_wins(self):
        converted = span_to_bench(fake_span(0xab, 0x9, "step", {"bench.kind": "CHAIN", "gen_ai.request.model": "gpt-4.1"}))
        self.assertEqual(converted["kind"], "CHAIN")
        self.assertNotIn("bench.kind", converted["attributes"])


    def test_attach_to_a_real_opentelemetry_tracer_provider(self):
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.trace import TracerProvider
        except ImportError:
            self.skipTest("opentelemetry-sdk not installed")
        from bench_sdk.otel import attach
        provider = attach(self.bench, TracerProvider())
        tracer = provider.get_tracer("framework")
        with tracer.start_as_current_span("invoke_agent Support", attributes={"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "Support agent"}):
            with tracer.start_as_current_span("chat", attributes={"gen_ai.operation.name": "chat", "gen_ai.request.model": "gpt-4.1", "gen_ai.provider.name": "openai"}):
                pass
            with tracer.start_as_current_span("execute_tool refund", attributes={"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "issue_refund"}) as tool:
                tool.set_status(trace.StatusCode.ERROR, "declined")
        self.bench.flush()
        spans = [s for t in self.bodies[0][1]["traces"] for s in t["spans"]]
        by_name = {s["name"]: s for s in spans}
        self.assertEqual(by_name["invoke_agent Support"]["kind"], "AGENT")
        self.assertEqual(by_name["chat"]["kind"], "LLM")
        self.assertEqual(by_name["chat"]["parent_span_id"], by_name["invoke_agent Support"]["span_id"])
        self.assertEqual(by_name["execute_tool refund"]["status"], "error")
        self.assertEqual(len({t["trace_id"] for t in self.bodies[0][1]["traces"]}), 1)


if __name__ == "__main__":
    unittest.main()
