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
  For module-level code use the name the result is assigned to when there is one
  (`math_agent = create_agent(...)` gives `<file>::math_agent#0`), and `<module>#n`
  only when nothing is assigned. A request made inside a function that is itself
  inside another function counts under the full chain, so the ordinal counts only the
  requests under that exact chain. Use the same rule every time so two runs give the
  same key.
- **Chained helpers:** when a helper forwards to another helper, the producing caller
  is the first function up the chain that supplies the prompt text.
- **`shape` is required on every call** (no call is sent without one; check each call
  in the payload before you register), and it must be computed, never invented: the first 10 hex characters of the SHA-1 of the string
  `kw:<sorted argument names of the request call, comma-joined>|model:<source text of
  the model argument, or of the receiver if there is none>|params:<parameter names of
  the enclosing function except self/cls, comma-joined>`. The finder prints it; in
  other languages compute the same string and hash it (`printf '%s' "$s" | shasum | cut -c1-10`).
  When the request is made by a helper that takes an options object, the argument names
  are the object's keys. Calls of one helper in one file often share a shape; that is
  fine: the key (with its ordinal) identifies them, and Bench uses the shape only to follow
  a call whose key changed.
