# Changelog

## 0.2.3 (beta)

- `register_calls` platform operation: register one entry per model call (messages as fragments, model
  configuration, conditions, verification level) instead of one per prompt. `register_prompts` is deprecated.
- Skill guidance for registering calls: code-derived keys, resolved strings, conditions, verification.
- Skill: say how to choose context source ids (start with `manual-`; reuse the id to update).
- Skill: when running app code to capture prompt text, set `BENCH_API_KEY` to an empty value so no empty traces are sent.
- Trace metadata allow-list adds `gen_ai.request.temperature`, `top_p`, `top_k` and `max_tokens` in all four
  clients, so observed model settings can be compared with declared ones.

## 0.2.2 (beta)

- Published through the tag-driven trusted-publishing workflow. No functional change from 0.2.1.

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
