# Changelog

## 0.2.1 (beta)

- Default API endpoint is `https://api.usebench.ai` in every client. The previous default pointed at a host Bench no longer controls; set `endpoint` explicitly if you pin an older version.
- READMEs describe Bench as evaluating and improving AI systems.

## 0.2.0 (beta)

- OpenTelemetry bridge (`BenchSpanExporter`, Python `bench_sdk.otel.attach`) and `record_external_span` /
  `RecordExternalSpan` in all four clients, so frameworks that already emit GenAI spans draw the whole
  system in Bench without code changes.
- Span kind inference from OpenTelemetry GenAI attributes (`gen_ai.operation.name` first).
- Failed spans keep their status description and exception type and message, never the stack trace.
- Platform client with the synchronized operation catalog, including `register_prompts`.
- Runtime evaluation continuation options and candidate suggestions.
- Skill guidance for prompt registration, recording understanding, and model and usage context so Bench
  can estimate quality and cost.

## 0.1.0 (beta)

- TypeScript, JavaScript, Python, Go and Rust clients.
- Nested model/tool traces, environments and metadata-only collection by default.
- Per-call duration, token usage and explicitly reported or estimated USD cost.
- Built-in privacy filtering, bounded queues, retries and explicit lifecycle flush.
- Local application evaluation and scripted simulations with independent state assertions.
- Explicit redacted report publication and strict incomplete-evidence handling.

Automatic framework instrumentation and OTLP export were not included in 0.1.0; 0.2.0 adds the OpenTelemetry bridge.
