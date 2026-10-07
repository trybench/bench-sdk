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

In a Python repository, start with the bundled finder (standard library only; it
never runs the repository): `python3 <this skill's folder>/scripts/find_calls.py
<repo root>` (add `--json` for machine output). It lists every request site with its
key and `shape`, says whether the site is direct or a shared helper, and for a
helper lists its callers, what each passes, and the key and shape of each producing
caller. It proposes; you confirm by reading the code. Its condition counts are candidates
(`if` blocks and ternaries that add text to the prompt) and over-report: confirm each
against the code. It cannot see dynamic dispatch
(prompt dictionaries, callbacks, decorators, registries), other languages, or whether
two callers do different jobs, and it only knows common client method names (pass
`--methods name1,name2` for others). In other languages, find the sites by reading
and derive the key and shape by hand as described in section 2.

**The finder's list is never the whole answer.** It only sees calls to common client
methods with an explicit prompt argument. Its output ends with a "not analysed" list of
files that use framework constructs (agent factories, runnables, prompt templates);
treat an empty or short site list as "the finder could not see the requests", not as "there
are few calls". Read every listed file, and any file that builds a prompt, and register what
you find there too (keys by hand, `key_source: "agent"`).

**Frameworks that hide the request.** Many frameworks send the request for you: an
agent or supervisor factory (`create_agent(model, prompt=...)`), a prompt template piped
into a model (`prompt | model`), a runnable or graph node (`model.invoke(...)`,
`await model.ainvoke(...)`, `with_structured_output(...)`, `bind_tools(...)`), or an
agent object created once at module level. The call is the place that owns the prompt
and the model, even if the framework makes the final request. Rules:
- Roles in framework prompts map to the request roles: `human` is `user`, `ai` is
  `assistant`, `system` stays `system`. A message that is only a runtime value (for
  example the running chat history, or the user's question) is a `runtime_value`
  fragment or a `history` slot, not text.
- A factory or helper used by several apps for different jobs follows the shared-helper
  rule above: one call per producing caller.
- A framework may add text you cannot see in the repository (a supervisor's built-in
  guidelines, middleware, tool descriptions): say so in `notes` and do not claim
  `agent_verified` for text you did not see.
- A call with no prompt text of its own (the framework supplies it, or only history is
  sent) is registered with `verification.level` `evidence_lacking` and a note, not
  skipped silently.

Find every place the code sends a request to a model (`.create(`, `generateText`,
`client.chat`, an agent `.run`, a framework's model call). Skip offline scripts
unless they matter in production: mark those `scope: "offline_script"`.

A call is one **job**, not one line of code. Decide as follows:

- The request site builds its messages from its own literal text, with parameters
  only as data: one call.
- The request site is a **shared helper** (its messages come from its parameters,
  e.g. `ask(system_prompt, user_text)`): list every function that calls it and what
  each passes. Callers that do different jobs are **separate calls, one per
  producing caller function**, each with its own key built from the caller (for
  example `app.py::summarize#0`, `app.py::translate#0`) and a `notes` entry (a list of strings) that
  they share a request site. A caller that only forwards its own parameter on is
  not a call: follow the chain to its callers.
- **Optional pieces inside one caller** (an `if` that adds a rule block, a language
  switch, a flag that picks between prompts) are **conditions** of that one call
  (section 4), not separate calls.
- The request goes through an **injected function or callback** (a `chat` function
  passed in, a client object chosen at start-up, a dependency container): the finder
  sees only the client classes behind it. Register one call per function that owns a
  prompt template and hands it to the callback, key it by that function, set
  `key_source: "agent"`, and compute `shape` with the formula in section 2.
- Different implementations of the same helper (an OpenAI and an Anthropic client
  class behind one interface) are model configurations of the same calls, not
  separate calls.
- If you cannot tell whether two callers do different jobs (a `mode` argument that
  changes the task), read the code and decide by the task the model is asked to do;
  say what you decided in `notes`.

### 2. Give each call a code-derived key

`key` is `<repo-relative file>::<qualified function>#<ordinal of the model call in
that function>` (for a shared helper, the producing caller's function and the
ordinal of its call to the helper), for example `src/services/recipe.py::extract_recipe#0`. Derive it
from the code, never from a name you chose, a line number, or a prompt title: Bench
uses it to recognize the same call after edits. Also send `shape`: a short
fingerprint of the call expression (its keyword argument names, model expression,
and the enclosing function's parameters) so Bench can follow a call that was
renamed or moved. If you can parse the code (Python `ast`, TypeScript compiler API),
use `key_source: "ast"` (the finder's output counts); if you derived the key by hand
use `"agent"`.

- **Ordinal:** the position of the model request among the request sites and
  helper calls in that function, counted in source order, starting at 0. A function
  with one request is `#0`.
- **Function part:** the enclosing named function with its classes (`Service.run`).
  For code inside a callback or lambda, use the nearest enclosing named construct
  and, where a framework object is assigned to a name, that name
  (`planActivities.execute`). For module-level code use the name the result is assigned
  to when there is one (`math_agent = create_agent(...)` gives `<file>::math_agent#0`),
  and `<module>#n` only when nothing is assigned. Use the same rule every time so two
  runs give the same key; the ordinal then counts only requests under that name.
- **Chained helpers:** when a helper forwards to another helper, the producing caller
  is the first function up the chain that supplies the prompt text.
- **`shape` is required** whenever you send a key, and it must be computed, never
  invented: the first 10 hex characters of the SHA-1 of the string
  `kw:<sorted argument names of the request call, comma-joined>|model:<source text of
  the model argument, or of the receiver if there is none>|params:<parameter names of
  the enclosing function except self/cls, comma-joined>`. The finder prints it; in
  other languages compute the same string and hash it (`printf '%s' "$s" | shasum | cut -c1-10`).
  When the request is made by a helper that takes an options object, the argument names
  are the object's keys. Calls of one helper in one file often share a shape; that is
  fine: the key (with its ordinal) identifies them, and Bench uses the shape only to follow
  a call whose key changed.

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
  model call is needed. Apps that call a model client directly can be captured by running the call
  with a fake client that records `messages`.
- TypeScript or JavaScript: extract the string expression (template literal,
  concatenation, builder function) into a scratch file and run it with `tsx` or
  `node`. Run it as TypeScript when the snippet has type syntax (`as string`).
- Go and Rust: print the builder's output from a scratch test or example.
- If a prompt is fetched from a remote service, or the framework wraps your text in
  its own template (supervisor agents add guidelines and a memory block), say so in
  `notes`; the text in the repository is not what the model receives.
- If you cannot resolve a piece, register what you can and set
  `verification.level` to `evidence_lacking`. Do not guess.

**How fragments are joined.** The text of all included `system` fragments is
concatenated in order, with nothing between them, into one system message; a group
adds its `header` before its first included fragment and its `separator` between
included fragments. Fragments sharing a `message_group` are concatenated the same way
into one message. Fragments of other roles, without a `message_group`, are separate
messages. So put every newline the code emits into the text, the header or the
separator. History and injected context go in `slots`, not in fragments.

**Braces.** Only `{identifier}` is a placeholder. JSON examples and other braces in
the resolved text stay as they are (`{"a": 1}` is fine). Text that must contain a
literal `{word}` cannot be expressed: say so in `notes` and set
`verification.level` to `evidence_lacking`.

**Loops.** A block the code builds in a loop (one line per item) is one variable
whose `kind` is `expression`, with a `format_hint` that shows one item and says how
items are joined. Do not expand the loop and do not add a condition per item.

**Not a prompt call.** Requests with no text prompt (speech to text, embeddings,
image generation, moderation) are not registered. A tool loop (the same call repeated
with a growing message list) is one call: register the first request.

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
code makes combinations impossible; each constraint names at least two conditions (a
single condition needs none). `output_format` belongs to the call, next to
`configurations`, not inside a configuration. A condition that changes which model, tools or
place in the code is used is a different call, not a condition.

### 5. Configuration, slots, tools

`configurations` lists the model setups. `model` is required: when the model is
chosen at runtime (a setting, a per-request choice), put the expression as written in
the code (`settings.DEFAULT_MODEL`) and explain in `notes`; set `provider` only when
you know it. Exactly one has `source: "code"`: what the
code does today (provider, model, fallback, tools, `tool_choice`, and `settings`
with `declared` values and `effective` values: wrappers often drop or inject
options such as `temperature` or `reasoning_effort`, so check what the provider call
really receives). `declared` and `effective` are plain objects of option name to JSON value
(`{"temperature": 0, "reasoning_effort": "low"}`); `tool_choice` is a string
(`"auto"`, `"required"` or a tool name); `output_format` is a free object that
describes the output contract the code asks for (for example a JSON schema or a
response-format object). A call is `production` when it runs in the live application
and `offline_script` when only scripts, tests or evaluation code run it (a judge in
an evaluation scorer is offline unless the same code runs it for live traffic).
A configuration's `provenance` says whether the model and tools were `declared`
(read from code) or `observed` (seen in a real request); `settings.effective_provenance`
says the same for the effective settings. A sync and an async method that do the same
job (`invoke` and `ainvoke` of one class) are one call; register the one the application
uses and mention the other in `notes`.
Do not add `suggested` configurations yourself. Use `effective_provenance`
`observed` only if you saw the values in a real request; `declared` when you read
them from code; `unknown` when you could not tell. Use `slots` for
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

`agent_verified` means you obtained the prompt text by running or evaluating the
code that builds it and compared the result. If you only extracted string literals and
checked them against themselves, or retyped text instead of extracting it, use
`declared_only`. It certifies the prompt text only, not that the call works: if you
see wiring that would fail (a missing required argument, a key that does not exist)
or a hard-coded secret, say so in `notes`, and never copy a secret into the payload.
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
