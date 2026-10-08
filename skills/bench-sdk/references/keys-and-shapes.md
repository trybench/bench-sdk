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
  with one request is `#0`. A helper that has its own prompt text is its own call with
  its own key; it does not count in the ordinal of the function that calls it. Only
  requests made in the function itself, and calls to a shared helper whose prompt this
  function supplies, count.
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
- **Send `shape` on every call.** The API accepts a call without one unless
  `key_source` is `ast`, but then Bench cannot follow a renamed or moved call: it
  becomes a new unit and the old one turns "not seen". It must be computed, never invented: the first 10 hex characters of the SHA-1 of the string
  `kw:<sorted argument names of the request call, comma-joined>|model:<source text of
  the model argument, or of the receiver if there is none>|params:<parameter names of
  the enclosing function except self/cls, comma-joined>`. The finder prints it; in
  other languages compute the same string and hash it (`printf '%s' "$s" | shasum | cut -c1-10`).
  When the request is made by a helper that takes an options object, the argument names
  are the object's keys. In languages with positional arguments, the names are the keys
  of every object-literal argument at its top level (merged, de-duplicated, sorted);
  array literals, tuples and other positional values add nothing.
  The request call is the method that actually triggers the request on the model, chain
  or agent object (`invoke`, `stream`, `streamEvents`, `generate`, `run`, `create`); for
  a composed chain it is the call that runs the chain. The model part is the source text of
  the receiver the request is made on, up to the request method, with whitespace
  removed and every argument list replaced by `(...)` (`this.model`,
  `llm.withConfig(...)`); for a helper called with no receiver it is the helper's name.
  The parameter part lists the enclosing function's parameter names in source order;
  a destructured parameter contributes its property names in source order. This rule
  is untested across agents: if two runs disagree on shapes, the key still identifies the call. Calls of one helper in one file often share a shape; that is
  fine: the key (with its ordinal) identifies them, and Bench uses the shape only to follow
  a call whose key changed.
