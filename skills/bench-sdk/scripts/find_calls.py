#!/usr/bin/env python3
"""Find the model calls in a Python repository and derive their Bench keys.

Usage: python3 find_calls.py <repo root> [--json] [--methods a,b,c] [--exclude dir,dir]

Standard library only. It never imports or runs the repository. It prints, for
every place the code sends a request to a model:

* the call's key `<file>::<qualified function>#<ordinal>` and its `shape`
  fingerprint, derived the same way every time (send both in register_calls);
* whether the site is DIRECT (the prompt text is written there) or a WRAPPER
  (the prompt comes from the caller's arguments), and for a wrapper every function
  that calls it, what each caller passes, and the key of each producing caller;
* hints about conditional pieces (`if` blocks that add text to the prompt).

It proposes; you confirm by reading the code. It cannot see dynamic dispatch
(prompt dictionaries, callbacks, decorators, plugin registries), non-Python code,
or decide whether two callers do different jobs: read the code for those.

Shape: the first 10 hex characters of the SHA-1 of
    kw:<sorted argument names of the request call joined by ,>|model:<source text of
    the model argument, or of the receiver when there is none>|params:<parameter names
    of the enclosing function except self/cls, joined by ,>
Other languages can compute the same string by hand and hash it.
"""
from __future__ import annotations

import argparse
import ast
import collections
import hashlib
import json
import os
import sys

# Methods that send a request to a model, and the arguments that carry the prompt.
REQUEST_METHODS = {
    "create", "acreate", "generate_content", "generate_content_async", "generate",
    "agenerate", "chat", "achat", "complete", "acomplete", "invoke", "ainvoke",
    "predict", "apredict", "parse", "stream",
}
PROMPT_ARGUMENTS = {"messages", "contents", "prompt", "input", "system", "system_instruction", "instructions"}
SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "env", "__pycache__", "site-packages", "build", "dist", ".tox"}
OFFLINE_HINTS = ("test", "scripts", "script", "examples", "example", "benchmark", "notebook", "eval", "tools")
OWN_TEXT_MIN = 40  # characters of literal text that make a request site "direct"
MAX_CHAIN = 3


def python_files(root: str, exclude: set[str]):
    for directory, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and d not in exclude]
        for name in sorted(files):
            if name.endswith(".py"):
                yield os.path.join(directory, name)


def callee_name(node: ast.Call) -> str | None:
    f = node.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None


def parameters(fn) -> list[str]:
    if fn is None:
        return []
    a = fn.args
    names = [x.arg for x in a.posonlyargs + a.args + a.kwonlyargs]
    return [n for n in names if n not in ("self", "cls")]


def positional_parameters(fn) -> list[str]:
    a = fn.args
    return [x.arg for x in a.posonlyargs + a.args if x.arg not in ("self", "cls")]


class Module(ast.NodeVisitor):
    """Functions, request sites and calls of one file."""

    def __init__(self, rel: str, tree: ast.AST, methods: set[str]):
        self.rel, self.methods, self.stack = rel, methods, []
        self.functions: dict[str, ast.AST] = {}
        self.sites: list[dict] = []
        self.calls: list[dict] = []
        self.constants = {
            t.id for n in getattr(tree, "body", []) if isinstance(n, ast.Assign)
            and ((isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)) or isinstance(n.value, ast.JoinedStr))
            for t in n.targets if isinstance(t, ast.Name)
        }
        self.visit(tree)

    def qualified(self) -> str:
        return ".".join(n.name for n in self.stack) or "<module>"

    def _scope(self, node):
        self.stack.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.functions[self.qualified()] = node
        self.generic_visit(node)
        self.stack.pop()

    visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = _scope

    def visit_Call(self, node: ast.Call):
        name = callee_name(node)
        fn = next((s for s in reversed(self.stack) if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
        entry = {"rel": self.rel, "qual": self.qualified(), "node": node, "fn": fn, "name": name}
        if name in self.methods and any(k.arg in PROMPT_ARGUMENTS for k in node.keywords):
            self.sites.append(entry)
        elif name:
            self.calls.append(entry)
        self.generic_visit(node)


def names_in(expr) -> set[str]:
    return {n.id for n in ast.walk(expr) if isinstance(n, ast.Name)}


def assignments_before(fn, line: int):
    """(targets, value) of statements in fn that run before `line` and build a value."""
    for st in ast.walk(fn):
        if getattr(st, "lineno", line) >= line:
            continue
        if isinstance(st, ast.Assign):
            yield [t for t in st.targets if isinstance(t, ast.Name)], st.value, st
        elif isinstance(st, ast.AugAssign) and isinstance(st.target, ast.Name):
            yield [st.target], st.value, st
        elif (isinstance(st, ast.Expr) and isinstance(st.value, ast.Call) and isinstance(st.value.func, ast.Attribute)
              and st.value.func.attr in ("append", "extend", "insert") and isinstance(st.value.func.value, ast.Name)):
            yield [st.value.func.value], ast.Tuple(elts=st.value.args, ctx=ast.Load()), st


FEED_DEPTH = 3  # hops from the request's own arguments; further back is data, not prompt text


def feeding(fn, expr, line: int) -> tuple[set[str], list]:
    """Names that feed `expr` through assignments before `line` (at most FEED_DEPTH hops),
    and the feeding statements."""
    need, used = names_in(expr), set()
    stmts: list = []
    for _ in range(FEED_DEPTH):
        added = False
        for targets, value, st in assignments_before(fn, line):
            if id(st) not in used and any(t.id in need for t in targets):
                used.add(id(st))
                stmts.append((targets, value, st))
                need |= names_in(value)
                added = True
        if not added:
            break
    return need, stmts


def own_text_chars(expr, stmts) -> int:
    seen, total = set(), 0
    for root in [expr] + [value for _, value, _ in stmts]:
        for n in ast.walk(root):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in seen:
                seen.add(id(n))
                total += len(n.value)
    return total


def adds_text(node, constants: set[str] = frozenset()) -> bool:
    """literal text of some length, an f-string, or a module-level string constant"""
    return any(
        isinstance(n, ast.JoinedStr)
        or (isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) >= 8)
        or (isinstance(n, ast.Name) and n.id in constants)
        for n in ast.walk(node)
    )


def conditional_pieces(fn, stmts, constants: set[str] = frozenset()) -> int:
    """How many distinct `if` blocks or ternaries add literal text to a variable that feeds the prompt."""
    guards = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.If):
            inner = {id(n) for n in ast.walk(node)}
            if any(id(st) in inner and adds_text(value, constants) for _, value, st in stmts):
                guards.add(id(node))
        elif isinstance(node, ast.IfExp):
            inner = {id(n) for n in ast.walk(node)}
            if any(id(value) in inner or any(id(n) in inner for n in ast.walk(value)) for _, value, _ in stmts) and adds_text(node, constants):
                guards.add(id(node))
    return len(guards)


def shape_of(site: dict) -> str:
    node, fn = site["node"], site["fn"]
    keywords = sorted(k.arg for k in node.keywords if k.arg)
    model = next((ast.unparse(k.value) for k in node.keywords if k.arg == "model"), None)
    if model is None:
        f = node.func
        model = ast.unparse(f.value) if isinstance(f, ast.Attribute) else ""
    canonical = f"kw:{','.join(keywords)}|model:{model}|params:{','.join(parameters(fn))}"
    return hashlib.sha1(canonical.encode()).hexdigest()[:10]


def argument_kind(node, caller_fn, constants: set[str]) -> str:
    if node is None:
        return "omitted"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return "literal"
    if isinstance(node, ast.JoinedStr):
        return "f-string"
    if isinstance(node, ast.Name):
        if node.id in constants:
            return "module constant"
        if caller_fn is not None and node.id in parameters(caller_fn):
            return "caller parameter"
        return "variable"
    if isinstance(node, ast.Call):
        return "call result"
    return type(node).__name__


def passed_argument(call: ast.Call, fn, name: str):
    for k in call.keywords:
        if k.arg == name:
            return k.value
    positional = positional_parameters(fn)
    if name in positional:
        index = positional.index(name)
        if index < len(call.args):
            return call.args[index]
    return None


def likely_offline(rel: str) -> bool:
    parts = rel.lower().replace("\\", "/").split("/")
    return any(any(h in part for h in OFFLINE_HINTS) for part in parts)


def analyze(root: str, methods: set[str], exclude: set[str]) -> dict:
    modules: dict[str, Module] = {}
    for path in python_files(root, exclude):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        try:
            with open(path, encoding="utf-8") as handle:
                modules[rel] = Module(rel, ast.parse(handle.read()), methods)
        except (SyntaxError, UnicodeDecodeError):
            continue
    all_sites = [s for m in modules.values() for s in m.sites]
    all_calls = [c for m in modules.values() for c in m.calls]
    definitions = collections.defaultdict(list)
    for m in modules.values():
        for qual, fn in m.functions.items():
            definitions[fn.name].append((m.rel, qual, fn))

    records = []
    # pass 1: classify every site
    for s in all_sites:
        node, fn = s["node"], s["fn"]
        prompt_args = [k.value for k in node.keywords if k.arg in PROMPT_ARGUMENTS]
        expr = ast.Tuple(elts=prompt_args, ctx=ast.Load())
        line = node.lineno
        if fn is not None:
            fed, stmts = feeding(fn, expr, line)
        else:
            fed, stmts = names_in(expr), []
        params = [p for p in parameters(fn) if p in fed and p not in ("client", "llm", "model", "api_key")]
        chars = own_text_chars(expr, stmts)
        direct = chars >= OWN_TEXT_MIN or not params
        s["record"] = {
            "file": s["rel"], "function": s["qual"], "line": line, "shape": shape_of(s),
            "kind": "direct" if direct else "wrapper", "prompt_parameters": [] if direct else params,
            "own_text_chars": chars, "conditional_text_pieces": conditional_pieces(fn, stmts, modules[s["rel"]].constants) if fn else 0,
        }
        if direct and not params and chars < OWN_TEXT_MIN:
            s["record"]["note"] = "little literal text and no parameters: the prompt comes from elsewhere (global, config, remote); read the code"

    wrapper_sites = [s for s in all_sites if s["record"]["kind"] == "wrapper"]
    wrapper_names = {s["fn"].name for s in wrapper_sites if s["fn"] is not None}
    # a function that only passes its own parameters on to a wrapper is a forwarder:
    # calls to it count as requests too (found by repeating until nothing is added)
    prompt_params_of = {s["fn"].name: s["record"]["prompt_parameters"] for s in wrapper_sites if s["fn"] is not None}
    function_of = {s["fn"].name: s["fn"] for s in wrapper_sites if s["fn"] is not None}
    changed = True
    while changed:
        changed = False
        for c in all_calls:
            name = c["name"]
            if name in wrapper_names and c["fn"] is not None and c["fn"].name not in wrapper_names:
                target = function_of.get(name)
                names = prompt_params_of.get(name) or []
                if target is None or not names:
                    continue
                kinds = [argument_kind(passed_argument(c["node"], target, p), c["fn"], modules[c["rel"]].constants) for p in names]
                if all(k in ("caller parameter", "omitted") for k in kinds):
                    wrapper_names.add(c["fn"].name)
                    function_of[c["fn"].name] = c["fn"]
                    prompt_params_of[c["fn"].name] = [p for p in names if p in parameters(c["fn"])] or names
                    changed = True

    def events_in(rel: str, qual: str):
        """request sites and calls to wrappers inside one function, in source order"""
        found = [(s["node"].lineno, s["node"].col_offset, "site", s) for s in all_sites if s["rel"] == rel and s["qual"] == qual]
        found += [(c["node"].lineno, c["node"].col_offset, "call", c) for c in all_calls
                  if c["rel"] == rel and c["qual"] == qual and c["name"] in wrapper_names]
        return sorted(found, key=lambda e: (e[0], e[1]))

    def key_for(rel: str, qual: str, event) -> str:
        ordinal = [e[3] for e in events_in(rel, qual)].index(event)
        return f"{rel}::{qual}#{ordinal}"

    # pass 2: keys; callers of wrappers
    for s in all_sites:
        r = s["record"]
        r["key"] = key_for(s["rel"], s["qual"], s)
    for s in wrapper_sites:
        r, fn = s["record"], s["fn"]
        callers = []
        pending = [(c, fn, 0) for c in all_calls if c["name"] == fn.name and c["node"] is not s["node"]]
        seen_calls = set()
        while pending:
            c, target_fn, depth = pending.pop(0)
            if id(c["node"]) in seen_calls:
                continue
            seen_calls.add(id(c["node"]))
            module = modules[c["rel"]]
            passes = {}
            for p in (prompt_params_of.get(target_fn.name) or r["prompt_parameters"]):
                passes[p] = argument_kind(passed_argument(c["node"], target_fn, p), c["fn"], module.constants)
            forwards = bool(passes) and all(v in ("caller parameter", "omitted") for v in passes.values()) and c["fn"] is not None
            entry = {
                "caller": f"{c['rel']}::{c['qual']}", "line": c["node"].lineno, "passes": passes,
                "role": "forwarder" if forwards else "likely_offline" if likely_offline(c["rel"]) else "producer",
            }
            if not forwards:
                entry["key"] = key_for(c["rel"], c["qual"], c)
                entry["shape"] = shape_of(c)
                builders = []
                for arg in list(c["node"].args) + [k.value for k in c["node"].keywords]:
                    if isinstance(arg, ast.Call) and callee_name(arg) in definitions:
                        for rel, qual, bfn in definitions[callee_name(arg)]:
                            ret = [n.value for n in ast.walk(bfn) if isinstance(n, ast.Return) and n.value is not None]
                            if ret:
                                _, bstmts = feeding(bfn, ast.Tuple(elts=ret, ctx=ast.Load()), 10**9)
                                builders.append({"builder": f"{rel}::{qual}", "conditional_text_pieces": conditional_pieces(bfn, bstmts, modules[rel].constants)})
                if builders:
                    entry["prompt_builders"] = builders
            callers.append(entry)
            if forwards and depth + 1 < MAX_CHAIN:
                pending += [(c2, c["fn"], depth + 1) for c2 in all_calls if c2["name"] == c["fn"].name]
        ambiguous = len(definitions[fn.name]) > 1
        r["callers"] = callers
        r["proposed_calls"] = sorted({c["key"] for c in callers if c["role"] == "producer"})
        r["offline_calls"] = sorted({c["key"] for c in callers if c["role"] == "likely_offline"})
        if ambiguous:
            r["warning"] = f"several functions are named {fn.name}: callers were matched by name only, verify them"
        if not callers:
            r["warning"] = "no caller found in this repository: an entry point, a dynamic caller, or another language"
        r["unit_hint"] = (
            "register one call per key in proposed_calls (callers that do different jobs), each with a note that they share a "
            "request site; optional text inside one caller is a condition"
        )
    for s in all_sites:
        records.append(s["record"])
    return {"root": os.path.abspath(root), "sites": sorted(records, key=lambda r: (r["file"], r["line"]))}


def print_text(result: dict) -> None:
    print(f"Request sites found under {result['root']}: {len(result['sites'])}")
    for r in result["sites"]:
        print(f"\n{r['key']}  [{r['kind']}]  shape={r['shape']}  line {r['line']}")
        if r["kind"] == "direct":
            if r["conditional_text_pieces"]:
                print(f"   {r['conditional_text_pieces']} `if` block(s) add text to this prompt: candidate conditions")
            if r.get("note"):
                print("   note:", r["note"])
            continue
        print(f"   prompt comes from parameters: {', '.join(r['prompt_parameters'])}")
        for c in r["callers"]:
            line = f"   caller {c['caller']} (line {c['line']}) [{c['role']}] passes {c['passes']}"
            if "key" in c:
                line += f" -> key {c['key']} shape {c['shape']}"
            print(line)
            for b in c.get("prompt_builders", []):
                print(f"       builds the prompt in {b['builder']} ({b['conditional_text_pieces']} text-adding `if` block(s))")
        for w in (r.get("warning"),):
            if w:
                print("   WARNING:", w)
        if r["proposed_calls"]:
            print("   proposed calls:", ", ".join(r["proposed_calls"]))
        if r["offline_calls"]:
            print("   likely offline (scope offline_script):", ", ".join(r["offline_calls"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("root")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--methods", help="comma-separated extra request method names")
    parser.add_argument("--exclude", help="comma-separated directory names to skip")
    args = parser.parse_args()
    methods = set(REQUEST_METHODS) | {m for m in (args.methods or "").split(",") if m}
    exclude = {d for d in (args.exclude or "").split(",") if d}
    result = analyze(args.root, methods, exclude)
    if args.json:
        json.dump(result, sys.stdout, indent=1)
        print()
    else:
        print_text(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
