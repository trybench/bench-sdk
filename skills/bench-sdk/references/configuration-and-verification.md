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
says the same for the effective settings. A sync and an async method, or a streaming and a non-streaming method, that do the same
job are one call; register the one the application uses and mention the other in `notes`.
A tool that a library supplies, whose definition is not in the repository, is listed by
the name the code uses; say in `notes` that its description and schema are not visible,
and do not invent them. A tool defined in the repository is listed with the schema you
can read.
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

Decide the level with one question: **could your check have failed?**
- `agent_verified` needs a check that could have caught a wrong registration. You
  executed code that assembles the text (a builder function, a template with
  interpolation or concatenation, the call itself against a fake client) with sample
  inputs, and compared its output to your fragments, state by state.
- If the text is a plain literal that you copied out and "checked" against the same
  literal, the check cannot fail: `declared_only`. The same holds when you retyped the
  text instead of extracting it, or evaluated only some of the pieces.
- Evaluating a template literal or f-string with sample values and comparing it to the
  fragment with its `{name}` placeholders filled in counts as running the code.
- Framework-added text you did not see, or a remote prompt, caps the level at
  `declared_only` or `evidence_lacking` whatever you ran.

Whichever level you choose, `verification.evidence.method` says exactly what you ran
or read. It certifies the prompt text only, not that the call works: if you
see wiring that would fail (a missing required argument, a key that does not exist)
or a hard-coded secret, say so in `notes`, and never copy a secret into the payload.
Bench refuses `agent_verified` without matching evidence. Fix a mismatch (a missed
inline rule string is the usual cause) before you register.
