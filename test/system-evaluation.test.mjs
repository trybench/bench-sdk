import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Bench } from '../dist/index.js';

test('evaluates the actual application including rendered state, tools and final response', async () => {
  const bench = new Bench({ apiKey: 'bench_sk_test', repository: 'test/refunds', branch: 'dev', sampleRate: 0 });
  let calls = 0;
  const report = await bench.evaluateSystem({
    sourceRevision: 'a'.repeat(40), contextRevision: 'policy-v1',
    cases: [{ id: 'outside-policy', input: { days: 45 }, expectedOutput: { refund: false }, requiredTools: ['load-policy'] }],
    run: async (input) => {
      calls++;
      const policy = await bench.trace({ name: 'load-policy', kind: 'TOOL' }, () => ({ days: 30 }));
      return bench.trace({ name: 'decide', kind: 'LLM', model: 'fixture-model' }, () => ({ refund: input.days <= policy.days }));
    },
  });
  await bench.shutdown();
  assert.equal(calls, 1);
  assert.equal(report.execution_mode, 'application_runtime');
  assert.equal(report.summary.score, 100);
  assert.deepEqual(report.cases[0].spans.map(s => s.kind).sort(), ['AGENT', 'LLM', 'TOOL']);
  assert.equal(report.cases[0].checks.find(c => c.id === 'required-tool:load-policy').passed, true);
  assert.equal(report.evidence_origin, 'sdk_client_reported');
});

test('a harness defect fails even when every individual model output is correct', async () => {
  const bench = new Bench({ apiKey: 'bench_sk_test', repository: 'test/refunds', branch: 'dev' });
  const options = {
    sourceRevision: 'a'.repeat(40), contextRevision: 'policy-v1',
    cases: [{ id: 'rejected-refund', split: 'incident', input: { days: 45 }, expectedOutput: { refund: false }, forbiddenTools: ['issue-refund'] }],
    run: async () => {
      const decision = await bench.trace({ name: 'decide', kind: 'LLM' }, () => ({ refund: false }));
      // This models the real bug: the harness ignores a correct model decision.
      await bench.trace({ name: 'issue-refund', kind: 'TOOL' }, () => 'refunded');
      return decision;
    },
  };
  const bad = await bench.evaluateSystem(options);
  assert.equal(bad.summary.score, 0);
  assert.equal(bad.cases[0].checks.find(c => c.id === 'expected-output').passed, true);
  assert.equal(bad.cases[0].checks.find(c => c.id === 'forbidden-tool:issue-refund').passed, false);
  assert.equal(bad.cases[0].findings[0].category, 'harness');
  const fixed = await bench.evaluateSystem({ ...options, sourceRevision: 'b'.repeat(40), run: () => ({ refund: false }) });
  assert.equal(fixed.summary.score, 100);
  assert.equal(bad.suite_hash, fixed.suite_hash);
  await bench.shutdown();
});

test('missing assertions, missing telemetry and timed out applications never receive a successful score', async () => {
  const bench = new Bench({ apiKey: 'bench_sk_test', repository: 'test/refunds', branch: 'dev' });
  const options = { sourceRevision: 'a'.repeat(40), contextRevision: '1', cases: [{ id: 'x', input: 1 }], run: () => 'ok' };
  assert.equal((await bench.evaluateSystem(options)).summary.score, null);
  const required = await bench.evaluateSystem({ ...options, cases: [{ id: 'x', input: 1, requiredTools: ['unrecorded'] }] });
  assert.equal(required.summary.score, 0);
  assert.match(required.cases[0].findings[0].fix_brief, /Trace the real application/);
  let attempts = 0;
  const timed = await bench.evaluateSystem({ ...options, timeoutMs: 5, cases: [{ id: 'first', input: 1, expectedOutput: 1 }, { id: 'second', input: 2, expectedOutput: 2 }], run: async (_, { signal }) => { attempts++; await new Promise(resolve => signal.addEventListener('abort',resolve,{once:true})); return 1; } });
  assert.equal(attempts, 1);
  assert.equal(timed.summary.score, null);
  assert.equal(timed.summary.status, 'incomplete');
  await bench.shutdown();
});

test('unfinished application tools cannot disappear from a successful result', async () => {
  const bench = new Bench({ apiKey: 'bench_sk_test', repository: 'test/refunds', branch: 'dev' });
  let release;
  let attempts = 0;
  const pending = new Promise(resolve => { release = resolve });
  const report = await bench.evaluateSystem({
    sourceRevision: 'a'.repeat(40), contextRevision: 'v1',
    cases: [{id: 'first', input: 1, expectedOutput: 1, forbiddenTools: ['delete-account']}, {id:'second', input:2, expectedOutput:2}],
    run: () => { attempts++; void bench.trace({name:'delete-account',kind:'TOOL'}, () => pending); return 1; },
  });
  release('done');
  await bench.shutdown();
  assert.equal(report.summary.status, 'incomplete');
  assert.equal(report.summary.score, null);
  assert.equal(attempts, 1);
});

test('publishing runtime evidence is explicit and preserves suite coverage', async () => {
  const sent = [];
  const bench = new Bench({apiKey:'bench_sk_test', repository:'test/refunds',branch:'dev',fetch:async (url, options) => {sent.push({url,body:JSON.parse(options.body)});return new Response(JSON.stringify({evaluation:{id:7,ai_system_id:3}}),{status:201})}});
  const report = await bench.evaluateSystem({sourceRevision:'a'.repeat(40),contextRevision:'v1',cases:[{id:'case',split:'capability',input:1,expectedOutput:'person@example.com'}],run:()=> 'person@example.com'});
  assert.equal(sent.length,0);
  await bench.publishSystemEvaluation(3,report);
  assert.equal(sent.length,1);
  assert.equal(sent[0].body.planned_case_count,1);
  assert.equal(sent[0].body.cases[0].split,'capability');
  assert.equal(sent[0].body.summary.score,100);
  assert.equal(JSON.stringify(sent[0]).includes('person@example.com'),false);
  await bench.shutdown();
});
