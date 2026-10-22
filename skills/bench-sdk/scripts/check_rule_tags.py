"""check_rule_tags.py - parse and check the tag header on rule lines and call-profile lines (L1 draft).

A rules document keeps free text. Each rule is one bullet line that starts with a bracket header of
key=value pairs separated by ';':

  - [applies_to=all; basis=stated; source=src/app.py:41] Never give medical advice.
  - [applies_to=audience:end_user; basis=inferred; source=prompts/chat.md] Address the customer formally.
  - [applies_to=src/a.py::run#0,src/b.py::plan#1; basis=stated; source=src/a.py:12] Reply with only a JSON object.

Call-profile lines (one per registered call) use the same shape, in their own document:

  - [call=src/a.py::run#0; audience=end_user; basis=stated; source=src/a.py:12] role: answers customers; outcome: first-contact resolution

Rule keys:  applies_to (required: all | audience:end_user | audience:internal | audience:unknown | call keys joined by ','),
            basis (required: stated | inferred), source (required, non-empty).
Profile keys: call (required, one call key), audience (required: end_user | internal | unknown), basis, source (required).
Other keys (for example channel, locale, stakes, owner_confirmed) are kept and ignored, so a dimension can be added later.

A bullet without a header is 'untagged' (kept, flagged). A bullet whose header does not follow the grammar is 'malformed'
(kept, flagged, with the reason). Headings and prose are not rule lines. Nothing here drops a rule.
"""
from __future__ import annotations

import re

_HEADER = re.compile(r"^\s*[-*]\s+\[(?P<header>[^\]\n]*)\]\s*(?P<text>.*)$")
_BULLET = re.compile(r"^\s*[-*]\s+\S")
_PAIR = re.compile(r"^\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*?)\s*$")
_AUDIENCES = {"end_user", "internal", "unknown"}
_BASIS = {"stated", "inferred"}
_CALL_KEY = re.compile(r"^[^\s,;\[\]]+::[^\s,;\[\]]+$|^[^\s,;\[\]]+:\d+$")  # file::function with an optional #n (as register_calls accepts), or file:line for older keys


def _pairs(header: str) -> tuple[dict, str | None]:
    out: dict[str, str] = {}
    for part in header.split(";"):
        if not part.strip():
            continue
        m = _PAIR.match(part)
        if not m:
            return {}, f"not a key=value pair: {part.strip()[:40]!r}"
        key = m.group("key")
        if re.search(r"\s[A-Za-z_][A-Za-z0-9_]*\s*=", m.group("value")):
            return {}, f"missing ';' between pairs near {key}"
        if key in out:
            return {}, f"duplicate key {key}"
        out[key] = m.group("value")
    return out, None


def _check_applies_to(value: str) -> str | None:
    if value in ("all",) or value.startswith("audience:") and value[len("audience:"):] in _AUDIENCES:
        return None
    if value.startswith("audience:"):
        return f"unknown audience in applies_to: {value}"
    keys = [k.strip() for k in value.split(",")]
    bad = [k for k in keys if not _CALL_KEY.match(k)]
    return f"applies_to is not all, audience:<name> or call keys: {bad[0][:40]!r}" if bad else None


def parse_line(line: str, kind: str = "rule") -> dict | None:
    """None when the line is not a bullet. Otherwise {status: tagged|untagged|malformed, tags, reason, text}."""
    if not _BULLET.match(line):
        return None
    m = _HEADER.match(line)
    if not m:
        return {"status": "untagged", "tags": {}, "reason": "no header", "text": re.sub(r"^\s*[-*]\s+", "", line)}
    tags, err = _pairs(m.group("header"))
    text = m.group("text")
    if err:
        return {"status": "malformed", "tags": {}, "reason": err, "text": text}
    required = ("applies_to", "basis", "source") if kind == "rule" else ("call", "audience", "basis", "source")
    missing = [k for k in required if not tags.get(k)]
    if missing:
        return {"status": "malformed", "tags": tags, "reason": "missing " + ", ".join(missing), "text": text}
    if tags["basis"] not in _BASIS:
        return {"status": "malformed", "tags": tags, "reason": f"basis must be stated or inferred, got {tags['basis']!r}", "text": text}
    if kind == "rule":
        problem = _check_applies_to(tags["applies_to"])
    else:
        problem = None if tags["audience"] in _AUDIENCES else f"audience must be one of {sorted(_AUDIENCES)}"
        if problem is None and not _CALL_KEY.match(tags["call"]):
            problem = f"call is not a call key: {tags['call'][:40]!r}"
    if problem:
        return {"status": "malformed", "tags": tags, "reason": problem, "text": text}
    if not text.strip():
        return {"status": "malformed", "tags": tags, "reason": "no rule text after the header", "text": text}
    return {"status": "tagged", "tags": tags, "reason": "", "text": text}


def check_document(document: str, kind: str = "rule", registered_keys=None) -> dict:
    """Per bullet line: tagged, untagged or malformed. Labels and line numbers only; no rule text in the report."""
    report = {"tagged": [], "untagged": [], "malformed": [], "prose": [], "unresolved": []}
    for n, line in enumerate(document.splitlines(), start=1):
        r = parse_line(line, kind)
        if r is None:
            # Long prose that is not a heading may hold rules the check cannot see; reported, never counted as a rule.
            if len(line.strip()) >= 40 and not line.rstrip().endswith(":"):
                report["prose"].append({"line": n})
            continue
        entry = {"line": n} if r["status"] == "tagged" else {"line": n, "reason": r["reason"]}
        report[r["status"]].append(entry)
        if registered_keys is not None and r["status"] == "tagged":
            keys = call_keys(r["tags"]) if kind == "rule" else [r["tags"].get("call", "")]
            bad = [k for k in keys if k not in set(registered_keys)]
            if bad:
                report["unresolved"].append({"line": n, "keys": len(bad)})
    report["counts"] = {k: len(report[k]) for k in ("tagged", "untagged", "malformed", "prose", "unresolved")}
    return report


def call_keys(tags: dict) -> list[str]:
    """The call keys a rule lists in applies_to (empty for all and audience:...)."""
    value = tags.get("applies_to", "")
    if value == "all" or value.startswith("audience:"):
        return []
    return [k.strip() for k in value.split(",") if k.strip()]


def unresolved_keys(tags: dict, registered_keys) -> list[str]:
    """Listed call keys that match no registered call (for example a key rewritten by redaction)."""
    return [k for k in call_keys(tags) if k not in set(registered_keys or [])]


def applies(tags: dict, call_key: str, call_audience: str | None, registered_keys=None) -> str:
    """'yes' | 'no' | 'unclear' for one tagged rule and one evaluated call. 'no' only when the tag is stated and call-specific
    or audience-specific; an inferred tag, an unknown audience, an untagged rule, or a listed key that matches no registered
    call (when registered_keys is given) is 'unclear', never 'no'."""
    value = tags.get("applies_to", "")
    if value == "all":
        return "yes"
    if value.startswith("audience:"):
        want = value[len("audience:"):]
        if call_audience in (None, "unknown") or want == "unknown":
            return "unclear"
        return "yes" if want == call_audience else ("no" if tags.get("basis") == "stated" else "unclear")
    keys = call_keys(tags)
    if call_key in keys:
        return "yes"
    if registered_keys is not None and unresolved_keys(tags, registered_keys):
        return "unclear"
    return "no" if tags.get("basis") == "stated" else "unclear"


def main(argv: list[str]) -> int:
    """python3 check_rule_tags.py <file> [--profile]: print counts and line numbers, never rule text. Exit 1 when a bullet is malformed."""
    import json
    import sys
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print("usage: check_rule_tags.py <file> [--profile]", file=sys.stderr)
        return 2
    with open(args[0], encoding="utf-8") as handle:
        report = check_document(handle.read(), "profile" if "--profile" in argv else "rule")
    print(json.dumps(report, indent=2))
    return 1 if report["malformed"] else 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
