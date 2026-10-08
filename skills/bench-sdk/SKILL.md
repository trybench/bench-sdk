---
name: bench-sdk
description: Install and verify Bench's server-side SDK for JavaScript, Python, Go or Rust, or update its instrumentation. Use for Bench SDK setup, not arbitrary OpenTelemetry configuration.
---

# Bench SDK setup

Bench evaluates and improves AI systems: agents, prompts, tools, model
configuration and hand-offs. The SDK is how Bench learns what the system is made
of, so instrument the structure that actually runs.

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

Start metadata-only (`captureContent: false` or the language's equivalent). Content
capture is a separate opt-in. Production checks are on by default per system (the owner can turn them off in Bench). A setup key has a zero evaluation cap. Do not
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

## Register the model calls you can see

Traces never carry the editable prompt, so a runtime system has no prompt
components until you register them. You are inside the repository. Register
**calls, not prompts**: one entry per job the code sends to a model. A call's system
prompt, user template, few-shot turns and injected context belong in the same entry,
because Bench evaluates the call as the code sends it. Use the `register_calls`
operation (`POST /api/ai-systems/{id}/calls`, MCP tool `bench_register_calls`).
`register_prompts` is deprecated: it stores each prompt as its own unit, so a call
whose system prompt and user message are registered separately is evaluated as two
partial calls.

Field names below follow the input schema of `register_calls` (the operation catalog or
the MCP tool definition); if any other schema file disagrees, the catalog wins.

Work through the steps in order. Each step names the file in `references/` (next to
this file) to open **before** you do it; do not work from memory of the rules.

1. **Find the calls.** In a Python repository run `python3 <this skill's folder>/scripts/find_calls.py <repo root>`
   (it never runs the repo). Its list is a starting point, never the whole answer:
   read every file in its "not analysed" list. A call is one **job**: callers that do
   different jobs through one shared helper are separate calls; optional pieces inside
   one caller are conditions. Skip speech, embeddings, images and moderation.
   Open `references/finding-calls.md`.
2. **Give each call a key and a shape.** `key` = `<file>::<qualified function>#<ordinal>`,
   derived from the code, never a name you chose or a line number; `shape` is required on
   every call and computed (the finder prints it), never invented. Open `references/keys-and-shapes.md`.
3. **Write the request as fragments.** Register the resolved string (run or evaluate the
   builder), variables as `{name}`, the user turn as a `runtime_value` fragment when it
   has no text of its own. Open `references/fragments-and-conditions.md`.
4. **Conditions.** If the code decides which pieces to send, use `conditions` and
   `when`; never one call per combination. Same file as step 3.
5. **Configuration, slots, tools.** Exactly one configuration with `source: "code"`;
   `model` is required. Open `references/configuration-and-verification.md`.
6. **Verify before you claim it.** `agent_verified` only if you ran or evaluated the code
   that builds the prompt and every checked state matched; otherwise `declared_only`
   or `evidence_lacking`. Same file as step 5.
7. **Check, register and use the result.** Write the payload to a file and run
   `python3 <this skill's folder>/scripts/check_payload.py payload.json` until it reports
   no errors (it never contacts Bench; it checks form, not whether the text is right, and
   warnings are worth reading). Then register. Open `references/after-registering.md`.

Rules that apply to every step:
- Never copy a hard-coded secret into a payload; say in `notes` that you saw one.
- Never paraphrase prompt text and never invent text, models, tools or settings.
- If you cannot resolve something, register what you can and set
  `verification.level` to `evidence_lacking`. Do not guess.
- Say in `notes` anything the code does not show: framework-added text, remote prompts,
  a model chosen at runtime, a shared request site.
- Registration is repeatable, edits no source, starts no evaluation, and needs no GitHub
  connection.

Example payload (one call with a system prompt and a user template):

```json
{"repo_full_name": "owner/repo", "branch": "main",
 "calls": [{"key": "src/services/recipe.py::extract_recipe#0",
            "key_source": "ast", "shape": "a1b2c3d4e5", "name": "recipe extraction",
            "scope": "production",
            "configurations": [{"source": "code", "provider": "openai", "model": "gpt-5.4-nano",
                                "settings": {"declared": {}, "effective": {"reasoning_effort": "low"},
                                             "effective_provenance": "declared"}}],
            "fragments": [
              {"id": "system", "role": "system", "kind": "text", "text_provenance": "resolved_string",
               "text": "You are a recipe parser. Return ONLY valid JSON."},
              {"id": "user", "role": "user", "kind": "text", "text_provenance": "resolved_string",
               "text": "Extract the recipe from this text:\n\n{content}",
               "variables": [{"name": "content", "expr": "content", "source": "runtime_input"}]}],
            "verification": {"level": "agent_verified",
                             "evidence": {"method": "ran the call with a fake client and compared messages",
                                          "states_checked": 1, "states_matched": 1}}}]}
```

## Write down what the system is for

Bench judges results against the system's purpose and rules, so after the
system exists, record them from the repository with `put_context_source`
(`PUT /api/ai-systems/{id}/context/sources`, MCP `bench_put_context_source`),
reading `expected_version` from `get_system_context` first:

- **Purpose** (category `business_intent`, kind `document`, scope `system`): who
  the system serves, what it does, what a good result looks like. One short text.
- **Supporting context**, one source each: policies and constraints the system
  must follow as `rules_constraints`; example conversations or expected results
  as `examples_feedback`; architecture notes as `system_structure`.
- **Models and usage** (also `system_structure`): which model each prompt runs
  on, the provider, expected monthly request volume and typical input and
  output sizes. Bench uses this to estimate cost, compare models and propose
  cheaper options before production traffic exists. Estimate from code and
  configuration when nothing is documented, and say that it is an estimate.

Cite the files each statement came from and phrase it as derived from the
repository. The owner reviews and can edit or add more later in Bench. Never
invent business policy, reference answers or scores that are not in the files.

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
resources; the tracing client executes real app tests and simulations. Both ship
in the published 0.2.0 packages.
