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
**calls, not prompts**: one entry per request the code sends to a model. A call's
system prompt, user template, few-shot turns and injected context belong in the
same entry, because Bench evaluates the call as the code sends it. Use the
`register_calls` operation (`POST /api/ai-systems/{id}/calls`, MCP tool
`bench_register_calls`). `register_prompts` is deprecated: it stores each prompt as
its own unit, so a call whose system prompt and user message are registered
separately is evaluated as two partial calls.

### 1. Find the calls

Find every place the code sends a request to a model (`.create(`, `generateText`,
`client.chat`, an agent `.run`, a framework's model call). Skip offline scripts
unless they matter in production: mark those `scope: "offline_script"`. If one
function builds the prompt for several calls, or one call takes several prompts as
an argument, that is one call with conditions (below), not several calls.

### 2. Give each call a code-derived key

`key` is `<repo-relative file>::<qualified function>#<ordinal of the model call in
that function>`, for example `src/services/recipe.py::extract_recipe#0`. Derive it
from the code, never from a name you chose, a line number, or a prompt title: Bench
uses it to recognize the same call after edits. Also send `shape`: a short
fingerprint of the call expression (its keyword argument names, model expression,
and the enclosing function's parameters) so Bench can follow a call that was
renamed or moved. If you can parse the code (Python `ast`, TypeScript compiler API),
use `key_source: "ast"`; if you derived the key by hand use `"agent"`.

### 3. Describe the request as fragments

A call is a list of `fragments`, in the order the code sends them. Each has a
`role`, a `kind` and an `id`:

- `kind: "text"`: prompt text. `text` is the string the model receives with every
  variable written as `{name}`.
- `kind: "runtime_value"`: a message with no source text that the test case supplies
  (the user's turn is usually this). Send no `text`.
- `kind: "constant"`: text that only feeds a variable of another fragment (a shared
  few-shot block). Bind it with `variables[].bound_to_fragment`; it is never sent
  on its own.

**Register the resolved string, not the code that builds it.** Source such as
`parts = [f"Case: {x.category}", ...]` is not a prompt. Run or evaluate the code
that builds the prompt with small sample inputs and register what it produces, with
the variable parts replaced by `{name}`. Set `text_provenance: "resolved_string"`
when you obtained the text by running or evaluating code, `"source_text"` when you
only read it from the source. Never paraphrase.

- Python: import the builder and call it with simple stand-ins; no network or
  model call is needed. Keepr-style apps can be captured by running the call with a
  fake client that records `messages`.
- TypeScript or JavaScript: extract the string expression (template literal,
  concatenation, builder function) into a scratch file and run it with `tsx` or
  `node`. Run it as TypeScript when the snippet has type syntax (`as string`).
- Go and Rust: print the builder's output from a scratch test or example.
- If a prompt is fetched from a remote service, or the framework wraps your text in
  its own template (supervisor agents add guidelines and a memory block), say so in
  `notes`; the text in the repository is not what the model receives.
- If you cannot resolve a piece, register what you can and set
  `verification.level` to `evidence_lacking`. Do not guess.

For each variable give `name`, `expr` (the expression exactly as written, e.g.
`row[0]` or `item.name`) and, when you ran the code, `format_hint` and
`source`: `literal`, `code`, `command_output` (a value from running a command) or
`runtime_input`.

### 4. Conditions

When the code decides which pieces to send (`if "slot" in present_types:`, a
language switch, optional rule blocks), do not register one call per combination and
do not merge every piece in. Register the pieces as fragments with a `when`
expression over named `conditions`:

```json
"conditions": [{"id": "slot", "description": "the batch has a slot cluster",
                "code_ref": "\"slot\" in present_types"}],
"fragments": [{"id": "rule_slot", "role": "system", "kind": "text", "group": "rules",
               "when": "slot", "text": "- For a slot cluster ..."}]
```

`when` uses condition ids with `and`, `or`, `not` and parentheses only. Use
`groups` for text the code emits around a run of fragments (a header before the
first included fragment, a separator between them). Use `message_group` when
several fragments form one message that is not the system message. Add
`constraints` (`exactly_one`, `not_both`, `implies`, each with a `source`) when the
code makes combinations impossible. A condition that changes which model, tools or
place in the code is used is a different call, not a condition.

### 5. Configuration, slots, tools

`configurations` lists the model setups. Exactly one has `source: "code"`: what the
code does today (provider, model, fallback, tools, `tool_choice`, and `settings`
with `declared` values and `effective` values: wrappers often drop or inject
options such as `temperature` or `reasoning_effort`, so check what the provider call
really receives). Do not add `suggested` configurations yourself. Use `slots` for
conversation history and injected context (a second system message with user facts)
that the code adds around the fragments.

### 6. Verify before you claim it

For every call, compare what you registered with what the code produces. Render
the fragments yourself for at least the all-conditions-off state, one state with
each condition on, and (for a few conditions) every state, and check each equals the
real builder's output. Then set `verification`:

- `agent_verified`: you ran the builder and **every** checked state matched. Send
  `evidence` with `method`, `states_checked` and `states_matched` (equal).
- `declared_only`: read from code, not run.
- `evidence_lacking`: part of the text could not be resolved.

Bench refuses `agent_verified` without matching evidence. Fix a mismatch (a missed
inline rule string is the usual cause) before you register.

### 7. Register and use the result

```json
{"repo_full_name": "owner/repo", "branch": "main",
 "calls": [{"key": "src/services/recipe.py::extract_recipe#0",
            "key_source": "ast", "shape": "a1b2c3d4e5", "name": "recipe extraction",
            "scope": "production",
            "configurations": [{"source": "code", "provider": "openai", "model": "gpt-5.4-nano",
                                "settings": {"declared": {}, "effective": {"reasoning_effort": "low"},
                                             "effective_provenance": "observed"}}],
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

The response returns one component id per call (shown in Bench as `Call #<id>`).
Put it on the spans of that call (`bench.component_id` / `componentId`) so runtime
evidence links to it. Read the `status` and `warnings` of each call: Bench cleans
placeholder syntax such as `{row[0]}` itself and tells you what it changed; a call
that "looks like Call #N" was registered as new because the match was doubtful; a
call missing from a later registration is kept and labelled not seen. If Bench
could not reach its checker, calls are stored as not checked and are not evaluated
until you register again. Registration is repeatable, edits no source, starts no
evaluation, and does not need a GitHub connection. Cite real files and lines, and
do not invent text, models, tools or settings.

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
