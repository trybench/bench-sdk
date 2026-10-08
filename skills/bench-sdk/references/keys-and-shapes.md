# Keys and shapes

Opened from the registration checklist in SKILL.md, step 2.

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
- **Function part:** the full chain of enclosing named classes and functions, outermost
  first, joined with dots (`Service.run`; a call inside a function `inner` defined
  inside `build` is `build.inner`, never just `inner` or `build`). Do not skip a level
  and do not mix styles within one repository. An anonymous callback or lambda adds
  the name it is assigned to or the property it sits under (`planActivities.execute`).
  A callback registered by name (`graph.add("label", async () => ...)`) adds that
  label (`graph.label`), never the name of the registering method. A function that
  is defined and called on the spot adds nothing: its requests count under the
  enclosing function.
  For module-level code use the name the result is assigned to when there is one
  (`math_agent = create_agent(...)` gives `<file>::math_agent#0`), and `<module>#n`
  only when nothing is assigned. A request made inside a function that is itself
  inside another function counts under the full chain, so the ordinal counts only the
  requests under that exact chain. Use the same rule every time so two runs give the
  same key.
- **Composed chains:** count model invocations, not calls of `.run`. A chain with two
  model steps (for example rewrite the question, then answer it) sends two requests:
  two ordinals, in the order the steps are built in the code, even if one `.invoke`
  or `.stream` runs the whole chain. Their shapes may be equal; the key tells them apart.
- **Chained helpers:** when a helper forwards to another helper, the producing caller
  is the first function up the chain that supplies the prompt text.
- **`shape` is required on every call** (no call is sent without one; check each call
  in the payload before you register), and it must be computed, never invented: the first 10 hex characters of the SHA-1 of the string
  `kw:<sorted argument names of the request call, comma-joined>|model:<source text of
  the model argument, or of the receiver if there is none>|params:<parameter names of
  the enclosing function except self/cls, comma-joined>`. The finder prints it; in
  other languages compute the same string and hash it (`printf '%s' "$s" | shasum | cut -c1-10`).
  When the request is made by a helper that takes an options object, the argument names
  are the object's keys. In languages with positional arguments, the names are the keys
  of every object-literal argument (merged, sorted); positional values add nothing.
  The request call is the method that actually triggers the request on the model, chain
  or agent object (`invoke`, `stream`, `streamEvents`, `generate`, `run`, `create`); for
  a composed chain it is the call that runs the chain. The model part is the model
  expression; when the request is made on a chain or agent object and no model
  expression is in scope, use the variable name of that object. Calls of one helper in one file often share a shape; that is
  fine: the key (with its ordinal) identifies them, and Bench uses the shape only to follow
  a call whose key changed.
