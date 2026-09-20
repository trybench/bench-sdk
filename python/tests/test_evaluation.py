import asyncio
import json
import unittest

from bench_sdk import Bench


class EvaluationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sent = []
        self.bench = Bench(api_key="bench_sk_fixture", repository="test/refunds", branch="dev",
                           sample_rate=0, environment="ci",
                           transport=lambda *args: self.sent.append(args) or 201)

    async def asyncTearDown(self):
        await self.bench.aflush()

    async def test_real_harness_failure_is_detected_despite_correct_answer(self):
        cases = [{"id": "late-refund", "input": {"days": 45}, "split": "incident",
                  "expected_output": {"refunded": False}, "forbidden_tools": ["issue-refund"]}]

        async def broken(request, context):
            with self.bench.trace("decide", kind="LLM") as span:
                span.set_output({"refunded": False})
            with self.bench.trace("issue-refund", kind="TOOL") as span:
                span.set_output({"refunded": True})
            return {"refunded": False}

        options = dict(source_revision="a" * 40, context_revision="policy-v1", cases=cases)
        bad = await self.bench.evaluate_system(**options, run=broken)
        good = await self.bench.evaluate_system(**options, run=lambda request, context: {"refunded": False})
        self.assertEqual(bad["summary"]["score"], 0)
        self.assertEqual(good["summary"]["score"], 100)
        self.assertEqual(bad["suite_hash"], good["suite_hash"])
        self.assertEqual(len(bad["cases"][0]["spans"]), 3)
        self.assertEqual(bad["cases"][0]["findings"][0]["category"], "harness")
        self.assertEqual(self.sent, [])
        self.assertEqual(self.bench.stats["queued"], 0)

    async def test_simulated_customer_retries_check_real_state_and_reset_sessions(self):
        closed = []
        class Session:
            def __init__(self, state, fixed, bench):
                self.state, self.fixed, self.bench = state, fixed, bench
            async def turn(self, message, context):
                with self.bench.trace("refund", kind="TOOL") as span:
                    if not self.fixed or self.state["cents"] == 0:
                        self.state["cents"] += 12000
                    span.set_output("Refunded")
                return "Refunded"
            def observe(self):
                return self.state
            def close(self):
                closed.append(True)
                self.state["cents"] = 0
        cases = [{"id": "retry", "input": {"initial_state": {"cents": 0}, "turns": ["Refund", "Try again"]},
                  "expected_output": "Refunded", "expected_state": {"cents": 12000}}]
        options = dict(source_revision="a" * 40, context_revision="v1", cases=cases)
        bad = await self.bench.simulate_system(**options, create_session=lambda state, ctx: Session(state, False, self.bench))
        fixed = await self.bench.simulate_system(**options, create_session=lambda state, ctx: Session(state, True, self.bench))
        self.assertEqual(bad["cases"][0]["observedState"]["cents"], 24000)
        self.assertEqual(bad["summary"]["score"], 0)
        self.assertEqual(fixed["summary"]["score"], 100)
        self.assertEqual(closed, [True, True])
        self.assertEqual(cases[0]["input"]["initial_state"]["cents"], 0)

    async def test_publishing_is_explicit_and_filters_report_content(self):
        report = await self.bench.evaluate_system(source_revision="a" * 40, context_revision="v1",
            cases=[{"id": "privacy", "input": 1, "expected_output": "person@example.test"}],
            run=lambda value, ctx: "person@example.test")
        self.assertEqual(report["summary"]["score"], 100)
        self.assertNotIn("person@example.test", json.dumps(report))
        self.assertEqual(self.sent, [])
        await self.bench.publish_system_evaluation(3, report)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0][0].endswith("/api/ai-systems/3/runtime-evaluations"))
        self.assertEqual(json.loads(self.sent[0][2])["planned_case_count"], 1)

    async def test_observer_operations_belong_to_the_same_application_tree(self):
        def observe(context):
            with self.bench.trace("read-ledger", kind="TOOL") as span:
                span.set_output({"count": 1})
            return {"count": 1}
        report = await self.bench.evaluate_system(source_revision="a" * 40, context_revision="v1",
            cases=[{"id":"state", "input":1, "expected_state":{"count":1}}],
            run=lambda value, ctx: "done", observe=observe)
        roots = [s for s in report["cases"][0]["spans"] if not s.get("parent_span_id")]
        self.assertEqual(len(roots), 1)
        self.assertEqual(report["summary"]["score"], 100)

    async def test_missing_assertions_observation_and_failed_execution_stay_incomplete(self):
        async def failure(value, context):
            raise RuntimeError('do not retain person@example.test')
        for case, run in [
            ({"id":"none", "input":1}, lambda value, ctx: 1),
            ({"id":"state", "input":1, "expected_state":None}, lambda value, ctx: 1),
            ({"id":"failed", "input":1, "expected_output":1}, failure),
        ]:
            report = await self.bench.evaluate_system(source_revision="a"*40, context_revision="v1", cases=[case], run=run)
            self.assertIsNone(report["summary"]["score"])
            self.assertEqual(report["summary"]["status"], "incomplete")
            self.assertNotIn('person@example.test', json.dumps(report))
        explicit_null = await self.bench.evaluate_system(source_revision="a"*40, context_revision="v1",
            cases=[{"id":"null", "input":None, "expected_output":None}], run=lambda value, ctx: None)
        self.assertEqual(explicit_null["summary"]["score"],100)

    async def test_timeout_stops_the_suite_and_closes_the_session(self):
        closed = asyncio.Event()
        started = []
        class Session:
            async def turn(self, message, context):
                started.append(message)
                await asyncio.Event().wait()
            def observe(self):
                return 1
            async def close(self):
                closed.set()
        report = await self.bench.simulate_system(source_revision="a"*40, context_revision="v1", timeout=0.02,
            cases=[{"id":name,"input":{"initial_state":{},"turns":[name]},"expected_state":1} for name in ['one','two']],
            create_session=lambda state, ctx: Session())
        await asyncio.wait_for(closed.wait(),1)
        self.assertIsNone(report["summary"]["score"])
        self.assertEqual(report["planned_case_count"],2)
        self.assertEqual(len(report["cases"]),1)
        self.assertEqual(started,['one'])
        self.assertEqual(self.bench.stats['queued'],0)

    async def test_explicit_cancellation_never_starts_next_case(self):
        import threading
        cancelled = threading.Event()
        calls = []
        async def run(value, ctx):
            calls.append(value)
            cancelled.set()
            await asyncio.Event().wait()
        report = await self.bench.evaluate_system(source_revision="a"*40, context_revision="v1", cancel_event=cancelled,
            cases=[{"id":str(i),"input":i,"expected_output":i} for i in range(2)],run=run)
        self.assertEqual(calls,[0])
        self.assertIsNone(report['summary']['score'])

    async def test_unfinished_tools_and_capture_overflow_do_not_pass(self):
        release = asyncio.Event()
        started = asyncio.Event()
        child = None
        async def tool():
            with self.bench.trace('unfinished',kind='TOOL'):
                started.set()
                await release.wait()
        async def run(value,ctx):
            nonlocal child
            child = asyncio.create_task(tool())
            await started.wait()
            return 1
        options = dict(source_revision="a"*40,context_revision="v1",cases=[{"id":"one","input":1,"expected_output":1}])
        report = await self.bench.evaluate_system(**options,run=run)
        release.set()
        await child
        self.assertIsNone(report['summary']['score'])
        async def overflowing(value,ctx):
            for i in range(100):
                with self.bench.trace('tool',kind='TOOL') as span:
                    span.set_output(i)
            return 1
        self.assertIsNone((await self.bench.evaluate_system(**options,run=overflowing))['summary']['score'])
        self.assertEqual(self.bench.stats['queued'],0)

    async def test_parallel_suites_are_isolated_and_cases_are_snapshotted(self):
        async def evaluate(value):
            case = {"id":"same-id","input":{"value":value},"expected_output":{"value":value}}
            async def run(data,ctx):
                expected = dict(data)
                data['value'] = 999
                await asyncio.sleep(0)
                with self.bench.trace('tool',kind='TOOL') as span:
                    span.set_output(expected)
                return expected
            result = await self.bench.evaluate_system(source_revision="a"*40,context_revision="v1",cases=[case],run=run)
            self.assertEqual(case['input']['value'],value)
            self.assertEqual(len(result['cases'][0]['spans']),2)
            return result
        results = await asyncio.gather(evaluate(1),evaluate(2))
        self.assertTrue(all(r['summary']['score']==100 for r in results))
        self.assertNotEqual(results[0]['suite_hash'],results[1]['suite_hash'])

    async def test_failed_publication_is_visible_and_structural_checks_are_preserved(self):
        report = await self.bench.evaluate_system(source_revision="a"*40,context_revision="v1",
            cases=[{"id":"one","input":1,"expected_output":1}],run=lambda value,ctx:1)
        report['cases'][0]['checks'] = [{"id":str(i),"passed":i != 109,"reason":"test"} for i in range(110)]
        await self.bench.publish_system_evaluation(3,report)
        self.assertEqual(len(json.loads(self.sent[0][2])['cases'][0]['checks']),110)
        self.bench._transport = lambda *args: 403
        with self.assertRaisesRegex(RuntimeError,'HTTP 403'):
            await self.bench.publish_system_evaluation(3,report)

    async def test_tool_measurements_survive_metadata_only_capture(self):
        # Production tracing is independently sampled; force one synthetic event.
        self.bench._sample = 1
        with self.bench.trace('search',kind='TOOL',input='private query',attributes={
            'gen_ai.operation.name':'execute_tool','gen_ai.tool.name':'search','gen_ai.tool.call.id':'call-1',
            'bench.cost.usd':0.002,'bench.cost.source':'reported','bench.duration_ms':-1,'private':'customer input'}) as span:
            await asyncio.sleep(0.005)
            span.set_output('private response')
        await self.bench.aflush()
        span = json.loads(self.sent[0][2])['traces'][0]['spans'][0]
        self.assertEqual(span['attributes']['bench.cost.usd'],0.002)
        self.assertEqual(span['attributes']['gen_ai.tool.call.id'],'call-1')
        self.assertGreaterEqual(span['attributes']['bench.duration_ms'],1)
        self.assertNotIn('input_value',span)
        self.assertNotIn('output_value',span)
        self.assertNotIn('private',span['attributes'])
