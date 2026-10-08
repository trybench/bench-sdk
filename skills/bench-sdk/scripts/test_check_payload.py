"""Tests for check_payload.py on invented payloads (no real prompt text).

Run: python3 -m unittest skills/bench-sdk/scripts/test_check_payload.py

Set CHECK_PAYLOAD_DUMP=<dir> to write every case as <name>.json plus expected.json
(case name -> sorted error paths), so the same payloads can be run through the API's
validator and the two compared (see the note at the end of this file).
"""
import copy
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import check_payload as cp  # noqa: E402


def base():
    return {"repo_full_name": "acme/shop", "branch": "main", "calls": [{
        "key": "src/pricing.py::quote#0", "key_source": "ast", "shape": "a1b2c3d4e5",
        "name": "price quote", "scope": "production",
        "configurations": [{"source": "code", "provider": "openai", "model": "model-x"}],
        "fragments": [
            {"id": "system", "role": "system", "kind": "text", "text": "You write price quotes.",
             "text_provenance": "resolved_string"},
            {"id": "extra", "role": "system", "kind": "text", "text": " Mention {currency}.",
             "when": "rush", "variables": [{"name": "currency", "expr": "cur", "source": "code"}]},
            {"id": "user", "role": "user", "kind": "runtime_value"}],
        "conditions": [{"id": "rush", "description": "rush order", "code_ref": "if rush:"},
                       {"id": "bulk", "description": "bulk order", "code_ref": "if bulk:"}],
        "constraints": [{"type": "not_both", "conditions": ["rush", "bulk"], "source": "code"}],
        "verification": {"level": "agent_verified",
                         "evidence": {"method": "ran the builder", "states_checked": 2, "states_matched": 2}},
        "notes": "one note as a string"}]}


def mutate(fn):
    p = base()
    fn(p["calls"][0], p)
    return p


# name -> (payload, expected error paths). Warnings are tested separately.
CASES = {
    "valid": (base(), []),
    "no_calls": (mutate(lambda c, p: p.update(calls=[])), ["calls"]),
    "key_missing": (mutate(lambda c, p: c.pop("key")), ["calls[0].key"]),
    "key_bad_format": (mutate(lambda c, p: c.update(key="no separator")), ["calls[0].key"]),
    "key_absolute_parent": (mutate(lambda c, p: c.update(key="../x.py::f#0")), ["calls[0].key"]),
    "ast_without_shape": (mutate(lambda c, p: c.pop("shape")), ["calls[0].shape"]),
    "key_source_unknown": (mutate(lambda c, p: c.update(key_source="guess")), ["calls[0].key_source"]),
    "name_blank": (mutate(lambda c, p: c.update(name="  ")), ["calls[0].name"]),
    "scope_unknown": (mutate(lambda c, p: c.update(scope="prod")), ["calls[0].scope"]),
    "two_code_configurations": (mutate(lambda c, p: c["configurations"].append(dict(c["configurations"][0]))),
                                ["calls[0].configurations"]),
    "no_model": (mutate(lambda c, p: c["configurations"][0].pop("model")), ["calls[0].configurations[0].model"]),
    "configuration_source_unknown": (mutate(lambda c, p: c["configurations"][0].update(source="declared")),
                                     ["calls[0].configurations", "calls[0].configurations[0].source"]),
    "no_fragments": (mutate(lambda c, p: c.update(fragments=[])), ["calls[0].fragments"]),
    "fragment_id_repeats": (mutate(lambda c, p: c["fragments"][1].update(id="system")),
                            ["calls[0].fragments[1].id"]),
    "fragment_role_unknown": (mutate(lambda c, p: c["fragments"][0].update(role="human")),
                              ["calls[0].fragments[0].role"]),
    "text_on_runtime_value": (mutate(lambda c, p: c["fragments"][2].update(text="hello")),
                              ["calls[0].fragments[2].text"]),
    "text_kind_without_text": (mutate(lambda c, p: c["fragments"][0].pop("text")),
                               ["calls[0].fragments[0].text"]),
    "kind_unknown": (mutate(lambda c, p: c["fragments"][0].update(kind="template")),
                     ["calls[0].fragments[0].kind"]),
    "provenance_unknown": (mutate(lambda c, p: c["fragments"][0].update(text_provenance="guess")),
                           ["calls[0].fragments[0].text_provenance"]),
    "group_not_declared": (mutate(lambda c, p: c["fragments"][0].update(group="g")),
                           ["calls[0].fragments[0].group"]),
    "when_unknown_condition": (mutate(lambda c, p: c["fragments"][1].update(when="rush and nope")),
                               ["calls[0].fragments[1].when"]),
    "when_bad_syntax": (mutate(lambda c, p: c["fragments"][1].update(when="rush &&")),
                        ["calls[0].fragments[1].when"]),
    "constant_not_bound": (mutate(lambda c, p: c["fragments"].append(
        {"id": "shots", "role": "user", "kind": "constant", "text": "example"})),
        ["calls[0].fragments[3]"]),
    "variable_source_unknown": (mutate(lambda c, p: c["fragments"][1]["variables"][0].update(source="magic")),
                                ["calls[0].fragments[1].variables[0].source"]),
    "variable_bound_to_non_constant": (mutate(lambda c, p: c["fragments"][1]["variables"][0].update(
        bound_to_fragment="system")), ["calls[0].fragments[1].variables[0].bound_to_fragment"]),
    "constraint_one_condition": (mutate(lambda c, p: c["constraints"][0].update(conditions=["rush"])),
                                 ["calls[0].constraints[0].conditions"]),
    "constraint_unknown_condition": (mutate(lambda c, p: c["constraints"][0].update(conditions=["rush", "x"])),
                                     ["calls[0].constraints[0].conditions"]),
    "constraint_type_unknown": (mutate(lambda c, p: c["constraints"][0].update(type="xor")),
                                ["calls[0].constraints[0].type"]),
    "condition_reserved_id": (mutate(lambda c, p: c["conditions"][0].update(id="and")),
                              ["calls[0].conditions[0].id", "calls[0].constraints[0].conditions",
                               "calls[0].fragments[1].when"]),
    "condition_without_code_ref": (mutate(lambda c, p: c["conditions"][0].update(code_ref="")),
                                   ["calls[0].conditions[0]"]),
    "agent_verified_without_evidence": (mutate(lambda c, p: c["verification"].pop("evidence")),
                                        ["calls[0].verification"]),
    "agent_verified_zero_states": (mutate(lambda c, p: c["verification"].update(
        evidence={"method": "m", "states_checked": 0, "states_matched": 0})), ["calls[0].verification"]),
    "agent_verified_mismatch": (mutate(lambda c, p: c["verification"]["evidence"].update(states_matched=1)),
                                ["calls[0].verification"]),
    "verification_level_unknown": (mutate(lambda c, p: c["verification"].update(level="verified")),
                                   ["calls[0].verification.level"]),
    "slot_kind_unknown": (mutate(lambda c, p: c.update(slots=[{"kind": "memory"}])),
                          ["calls[0].slots[0].kind"]),
}

# Wrong JSON types: the API rejects the whole body when a field has the wrong type.
TYPE_CASES = {
    "notes_as_number": (mutate(lambda c, p: c.update(notes=5)), ["calls[0].notes"]),
    "name_as_list": (mutate(lambda c, p: c.update(name=["a"])), ["calls[0].name"]),
    "calls_as_object": ({"calls": {}}, ["calls", "payload.calls"]),
    "line_as_text": (mutate(lambda c, p: c.update(location={"line": "12"})), ["calls[0].location.line"]),
}


def errors_of(payload):
    return sorted({a for a, _ in cp.check(payload).errors})


class CheckPayloadTests(unittest.TestCase):
    def test_every_case_reports_exactly_the_expected_error_paths(self):
        for name, (payload, expected) in CASES.items():
            with self.subTest(name):
                self.assertEqual(errors_of(copy.deepcopy(payload)), sorted(expected))

    def test_wrong_types_are_errors(self):
        for name, (payload, expected) in TYPE_CASES.items():
            with self.subTest(name):
                self.assertEqual(errors_of(copy.deepcopy(payload)), sorted(expected))

    def test_notes_may_be_a_string_or_a_list(self):
        self.assertEqual(errors_of(mutate(lambda c, p: c.update(notes=["a", "b"]))), [])

    def test_missing_shape_on_an_agent_key_is_a_warning_not_an_error(self):
        p = mutate(lambda c, p: (c.pop("shape"), c.update(key_source="agent")))
        r = cp.check(p)
        self.assertEqual(r.errors, [])
        self.assertIn("calls[0].shape", [a for a, _ in r.warnings])

    def test_key_without_ordinal_is_a_warning(self):
        r = cp.check(mutate(lambda c, p: c.update(key="src/a.py::f")))
        self.assertEqual(r.errors, [])
        self.assertIn("calls[0].key", [a for a, _ in r.warnings])

    def test_fields_the_api_ignores_are_warnings(self):
        r = cp.check(mutate(lambda c, p: c.update(model="x", output_format_typo=1)))
        self.assertEqual(r.errors, [])
        self.assertEqual(sorted(a for a, _ in r.warnings), ["calls[0].model", "calls[0].output_format_typo"])

    def test_placeholder_without_variable_is_a_warning(self):
        r = cp.check(mutate(lambda c, p: c["fragments"][0].update(text="Hello {customer}.")))
        self.assertEqual(r.errors, [])
        self.assertIn("calls[0].fragments[0].variables", [a for a, _ in r.warnings])

    def test_json_braces_are_not_placeholders(self):
        r = cp.check(mutate(lambda c, p: c["fragments"][0].update(text='Reply as {"total": 1}.')))
        self.assertEqual(r.warnings, [])

    def test_valid_payload_has_no_warnings(self):
        self.assertEqual(cp.check(base()).warnings, [])

    def test_command_line_exit_codes(self):
        import subprocess, tempfile
        script = str(Path(__file__).parent / "check_payload.py")
        with tempfile.TemporaryDirectory() as d:
            good, bad = Path(d, "good.json"), Path(d, "bad.json")
            good.write_text(json.dumps(base()))
            bad.write_text(json.dumps(mutate(lambda c, p: c.pop("key"))))
            run = lambda *a: subprocess.run([sys.executable, script, *a], capture_output=True, text=True)
            self.assertEqual(run(str(good)).returncode, 0)
            out = run(str(bad))
            self.assertEqual(out.returncode, 1)
            self.assertIn("calls[0].key", out.stdout)
            self.assertEqual(run(str(Path(d, "missing.json"))).returncode, 2)
            as_json = json.loads(run(str(bad), "--json").stdout)
            self.assertEqual(as_json["errors"][0]["path"], "calls[0].key")

    def test_the_skill_tells_agents_to_run_the_checker(self):
        skill = Path(__file__).parent.parent
        self.assertIn("scripts/check_payload.py", (skill / "SKILL.md").read_text())
        self.assertIn("scripts/check_payload.py", (skill / "references" / "after-registering.md").read_text())

    def test_dump_cases_for_comparison_with_the_api_validator(self):
        out = os.environ.get("CHECK_PAYLOAD_DUMP")
        if not out:
            self.skipTest("set CHECK_PAYLOAD_DUMP=<dir> to write the cases")
        Path(out).mkdir(parents=True, exist_ok=True)
        for name, (payload, _) in CASES.items():
            Path(out, name + ".json").write_text(json.dumps(payload))
        Path(out, "expected.json").write_text(json.dumps({n: sorted(e) for n, (_, e) in CASES.items()}))


# Parity with the API validator (bench-api internal/callunit): dump the cases, decode each
# with callunit.Registration, run callunit.Validate, and compare the error paths with
# expected.json. The two lists must be equal for every case in CASES (TYPE_CASES are
# decode errors in the API, not validation errors).

if __name__ == "__main__":
    unittest.main()
