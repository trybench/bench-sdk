"""Tests for check_rule_tags.py. Invented text only; no customer content.

Run: python3 -m unittest skills/bench-sdk/scripts/test_check_rule_tags.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import check_rule_tags as rt  # noqa: E402

GOOD = "- [applies_to=all; basis=stated; source=src/app.py:41] Never give medical advice."
CALL_A = "src/a.py::run#0"
CALL_B = "src/b.py::plan#1"


class ParseLine(unittest.TestCase):
    def test_valid_all(self):
        r = rt.parse_line(GOOD)
        self.assertEqual(r["status"], "tagged")
        self.assertEqual(r["tags"]["applies_to"], "all")
        self.assertEqual(r["text"], "Never give medical advice.")

    def test_valid_audience_and_call_keys(self):
        self.assertEqual(rt.parse_line("- [applies_to=audience:end_user; basis=inferred; source=p.md] Be formal.")["status"], "tagged")
        line = f"- [applies_to={CALL_A},{CALL_B}; basis=stated; source=a.py:1] Reply with JSON only."
        self.assertEqual(rt.parse_line(line)["status"], "tagged")

    def test_extra_keys_are_kept_and_ignored(self):
        r = rt.parse_line("- [applies_to=all; basis=stated; source=x.py; channel=voice; stakes=high] Keep it short.")
        self.assertEqual(r["status"], "tagged")
        self.assertEqual(r["tags"]["channel"], "voice")

    def test_untagged_bullet(self):
        r = rt.parse_line("- Use only information from tools.")
        self.assertEqual(r["status"], "untagged")

    def test_not_a_bullet(self):
        self.assertIsNone(rt.parse_line("Answer rules:"))
        self.assertIsNone(rt.parse_line("Plain prose line that is not a rule."))
        self.assertIsNone(rt.parse_line(""))

    def test_malformed_reasons(self):
        cases = {
            "- [applies_to=all; basis=stated] Missing source.": "missing source",
            "- [applies_to=all; basis=sure; source=x.py] Bad basis.": "basis must be",
            "- [applies_to=sometimes; basis=stated; source=x.py] Bad scope.": "applies_to is not",
            "- [applies_to=audience:robots; basis=stated; source=x.py] Bad audience.": "unknown audience",
            "- [applies_to=all; basis=stated; source=x.py; source=y.py] Duplicate.": "duplicate key",
            "- [applies_to=all basis=stated] Not pairs.": "missing ';'",
            "- [applies_to=all; basis=stated; just words] Not a pair.": "not a key=value pair",
            "- [applies_to=all; basis=stated; source=x.py]": "no rule text",
            "- [] Empty header.": "missing",
        }
        for line, fragment in cases.items():
            r = rt.parse_line(line)
            self.assertEqual(r["status"], "malformed", line)
            self.assertIn(fragment, r["reason"], line)

    def test_profile_lines(self):
        ok = f"- [call={CALL_A}; audience=end_user; basis=stated; source=a.py:12] role: answers customers; outcome: resolution"
        self.assertEqual(rt.parse_line(ok, "profile")["status"], "tagged")
        bad_audience = f"- [call={CALL_A}; audience=everyone; basis=stated; source=a.py:12] role: x"
        self.assertEqual(rt.parse_line(bad_audience, "profile")["status"], "malformed")
        bad_call = "- [call=not a key; audience=internal; basis=stated; source=a.py:12] role: x"
        self.assertEqual(rt.parse_line(bad_call, "profile")["status"], "malformed")


class CheckDocument(unittest.TestCase):
    DOC = "\n".join([
        "Rules the system must follow:",
        GOOD,
        "- Use only information from tools.",
        "- [applies_to=all; basis=maybe; source=x.py] Bad basis.",
        "Output contracts:",
        "- [applies_to=audience:internal; basis=inferred; source=x.py] Return an id.",
    ])

    def test_counts_and_lines(self):
        rep = rt.check_document(self.DOC)
        self.assertEqual(rep["counts"], {"tagged": 2, "untagged": 1, "malformed": 1, "prose": 0, "unresolved": 0})
        self.assertEqual([e["line"] for e in rep["untagged"]], [3])
        self.assertEqual([e["line"] for e in rep["malformed"]], [4])

    def test_report_holds_no_rule_text(self):
        rep = str(rt.check_document(self.DOC))
        self.assertNotIn("medical advice", rep)
        self.assertNotIn("information from tools", rep)

    def test_untagged_document_is_all_untagged(self):
        rep = rt.check_document("- one rule\n- another rule\n")
        self.assertEqual(rep["counts"], {"tagged": 0, "untagged": 2, "malformed": 0, "prose": 0, "unresolved": 0})


class Prose(unittest.TestCase):
    def test_long_prose_is_reported_but_not_a_rule(self):
        doc = "Output contracts: the recipe parser returns only valid JSON and nothing else at all.\nHeading:\n- [applies_to=all; basis=stated; source=x.py] A rule."
        rep = rt.check_document(doc)
        self.assertEqual(rep["counts"], {"tagged": 1, "untagged": 0, "malformed": 0, "prose": 1, "unresolved": 0})

    def test_headings_and_short_lines_are_ignored(self):
        rep = rt.check_document("Answer rules:\nShort line.\n")
        self.assertEqual(rep["counts"]["prose"], 0)


class Applies(unittest.TestCase):
    def tags(self, value, basis="stated"):
        return {"applies_to": value, "basis": basis, "source": "x"}

    def test_all_applies_everywhere(self):
        self.assertEqual(rt.applies(self.tags("all"), CALL_A, "end_user"), "yes")

    def test_call_key_match_and_stated_miss(self):
        self.assertEqual(rt.applies(self.tags(CALL_A), CALL_A, "internal"), "yes")
        self.assertEqual(rt.applies(self.tags(CALL_A), CALL_B, "internal"), "no")

    def test_inferred_miss_is_unclear_never_no(self):
        self.assertEqual(rt.applies(self.tags(CALL_A, "inferred"), CALL_B, "internal"), "unclear")
        self.assertEqual(rt.applies(self.tags("audience:end_user", "inferred"), CALL_B, "internal"), "unclear")

    def test_audience_rules(self):
        self.assertEqual(rt.applies(self.tags("audience:end_user"), CALL_A, "end_user"), "yes")
        self.assertEqual(rt.applies(self.tags("audience:end_user"), CALL_A, "internal"), "no")

    def test_unknown_audience_never_removes_a_rule(self):
        self.assertEqual(rt.applies(self.tags("audience:end_user"), CALL_A, "unknown"), "unclear")
        self.assertEqual(rt.applies(self.tags("audience:end_user"), CALL_A, None), "unclear")


class UnresolvedKeys(unittest.TestCase):
    REG = [CALL_A, CALL_B]

    def tags(self, value, basis="stated"):
        return {"applies_to": value, "basis": basis, "source": "x"}

    def test_stated_tag_with_unregistered_key_is_unclear_not_no(self):
        # a key that no registered call has (for example one rewritten by redaction) must not hide the rule
        self.assertEqual(rt.applies(self.tags("src/gone.py::run#0"), CALL_A, "end_user", self.REG), "unclear")

    def test_without_registered_keys_behaviour_is_unchanged(self):
        self.assertEqual(rt.applies(self.tags(CALL_B), CALL_A, "end_user"), "no")

    def test_resolved_miss_is_still_no_for_stated(self):
        self.assertEqual(rt.applies(self.tags(CALL_B), CALL_A, "end_user", self.REG), "no")

    def test_listed_key_that_matches_the_call_is_yes_even_if_another_is_unresolved(self):
        self.assertEqual(rt.applies(self.tags(CALL_A + ",src/gone.py::run#0"), CALL_A, "end_user", self.REG), "yes")

    def test_unresolved_with_mixed_keys_is_unclear_for_another_call(self):
        self.assertEqual(rt.applies(self.tags(CALL_A + ",src/gone.py::run#0"), CALL_B, "internal", self.REG), "unclear")

    def test_unresolved_keys_helper(self):
        self.assertEqual(rt.unresolved_keys(self.tags(CALL_A + ",src/gone.py::run#0"), self.REG), ["src/gone.py::run#0"])
        self.assertEqual(rt.unresolved_keys(self.tags("all"), self.REG), [])

    def test_redacted_key_is_malformed_and_therefore_kept_and_flagged(self):
        line = "- [applies_to=src/de[REDACTED].py::run#0; basis=stated; source=x.py] A rule."
        self.assertEqual(rt.parse_line(line)["status"], "malformed")

    def test_check_document_reports_unresolved_lines(self):
        doc = f"- [applies_to={CALL_A}; basis=stated; source=a.py] ok\n- [applies_to=src/gone.py::run#0; basis=stated; source=a.py] stale key\n"
        rep = rt.check_document(doc, "rule", registered_keys=self.REG)
        self.assertEqual([e["line"] for e in rep["unresolved"]], [2])
        self.assertEqual(rep["counts"]["tagged"], 2)
        self.assertEqual(rt.check_document(doc)["counts"]["unresolved"], 0)

    def test_profile_with_unregistered_call_is_reported(self):
        doc = "- [call=src/gone.py::run#0; audience=internal; basis=stated; source=a.py] role: x"
        rep = rt.check_document(doc, "profile", registered_keys=self.REG)
        self.assertEqual(len(rep["unresolved"]), 1)


class CallKeyShapes(unittest.TestCase):
    def test_key_without_ordinal_is_valid(self):
        # register_calls accepts the "#n" suffix as optional
        self.assertEqual(rt.parse_line("- [applies_to=src/a.py::run; basis=stated; source=a.py] A rule.")["status"], "tagged")

    def test_key_with_angle_brackets_and_dollar_is_valid(self):
        self.assertEqual(rt.parse_line("- [applies_to=src/a.py::<lambda>#0; basis=stated; source=a.py] A rule.")["status"], "tagged")

    def test_older_file_line_key_is_valid(self):
        self.assertEqual(rt.parse_line("- [applies_to=src/a.py:178; basis=stated; source=a.py] A rule.")["status"], "tagged")

    def test_key_with_spaces_or_missing_function_is_malformed(self):
        self.assertEqual(rt.parse_line("- [applies_to=src/a.py::run now; basis=stated; source=a.py] A rule.")["status"], "malformed")
        self.assertEqual(rt.parse_line("- [applies_to=src/a.py; basis=stated; source=a.py] A rule.")["status"], "malformed")


if __name__ == "__main__":
    unittest.main()
