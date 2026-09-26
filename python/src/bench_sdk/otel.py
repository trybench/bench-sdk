"""OpenTelemetry bridge: forward spans from any instrumented framework to Bench.

Frameworks such as OpenAI Agents, Strands, LangChain/LangGraph, Pydantic AI,
CrewAI, LlamaIndex, Google ADK and AutoGen already describe what ran through
OpenTelemetry GenAI spans. Attach this exporter and Bench receives the same
agent/model/tool tree, in metadata-only form, without per-framework adapters.
No paid evaluation starts from receiving spans.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from .client import Bench


def _iso(nanoseconds: int | None) -> str:
    if not nanoseconds:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return datetime.fromtimestamp(nanoseconds / 1e9, timezone.utc).isoformat().replace("+00:00", "Z")


def span_to_bench(span: Any) -> dict:
    """Convert a finished OpenTelemetry ReadableSpan to record_external_span arguments."""
    context = span.get_span_context() if hasattr(span, "get_span_context") else span.context
    parent = getattr(span, "parent", None)
    attributes = dict(getattr(span, "attributes", None) or {})
    kind = attributes.pop("bench.kind", None)
    status = getattr(getattr(span, "status", None), "status_code", None)
    model = attributes.get("gen_ai.request.model")
    return {
        "trace_id": format(context.trace_id, "032x"),
        "span_id": format(context.span_id, "016x"),
        "parent_span_id": format(parent.span_id, "016x") if parent is not None and getattr(parent, "span_id", 0) else None,
        "name": span.name,
        "kind": kind if isinstance(kind, str) else None,
        "started_at": _iso(getattr(span, "start_time", None)),
        "ended_at": _iso(getattr(span, "end_time", None)),
        "status": "error" if getattr(status, "name", "") == "ERROR" else "ok",
        "attributes": attributes,
        "model": model if isinstance(model, str) else None,
    }


def _result(ok: bool) -> Any:
    try:
        from opentelemetry.sdk.trace.export import SpanExportResult
    except ImportError:  # the bridge is also usable from tests without the OpenTelemetry SDK
        return 0 if ok else 1
    return SpanExportResult.SUCCESS if ok else SpanExportResult.FAILURE


class BenchSpanExporter:
    """An opentelemetry.sdk.trace.export.SpanExporter that records through a Bench client."""

    def __init__(self, bench: Bench):
        self._bench = bench

    def export(self, spans: Sequence[Any]) -> Any:
        for span in spans:
            try:
                self._bench.record_external_span(**span_to_bench(span))
            except Exception:
                pass  # the application's tracer must never fail because of Bench
        return _result(True)

    def shutdown(self) -> None:
        self._bench.flush()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        self._bench.flush()
        return True


def attach(bench: Bench, provider: Any = None) -> Any:
    """Register the exporter on the global (or given) OpenTelemetry tracer provider.

    Requires the opentelemetry-sdk package, which instrumented frameworks already
    bring. Returns the provider. Spans are exported as they end; the Bench client
    batches delivery itself.
    """
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    provider = provider or trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    provider.add_span_processor(SimpleSpanProcessor(BenchSpanExporter(bench)))
    return provider
