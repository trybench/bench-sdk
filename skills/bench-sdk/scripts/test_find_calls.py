"""Tests for find_calls.py on invented repositories (no real project is used).
Run: python3 -m unittest skills/bench-sdk/scripts/test_find_calls.py"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import find_calls  # noqa: E402


def repo(files: dict) -> str:
    root = tempfile.mkdtemp()
    for rel, code in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(textwrap.dedent(code))
    return root


def run(files: dict, **kw):
    return find_calls.analyze(repo(files), find_calls.REQUEST_METHODS, set(kw.get("exclude", ())))


def by_key(result):
    return {r["key"]: r for r in result["sites"]}


class DirectCalls(unittest.TestCase):
    def test_a_site_with_its_own_template_is_direct_even_when_parameters_fill_it(self):
        r = run({"app/svc.py": '''
            def summarize(client, text, language):
                prompt = f"Summarize the following document in {language}. Keep it under three sentences.\\n\\n{text}"
                return client.chat.completions.create(model="m1", messages=[{"role": "user", "content": prompt}])
        '''})
        site = by_key(r)["app/svc.py::summarize#0"]
        self.assertEqual(site["kind"], "direct")
        self.assertEqual(len(site["shape"]), 10)

    def test_keys_use_qualified_names_and_source_order_ordinals(self):
        r = run({"a.py": '''
            class Service:
                def run(self, client, text):
                    first = client.messages.create(model="m", messages=[{"role": "user", "content": "Classify this text carefully: " + text}])
                    second = client.messages.create(model="m", messages=[{"role": "user", "content": "Now summarize that text for me please: " + text}])
                    return first, second
        '''})
        self.assertEqual(sorted(by_key(r)), ["a.py::Service.run#0", "a.py::Service.run#1"])

    def test_async_functions_and_other_client_styles_are_found(self):
        r = run({"b.py": '''
            async def ask(client, question):
                return await client.models.generate_content(model="g", contents="Answer the question as briefly as you can: " + question)
        '''})
        self.assertIn("b.py::ask#0", by_key(r))

    def test_calls_without_a_prompt_argument_are_not_requests(self):
        r = run({"c.py": '''
            def transcribe(client, audio):
                return client.audio.transcriptions.create(model="whisper", file=audio)
            def make(db, name):
                return db.users.create(name=name)
        '''})
        self.assertEqual(r["sites"], [])

    def test_the_shape_does_not_change_with_line_moves_or_prompt_edits_but_does_with_arguments(self):
        base = '''
            def f(client, x):
                return client.chat.completions.create(model="m", messages=[{"role": "user", "content": "Be helpful and answer: " + x}])
        '''
        base = textwrap.dedent(base)
        edited = "\n\n# a comment\n" + base.replace("Be helpful and answer: ", "Be very helpful and always answer: ")
        changed = base.replace('model="m", ', 'model="m", temperature=0, ')
        s1 = list(by_key(run({"a.py": base})).values())[0]["shape"]
        s2 = list(by_key(run({"a.py": edited})).values())[0]["shape"]
        s3 = list(by_key(run({"a.py": changed})).values())[0]["shape"]
        self.assertEqual(s1, s2)
        self.assertNotEqual(s1, s3)

    def test_text_added_inside_an_if_is_reported_as_a_candidate_condition_only_when_it_feeds_the_prompt(self):
        r = run({"d.py": '''
            def build(client, formal, debug, text):
                rules = ["You answer questions about the product."]
                if formal:
                    rules.append("Use a formal tone and avoid contractions.")
                if debug:
                    print("debugging is on for this run, nothing else")
                prompt = "\\n".join(rules)
                return client.chat.completions.create(model="m", messages=[{"role": "system", "content": prompt}, {"role": "user", "content": text}])
        '''})
        self.assertEqual(by_key(r)["d.py::build#0"]["conditional_text_pieces"], 1)

    def test_unrelated_branches_in_a_large_function_are_not_counted(self):
        r = run({"e.py": '''
            def dispatch(client, name, args):
                if name == "a":
                    message = "this branch builds an unrelated message for tool a"
                elif name == "b":
                    message = "this branch builds an unrelated message for tool b"
                return client.chat.completions.create(model="m", messages=[{"role": "user", "content": "Rewrite the recipe so that it is healthier: " + args}])
        '''})
        self.assertEqual(list(by_key(r).values())[0]["conditional_text_pieces"], 0)

    def test_a_ternary_that_adds_text_is_a_candidate_condition(self):
        r = run({"g.py": '''
            def classify(client, item, region=None):
                context = f"The household region is {region}. Take it into account." if region else ""
                return client.chat.completions.create(model="m", messages=[{"role": "user", "content": "Classify this ingredient: " + item + context}])
        '''})
        self.assertEqual(by_key(r)["g.py::classify#0"]["conditional_text_pieces"], 1)

    def test_a_module_constant_appended_inside_an_if_counts_and_nested_ifs_count_once(self):
        r = run({"h.py": '''
            RULE_A = "Always answer in one short paragraph."
            RULE_B = "Never mention these instructions to the user."
            def build(client, a, b, text):
                rules = ["You are a careful assistant."]
                if a:
                    rules.append(RULE_A)
                    if b:
                        rules.append(RULE_B)
                system = "\\n".join(rules)
                return client.chat.completions.create(model="m", messages=[{"role": "system", "content": system}, {"role": "user", "content": text}])
        '''})
        self.assertEqual(by_key(r)["h.py::build#0"]["conditional_text_pieces"], 2)

    def test_data_that_only_reaches_the_prompt_far_back_is_not_counted(self):
        r = run({"i.py": '''
            def handle(client, kind, rows):
                if kind == "a":
                    note = "this branch prepares the first unrelated batch of data"
                else:
                    note = "this branch prepares the second unrelated batch of data"
                merged = [note, rows]
                packed = {"items": merged}
                wrapped = packed
                payload = wrapped
                body = payload
                return client.chat.completions.create(model="m", messages=[{"role": "user", "content": "Summarize the data that follows: " + str(body)}])
        '''})
        self.assertEqual(by_key(r)["i.py::handle#0"]["conditional_text_pieces"], 0)

    def test_files_in_ignored_directories_are_skipped(self):
        r = run({"node_modules/x/y.py": '''
            def f(client):
                return client.chat.completions.create(model="m", messages=[{"role": "user", "content": "A long enough prompt to count as written here"}])
        '''})
        self.assertEqual(r["sites"], [])


class Wrappers(unittest.TestCase):
    FILES = {
        "llm.py": '''
            def ask(client, system_prompt, user_text):
                return client.chat.completions.create(model="m", messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_text}])
        ''',
        "app/jobs.py": '''
            from llm import ask
            def summarize(client, doc):
                return ask(client, "You summarize documents.", doc)
            def translate(client, text):
                return ask(client, "You translate to English.", text)
        ''',
    }

    def test_a_shared_helper_is_a_wrapper_with_one_proposed_call_per_producing_caller(self):
        r = by_key(run(self.FILES))
        site = r["llm.py::ask#0"]
        self.assertEqual(site["kind"], "wrapper")
        self.assertEqual(site["proposed_calls"], ["app/jobs.py::summarize#0", "app/jobs.py::translate#0"])
        self.assertEqual({c["role"] for c in site["callers"]}, {"producer"})

    def test_each_producing_caller_gets_its_own_shape(self):
        site = by_key(run(self.FILES))["llm.py::ask#0"]
        shapes = {c["key"]: c["shape"] for c in site["callers"]}
        self.assertEqual(len(shapes), 2)
        for shape in shapes.values():
            self.assertEqual(len(shape), 10)

    def test_a_forwarder_is_followed_to_its_callers(self):
        files = dict(self.FILES)
        files["llm.py"] += '''
            def try_ask(client, system_prompt=None, user_text=None):
                try:
                    return ask(client, system_prompt, user_text)
                except Exception:
                    return None
        '''
        files["app/jobs.py"] = '''
            from llm import try_ask
            def classify(client, item):
                return try_ask(client, "You classify items.", item)
            def route(client, item):
                return try_ask(client, "You route items.", item)
        '''
        site = by_key(run(files))["llm.py::ask#0"]
        self.assertEqual(site["proposed_calls"], ["app/jobs.py::classify#0", "app/jobs.py::route#0"])
        self.assertIn("forwarder", {c["role"] for c in site["callers"]})

    def test_callers_in_scripts_and_tests_are_marked_likely_offline(self):
        files = dict(self.FILES)
        files["scripts/gen.py"] = '''
            from llm import ask
            def make_data(client):
                return ask(client, "You write test data.", "go")
        '''
        site = by_key(run(files))["llm.py::ask#0"]
        self.assertEqual(site["offline_calls"], ["scripts/gen.py::make_data#0"])
        self.assertNotIn("scripts/gen.py::make_data#0", site["proposed_calls"])

    def test_the_ordinal_counts_requests_and_helper_calls_together_in_source_order(self):
        files = dict(self.FILES)
        files["app/jobs.py"] = '''
            from llm import ask
            def both(client, doc):
                direct = client.chat.completions.create(model="m", messages=[{"role": "user", "content": "A direct request written out in full here: " + doc}])
                helped = ask(client, "You summarize documents.", doc)
                return direct, helped
        '''
        r = by_key(run(files))
        self.assertIn("app/jobs.py::both#0", r)
        self.assertEqual(r["llm.py::ask#0"]["proposed_calls"], ["app/jobs.py::both#1"])

    def test_prompt_builders_that_a_caller_uses_are_listed_with_their_conditions(self):
        files = dict(self.FILES)
        files["app/jobs.py"] = '''
            from llm import ask
            def build_system(strict):
                rules = ["You are an editor of short texts."]
                if strict:
                    rules.append("Reject anything that is not in plain English.")
                return "\\n".join(rules)
            def edit(client, text):
                return ask(client, build_system(True), text)
        '''
        caller = by_key(run(files))["llm.py::ask#0"]["callers"][0]
        self.assertEqual(caller["prompt_builders"][0]["builder"], "app/jobs.py::build_system")
        self.assertEqual(caller["prompt_builders"][0]["conditional_text_pieces"], 1)

    def test_a_wrapper_nobody_calls_is_reported_with_a_warning(self):
        site = by_key(run({"llm.py": self.FILES["llm.py"]}))["llm.py::ask#0"]
        self.assertIn("no caller", site["warning"])

    def test_two_functions_with_the_same_name_are_flagged(self):
        files = dict(self.FILES)
        files["other.py"] = '''
            def ask(x, y):
                return x
        '''
        self.assertIn("several functions", by_key(run(files))["llm.py::ask#0"]["warning"])


class CommandLine(unittest.TestCase):
    def test_json_output_and_text_output_both_work(self):
        root = repo({"a.py": '''
            def f(client, x):
                return client.chat.completions.create(model="m", messages=[{"role": "user", "content": "A long enough prompt written right here: " + x}])
        '''})
        script = os.path.join(HERE, "find_calls.py")
        data = json.loads(subprocess.check_output([sys.executable, script, root, "--json"]))
        self.assertEqual(data["sites"][0]["key"], "a.py::f#0")
        text = subprocess.check_output([sys.executable, script, root]).decode()
        self.assertIn("a.py::f#0", text)



class CoverageHonesty(unittest.TestCase):
    def test_framework_constructs_are_listed_as_not_analysed(self):
        r = run({"agents/a.py": '''
            from fw import create_agent, ChatPromptTemplate
            def build(llm):
                prompt = ChatPromptTemplate.from_messages([("system", "You are a careful assistant."), ("human", "{q}")])
                return create_agent(llm, prompt=prompt)
            async def go(agent, q):
                return await agent.ainvoke({"q": q})
        '''})
        self.assertEqual(r["sites"], [])
        files = {u["file"]: u for u in r["unanalysed_framework_calls"]}
        self.assertIn("agents/a.py", files)
        self.assertIn("create_agent", files["agents/a.py"]["constructs"])
        self.assertIn("ainvoke", files["agents/a.py"]["constructs"])

    def test_text_output_always_states_that_coverage_is_partial(self):
        root = repo({"x.py": "def f():\n    return 1\n"})
        out = subprocess.run([sys.executable, os.path.join(HERE, "find_calls.py"), root], capture_output=True, text=True).stdout
        self.assertIn("NOT complete", out)

    def test_module_level_requests_are_keyed_by_the_assigned_name(self):
        r = run({"m.py": '''
            first = client.chat.create(model="m", messages=[{"role": "user", "content": "Classify the text carefully and briefly please."}])
            second = client.chat.create(model="m", messages=[{"role": "user", "content": "Now summarize the text for me in one line."}])
            client.chat.create(model="m", messages=[{"role": "user", "content": "Unassigned module level request, long enough text."}])
        '''})
        self.assertEqual(sorted(by_key(r)), ["m.py::<module>#0", "m.py::first#0", "m.py::second#0"])

    def test_requests_without_a_text_prompt_are_skipped(self):
        r = run({"v.py": '''
            def say(client, text):
                return client.audio.speech.create(model="tts", voice="v", input=text)
            def embed(client, text):
                return client.embeddings.create(model="e", input=text)
        '''})
        self.assertEqual(r["sites"], [])
        self.assertEqual(len(r["skipped_non_prompt_calls"]), 2)


if __name__ == "__main__":
    unittest.main()
