#!/usr/bin/env python3
"""Check a `register_calls` payload before you send it.

    python3 check_payload.py payload.json [--json]

Reads the draft payload (a file, or `-` for standard input) and reports what Bench's
API would reject (errors) and what it would accept but probably is not what you meant
(warnings). Standard library only; it never contacts Bench and never runs the repo.
Exit status: 0 no errors, 1 errors, 2 the file could not be read.

The error rules mirror the API validator for call registrations: they check the form of
the payload, not whether the prompt text is right. Field names and types come from the
`register_calls` schema in the operation catalog; this script only enforces the rules
that the API enforces on top of it.
"""
from __future__ import annotations

import json
import re
import sys

MAX_CALLS = 100
MAX_FRAGMENT_BYTES = 64 << 10
MAX_TOTAL_BYTES = 2 << 20

KEY_RE = re.compile(r"^[A-Za-z0-9_./@+-]{1,300}::[A-Za-z0-9_.<>$-]{1,200}(#[0-9]{1,4})?$")
ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Fields the API reads, with the JSON type it expects. Unknown fields are ignored by
# the API without an error, which is why they are reported as warnings.
STR, INT, OBJ, LIST, STR_OR_LIST, ANY = "string", "integer", "object", "list", "string or list", "any"
SCHEMA = {
    "root": {"repo_full_name": STR, "branch": STR, "commit_sha": STR, "calls": LIST},
    "call": {"key": STR, "key_source": STR, "shape": STR, "name": STR, "scope": STR,
             "location": OBJ, "configurations": LIST, "fragments": LIST, "groups": LIST,
             "slots": LIST, "conditions": LIST, "constraints": LIST, "output_format": ANY,
             "verification": OBJ, "notes": STR_OR_LIST},
    "location": {"file": STR, "function": STR, "line": INT},
    "configuration": {"id": STR, "label": STR, "source": STR, "provider": STR, "model": STR,
                      "fallback": STR, "settings": OBJ, "tools": LIST, "tool_choice": STR,
                      "provenance": STR},
    "settings": {"declared": OBJ, "effective": OBJ, "effective_provenance": STR},
    "fragment": {"id": STR, "role": STR, "kind": STR, "text": STR, "text_provenance": STR,
                 "when": STR, "group": STR, "message_group": STR, "source": OBJ,
                 "variables": LIST},
    "source": {"file": STR, "line": INT},
    "variable": {"name": STR, "expr": STR, "kind": STR, "source": STR, "format_hint": STR,
                 "bound_to_fragment": STR},
    "group": {"id": STR, "header": STR, "separator": STR},
    "slot": {"kind": STR, "role": STR, "description": STR, "max_messages": INT,
             "after_fragment": STR},
    "condition": {"id": STR, "description": STR, "code_ref": STR},
    "constraint": {"type": STR, "conditions": LIST, "source": STR},
    "verification": {"level": STR, "evidence": OBJ},
    "evidence": {"method": STR, "states_checked": INT, "states_matched": INT, "notes": STR},
}


class Report:
    def __init__(self) -> None:
        self.errors: list[tuple[str, str]] = []
        self.warnings: list[tuple[str, str]] = []

    def error(self, path: str, message: str) -> None:
        self.errors.append((path, message))

    def warn(self, path: str, message: str) -> None:
        self.warnings.append((path, message))


def _type_ok(value, kind: str) -> bool:
    if kind == ANY:
        return True
    if kind == STR:
        return isinstance(value, str)
    if kind == INT:
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == OBJ:
        return isinstance(value, dict)
    if kind == LIST:
        return isinstance(value, list)
    if kind == STR_OR_LIST:
        return isinstance(value, str) or (isinstance(value, list) and all(isinstance(x, str) for x in value))
    return True


def typed(report: Report, obj, name: str, path: str) -> dict:
    """Return obj as a dict that is safe to read, reporting wrong types and unknown keys."""
    if not isinstance(obj, dict):
        report.error(path, f"must be an object, not {type(obj).__name__}")
        return {}
    spec = SCHEMA[name]
    clean = {}
    for key, value in obj.items():
        if key not in spec:
            report.warn(f"{path}.{key}", "is not a field of this object; the API ignores it")
            continue
        if value is None:
            continue
        if not _type_ok(value, spec[key]):
            report.error(f"{path}.{key}", f"must be a {spec[key]}")
            continue
        clean[key] = value
    return clean


def objects(report: Report, values, name: str, path: str) -> list[tuple[str, dict]]:
    out = []
    for i, v in enumerate(values or []):
        out.append((f"{path}[{i}]", typed(report, v, name, f"{path}[{i}]")))
    return out


# --- `when` expressions: condition ids with and, or, not and parentheses ---------------

def _tokens(expr: str) -> list[str]:
    toks, i = [], 0
    while i < len(expr):
        ch = expr[i]
        if ch.isspace():
            i += 1
        elif ch in "()":
            toks.append(ch)
            i += 1
        elif ch.isalpha() or ch == "_":
            j = i
            while j < len(expr) and (expr[j].isalnum() or expr[j] == "_"):
                j += 1
            toks.append(expr[i:j])
            i = j
        else:
            raise ValueError(f"unexpected character {ch!r}")
    if not toks:
        raise ValueError("is empty")
    return toks


def check_when(expr: str, declared: set[str]) -> str | None:
    try:
        toks = _tokens(expr)
    except ValueError as e:
        return str(e)
    pos = 0

    def peek() -> str:
        return toks[pos] if pos < len(toks) else ""

    def atom() -> None:
        nonlocal pos
        t = peek()
        if t == "":
            raise ValueError("ends too early")
        if t == "(":
            pos += 1
            disj()
            if peek() != ")":
                raise ValueError("missing closing parenthesis")
            pos += 1
            return
        if t in (")", "and", "or"):
            raise ValueError(f"unexpected {t!r}")
        if t not in declared:
            raise ValueError(f"{t!r} is not a declared condition")
        pos += 1

    def neg() -> None:
        nonlocal pos
        if peek() == "not":
            pos += 1
            neg()
        else:
            atom()

    def conj() -> None:
        nonlocal pos
        neg()
        while peek() == "and":
            pos += 1
            neg()

    def disj() -> None:
        nonlocal pos
        conj()
        while peek() == "or":
            pos += 1
            conj()

    try:
        disj()
        if pos != len(toks):
            raise ValueError(f"unexpected {toks[pos]!r}")
    except ValueError as e:
        return str(e)
    return None


# --- the checks ----------------------------------------------------------------------

def check(payload) -> Report:
    r = Report()
    root = typed(r, payload, "root", "payload")
    calls = root.get("calls") or []
    if not 1 <= len(calls) <= MAX_CALLS:
        r.error("calls", f"must contain 1 to {MAX_CALLS} entries")
    seen_keys: set[str] = set()
    total = 0
    for p, c in objects(r, calls, "call", "calls"):
        key = c.get("key", "")
        if not key:
            r.error(p + ".key", "is required")
        elif not KEY_RE.match(key) or ".." in key:
            r.error(p + ".key", "must look like <relative file>::<function>#<ordinal>")
        elif key in seen_keys:
            r.error(p + ".key", f"repeats {key!r}")
        elif "#" not in key:
            r.warn(p + ".key", "has no #<ordinal>; the skill asks for one (#0 for the first request)")
        seen_keys.add(key)
        source = c.get("key_source", "")
        if source not in ("", "ast", "agent"):
            r.error(p + ".key_source", "must be 'ast' or 'agent'")
        if source == "ast" and not c.get("shape"):
            r.error(p + ".shape", "is required when key_source is ast")
        elif not c.get("shape"):
            r.warn(p + ".shape", "is missing; the skill asks for a computed shape on every call "
                                 "(it is how Bench follows a call after a rename or move)")
        if not c.get("name", "").strip():
            r.error(p + ".name", "is required")
        if c.get("scope") not in ("production", "offline_script"):
            r.error(p + ".scope", "must be 'production' or 'offline_script'")
        total += check_configurations(r, p, c)
        total += check_fragments(r, p, c)
        check_conditions(r, p, c)
        check_verification(r, p, c)
        for sp, s in objects(r, c.get("slots"), "slot", p + ".slots"):
            if s.get("kind") not in ("history", "injected_context"):
                r.error(sp + ".kind", "must be history or injected_context")
        if isinstance(c.get("location"), dict):
            typed(r, c["location"], "location", p + ".location")
    if total > MAX_TOTAL_BYTES:
        r.error("calls", f"registered text is limited to {MAX_TOTAL_BYTES} bytes in total")
    return r


def check_configurations(r: Report, p: str, c: dict) -> int:
    code = 0
    for cp, cfg in objects(r, c.get("configurations"), "configuration", p + ".configurations"):
        src = cfg.get("source")
        if src == "code":
            code += 1
        elif src != "suggested":
            r.error(cp + ".source", "must be code or suggested")
        if not cfg.get("model", "").strip():
            r.error(cp + ".model", "is required")
        if isinstance(cfg.get("settings"), dict):
            typed(r, cfg["settings"], "settings", cp + ".settings")
    if code != 1:
        r.error(p + ".configurations", f"exactly one configuration must have source 'code' (found {code})")
    return 0


def check_fragments(r: Report, p: str, c: dict) -> int:
    fragments = objects(r, c.get("fragments"), "fragment", p + ".fragments")
    if not fragments:
        r.error(p + ".fragments", "must contain at least one fragment")
    groups = {g.get("id") for _, g in objects(r, c.get("groups"), "group", p + ".groups")}
    declared = {cd.get("id") for cd in (x for x in (c.get("conditions") or []) if isinstance(x, dict))}
    constants = {f.get("id") for _, f in fragments if f.get("kind") == "constant"}
    bound = {v.get("bound_to_fragment") for _, f in fragments
             for v in (x for x in (f.get("variables") or []) if isinstance(x, dict))
             if v.get("bound_to_fragment")}
    seen: set[str] = set()
    total = 0
    for fp, f in fragments:
        fid = f.get("id", "")
        if not ID_RE.match(fid):
            r.error(fp + ".id", "must be an identifier")
        elif fid in seen:
            r.error(fp + ".id", f"repeats {fid!r}")
        seen.add(fid)
        if f.get("role") not in ("system", "developer", "user", "assistant"):
            r.error(fp + ".role", "must be system, developer, user or assistant")
        text = f.get("text")
        total += len(text.encode()) if isinstance(text, str) else 0
        kind = f.get("kind")
        if kind in ("text", "constant"):
            if not isinstance(text, str) or not text.strip() or len(text.encode()) > MAX_FRAGMENT_BYTES:
                r.error(fp + ".text", f"must be 1 to {MAX_FRAGMENT_BYTES} bytes for kind {kind}")
            if kind == "constant" and fid not in bound:
                r.error(fp, f"constant {fid!r} is not bound to any variable (bound_to_fragment)")
        elif kind == "runtime_value":
            if text is not None:
                r.error(fp + ".text", "must be absent for kind runtime_value; the case supplies it")
        else:
            r.error(fp + ".kind", "must be text, runtime_value or constant")
        if f.get("text_provenance", "") not in ("", "resolved_string", "source_text"):
            r.error(fp + ".text_provenance", "must be resolved_string or source_text")
        if f.get("group") and f["group"] not in groups:
            r.error(fp + ".group", f"{f['group']!r} is not declared in groups")
        if f.get("when"):
            problem = check_when(f["when"], declared)
            if problem:
                r.error(fp + ".when", problem)
        names = set()
        for vp, v in objects(r, f.get("variables"), "variable", fp + ".variables"):
            if not ID_RE.match(v.get("name", "")):
                r.error(vp + ".name", "must be an identifier")
            names.add(v.get("name"))
            if v.get("bound_to_fragment") and v["bound_to_fragment"] not in constants:
                r.error(vp + ".bound_to_fragment", f"{v['bound_to_fragment']!r} is not a fragment of kind constant")
            if v.get("source", "") not in ("", "literal", "code", "command_output", "runtime_input"):
                r.error(vp + ".source", "must be literal, code, command_output or runtime_input")
        if kind == "text" and isinstance(text, str):
            missing = sorted({m for m in PLACEHOLDER_RE.findall(text) if m not in names})
            if missing:
                r.warn(fp + ".variables", "text has placeholders without a variable entry: " + ", ".join(missing))
    return total


def check_conditions(r: Report, p: str, c: dict) -> None:
    ids: set[str] = set()
    for cp, cd in objects(r, c.get("conditions"), "condition", p + ".conditions"):
        cid = cd.get("id", "")
        if not ID_RE.match(cid) or cid in ("and", "or", "not"):
            r.error(cp + ".id", "must be an identifier other than and, or, not")
        elif cid in ids:
            r.error(cp + ".id", f"repeats {cid!r}")
        ids.add(cid)
        if not cd.get("description", "").strip() or not cd.get("code_ref", "").strip():
            r.error(cp, "description and code_ref are required")
    for kp, k in objects(r, c.get("constraints"), "constraint", p + ".constraints"):
        if k.get("type") not in ("exactly_one", "not_both", "implies"):
            r.error(kp + ".type", "must be exactly_one, not_both or implies")
        if k.get("source") not in ("code", "user", "observed"):
            r.error(kp + ".source", "must be code, user or observed")
        named = [x for x in (k.get("conditions") or []) if isinstance(x, str)]
        if len(k.get("conditions") or []) < 2:
            r.error(kp + ".conditions", "must name at least two conditions (a single condition needs no constraint)")
        for cid in named:
            if cid not in ids:
                r.error(kp + ".conditions", f"{cid!r} is not a declared condition")


def check_verification(r: Report, p: str, c: dict) -> None:
    v = typed(r, c.get("verification", {}), "verification", p + ".verification") \
        if "verification" in c else {}
    level = v.get("level")
    evidence = typed(r, v["evidence"], "evidence", p + ".verification.evidence") if "evidence" in v else None
    if level == "agent_verified":
        checked = (evidence or {}).get("states_checked", 0)
        if not evidence or not evidence.get("method") or checked < 1 or evidence.get("states_matched") != checked:
            r.error(p + ".verification", "agent_verified needs evidence: a method and every checked state matched")
    elif level not in ("declared_only", "evidence_lacking"):
        r.error(p + ".verification.level", "must be agent_verified, declared_only or evidence_lacking")
    elif evidence and evidence.get("states_checked") and evidence.get("states_matched") != evidence["states_checked"]:
        r.warn(p + ".verification.evidence", "some checked states did not match; fix the fragments before registering")


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 2
    try:
        raw = sys.stdin.read() if args[0] == "-" else open(args[0], encoding="utf-8").read()
        payload = json.loads(raw)
    except (OSError, ValueError) as e:
        print(f"cannot read the payload: {e}")
        return 2
    report = check(payload)
    if "--json" in argv:
        print(json.dumps({"errors": [{"path": a, "message": b} for a, b in report.errors],
                          "warnings": [{"path": a, "message": b} for a, b in report.warnings]}, indent=2))
    else:
        for a, b in report.errors:
            print(f"ERROR   {a}: {b}")
        for a, b in report.warnings:
            print(f"WARNING {a}: {b}")
        print(f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)."
              " Errors would be rejected by Bench; this check does not judge whether the prompt text is right.")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
