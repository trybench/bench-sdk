# Fragments, variables and conditions

Opened from the registration checklist in SKILL.md, steps 3 and 4.

## Fragments

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

## Conditions

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
