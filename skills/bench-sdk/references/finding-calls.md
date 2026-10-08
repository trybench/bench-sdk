# Finding the calls

Opened from the registration checklist in SKILL.md, step 1.

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
and derive the key and shape by hand as described in `keys-and-shapes.md`.

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

A model call can also hide inside a library the application uses (a memory or
retrieval service, an SDK helper that summarizes or extracts). Check the application's
dependencies that take a model or an API key; if the library's code is in the
repository or installed, read it and register the call. If you cannot read it, name
the library in `notes` so the gap is visible.

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
  (`fragments-and-conditions.md`), not separate calls.
- The request goes through an **injected function or callback** (a `chat` function
  passed in, a client object chosen at start-up, a dependency container): the finder
  sees only the client classes behind it. Register one call per function that owns a
  prompt template and hands it to the callback, key it by that function, set
  `key_source: "agent"`, and compute `shape` with the formula in `keys-and-shapes.md`.
- Different implementations of the same helper (an OpenAI and an Anthropic client
  class behind one interface) are model configurations of the same calls, not
  separate calls.
- A flag or branch that changes only **how** the same tool or output format is
  attached (a different helper, a different forcing mode) while the prompt, the model
  and the tool set stay the same is **one call**. Register what the code does by
  default as the `source: "code"` configuration and describe the other path in `notes`.
  If the branch changes the set of tools, the model, or the place in the code that
  makes the request, it is a different call.
- Two request methods that do the same job for the same prompt (sync and async,
  streaming and non-streaming, `invoke` and `stream`) are one call: key it by the
  one the application's main path uses and name the other in `notes`.
- If you cannot tell whether two callers do different jobs (a `mode` argument that
  changes the task), read the code and decide by the task the model is asked to do;
  say what you decided in `notes`.
