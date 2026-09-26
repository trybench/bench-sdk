---
name: bench-sdk
description: Install and verify Bench's server-side SDK for JavaScript, Python, Go or Rust, or update its instrumentation. Use for Bench SDK setup, not arbitrary OpenTelemetry configuration.
---

# Bench SDK setup

Read the selected language package README for its actual API. JavaScript uses
Node.js 20+ ESM. Python, Go and Rust packages live in their named subdirectories.
These are server clients, not browser instrumentation or OTLP collectors.
Install the package exactly as the user's Bench quick start says: it may name a
local or preview package instead of the public registry. Otherwise the JavaScript
package is `npm install @benchai/sdk` and native packages use the supplied
package or pinned source from their documentation.

Use the repository, branch, endpoint and key supplied by the user's Bench quick
start. Write the key only into an existing gitignored server environment file.
Do not print it or copy it into client bundles, tests, examples or commits. If
the user has not supplied a key, leave an environment-variable reference and
direct them to Bench's SDK page. Do not mint keys with broader access.

Create one client per process. Preserve existing provider configuration,
prompt behavior and auth. Use the language's trace wrapper or span guard, passing
parent context through asynchronous tasks and goroutines. System name declares a stable AI-system boundary;
it does not automatically discover business objectives. Only use component IDs
read from Bench, never invented ones.

Start metadata-only (`captureContent: false` or the language's equivalent). Content capture and automatic
paid checks are separate opt-ins. A setup key has a zero evaluation cap. Do not
enable automatic evaluation, enable optional external judging, or modify caps
as an installation step. Built-in redaction is not guaranteed anonymization.

Verify with a local HTTP receiver or injected transport and synthetic inputs. Assert original return/error
behavior, no input/output in metadata-only payloads, and no extra model calls.
Native clients require explicit flush. Flush in the platform's serverless lifecycle hook or shut down after in-flight
requests on process exit. Telemetry failure must not break the application.

Report changed files, test outcome, missing credentials and actual limitations.
Never describe recorded runtime metadata as a completed benchmark or treat a
model judgment as a verified golden label. Publication or deployment requires
the user's separate authorization.

## Discover the system from what actually runs

Bench draws the AI system (agents, model calls, tools, hand-offs) from the spans
it receives, so instrument the structure, not just one call. Decide by framework:

1. **Framework that emits OpenTelemetry GenAI spans** (OpenAI Agents, Strands,
   Pydantic AI, LangChain/LangGraph with OTel instrumentation, CrewAI, LlamaIndex,
   Google ADK, AutoGen, Vercel AI SDK `experimental_telemetry`, and any OpenInference
   or OpenLLMetry instrumentor): do not wrap calls by hand. Attach the bridge to the
   framework's tracer provider and Bench infers agent/model/tool kinds from the
   `gen_ai.*` attributes.
   - Python: `from bench_sdk.otel import attach; attach(bench)` (or
     `attach(bench, provider)` when the framework owns its provider, e.g.
     `StrandsTelemetry().tracer_provider`). Pydantic AI also needs
     `Agent.instrument_all()`.
   - TypeScript: `new BenchSpanExporter(bench)` as a span exporter on the app's
     `@opentelemetry/sdk-trace-*` provider. No OpenTelemetry dependency is added by
     Bench; the exporter is structurally typed.
2. **Framework with its own tracing events but no OpenTelemetry** (Mastra
   observability exporters, LangChain callback handlers): write a small exporter
   that forwards each finished span to `recordExternalSpan` /
   `record_external_span` / `RecordExternalSpan` / `record_external_span`, keeping
   the framework's trace and span IDs (hash non-hex IDs to 32/16 hex) and mapping
   its span types to `AGENT`, `LLM`, `TOOL`, `CHAIN`. Set `gen_ai.agent.name`,
   `gen_ai.request.model`, `gen_ai.provider.name`, `gen_ai.tool.name` when known.
3. **Custom code, no framework**: nest the language's trace wrapper. An `AGENT`
   span per agent or workflow, an `LLM` span per model call (pass the model), a
   `TOOL` span per tool execution. Nested spans give Bench the hand-off edges.

Send one real request after setup and confirm the system appears under
AI Systems with its agents, models and tools. Metadata-only capture is enough
for discovery; do not enable content capture for it.

## Register the prompts you can see

Traces never carry the editable prompt template, so a runtime system has no
prompt components until they are registered. You are inside the repository:
find every prompt the system sends (system prompts, instructions, templates in
code, YAML, Markdown or JSON, prompts assembled across files) and register them
on the system with the `register_prompts` operation
(`POST /api/ai-systems/{id}/prompts`, MCP tool `bench_register_prompts`), using
the same key as the SDK:

```json
{"repo_full_name": "owner/repo", "branch": "main",
 "prompts": [{"path": "src/agents/prompts/analyzer.yaml", "line": 3, "end_line": 40,
              "name": "analyzer system prompt", "role": "system",
              "content": "<exact template text, variables unfilled>",
              "agent": "Analyzer agent"}]}
```

Rules: cite the real file and line; copy the text exactly, never paraphrase or
invent; set `agent` to the agent's runtime span name so the prompt lands inside
that agent's node; include `model` only when the account has model selection.
The response returns component ids: put each on the spans of the model call
that sends that prompt (`bench.component_id` / `componentId`) so runtime
evidence links to it. Registration is idempotent per path and line, edits no
source, starts no evaluation, and does not need a GitHub connection.

For application evaluation and scripted simulations, use the APIs documented by
the installed language package. Run synthetic cases with isolated test state.
Report upload is an explicit step; it must not happen during ordinary tracing setup.

## Headless development setup

Start with `bench_capabilities` and `bench_get_setup_status`. Authenticate through
MCP OAuth for account setup and SDK-key creation. Read the processing notice and
obtain user authorization before accepting a data source. `bench_connect_github`
starts the agent return flow; the user approves GitHub access and the agent resumes
with `bench_finish_github_connection`.

Use `bench_create_api_key` for a scoped application credential. Store it in the
project's secret environment; never echo or commit it. Instrument the actual entry
point, run a synthetic trace and verify `bench_sdk_status`. Prepare real app tests
and publish explicitly requested reports. The platform client manages saved
resources; the tracing client executes real app tests and simulations. The new
platform client requires a development source build until it is published.
