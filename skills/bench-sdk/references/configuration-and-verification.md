# Configuration, slots, tools and verification

Opened from the registration checklist in SKILL.md, steps 5 and 6.

## Configuration, slots, tools

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

## Verify before you claim it

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
