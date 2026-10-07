# Register and use the result

Opened from the registration checklist in SKILL.md, step 7.

(The example payload is in SKILL.md.)

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
