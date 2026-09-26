import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Bench, BenchSpanExporter, inferSpanKind, spanToBench } from '../dist/index.js';

const otelSpan = (traceId, spanId, name, attributes, parentSpanId, error = false) => ({
 name, attributes, parentSpanContext: parentSpanId ? { spanId: parentSpanId } : undefined,
 spanContext: () => ({ traceId, spanId }), startTime: [1758000000, 0], endTime: [1758000000, 500000000], status: { code: error ? 2 : 1 },
});
const client = bodies => new Bench({ apiKey: 'bench_sk_test', repository: 'acme/support', branch: 'main', systemName: 'Customer support', endpoint: 'http://127.0.0.1:55021', fetch: async (url, init) => { bodies.push([url, JSON.parse(init.body)]); return new Response('{}', { status: 201 }) } });

test('kind inference follows the GenAI semantic conventions', () => {
 assert.equal(inferSpanKind({ 'gen_ai.operation.name': 'invoke_agent' }), 'AGENT');
 assert.equal(inferSpanKind({ 'gen_ai.agent.name': 'Triage' }), 'AGENT');
 assert.equal(inferSpanKind({ 'gen_ai.operation.name': 'execute_tool', 'gen_ai.tool.name': 'lookup' }), 'TOOL');
 assert.equal(inferSpanKind({ 'gen_ai.request.model': 'gpt-4.1' }), 'LLM');
 assert.equal(inferSpanKind({ 'gen_ai.operation.name': 'embeddings' }), 'EMBEDDING');
 assert.equal(inferSpanKind({ 'http.method': 'GET' }), 'UNKNOWN');
 // Pydantic AI stamps the agent name on model and tool spans too.
 assert.equal(inferSpanKind({ 'gen_ai.operation.name': 'chat', 'gen_ai.agent.name': 'support_agent', 'gen_ai.request.model': 'claude' }), 'LLM');
 assert.equal(inferSpanKind({ 'gen_ai.operation.name': 'execute_tool', 'gen_ai.agent.name': 'support_agent', 'gen_ai.tool.name': 'balance' }), 'TOOL');
});

test('framework spans arrive as one metadata-only tree', async () => {
 const bodies = [];
 const bench = client(bodies);
 const trace = '0000000000000000000000000000ab01';
 const exporter = new BenchSpanExporter(bench);
 await new Promise(resolve => exporter.export([
  otelSpan(trace, '0000000000000001', 'invoke_agent Triage', { 'gen_ai.operation.name': 'invoke_agent', 'gen_ai.agent.name': 'Triage agent', 'customer.email': 'x@example.com' }),
  otelSpan(trace, '0000000000000002', 'chat gpt-4.1-mini', { 'gen_ai.operation.name': 'chat', 'gen_ai.request.model': 'gpt-4.1-mini', 'gen_ai.provider.name': 'openai', 'gen_ai.usage.input_tokens': 12, prompt: 'secret' }, '0000000000000001'),
  otelSpan(trace, '0000000000000003', 'execute_tool lookup_order', { 'gen_ai.operation.name': 'execute_tool', 'gen_ai.tool.name': 'lookup_order' }, '0000000000000001', true),
 ], result => { assert.equal(result.code, 0); resolve() }));
 await exporter.shutdown();
 assert.equal(bodies.length, 1);
 const [url, body] = bodies[0];
 assert.equal(url, 'http://127.0.0.1:55021/api/traces');
 assert.equal(body.system_name, 'Customer support');
 assert.equal(body.capture_content, false);
 assert.deepEqual([...new Set(body.traces.map(t => t.trace_id))], [trace]);
 const spans = Object.fromEntries(body.traces.flatMap(t => t.spans).map(s => [s.span_id, s]));
 assert.equal(spans['0000000000000001'].kind, 'AGENT');
 assert.equal(spans['0000000000000001'].parent_span_id, undefined);
 assert.equal(spans['0000000000000001'].attributes['gen_ai.agent.name'], 'Triage agent');
 assert.equal('customer.email' in spans['0000000000000001'].attributes, false);
 assert.equal(spans['0000000000000002'].kind, 'LLM');
 assert.equal(spans['0000000000000002'].parent_span_id, '0000000000000001');
 assert.equal(spans['0000000000000002'].model_name, 'gpt-4.1-mini');
 assert.equal(spans['0000000000000002'].attributes['gen_ai.usage.input_tokens'], 12);
 assert.equal('prompt' in spans['0000000000000002'].attributes, false);
 assert.equal(spans['0000000000000002'].input_value, undefined);
 assert.equal(spans['0000000000000003'].kind, 'TOOL');
 assert.equal(spans['0000000000000003'].status, 'error');
 assert.equal(spans['0000000000000003'].started_at, '2025-09-16T05:20:00.000Z');
});

test('invalid identifiers are dropped, explicit bench.kind wins, old parentSpanId is read', async () => {
 const bench = client([]);
 bench.recordExternalSpan({ traceId: 'not-hex', spanId: '0000000000000001', name: 'x', startedAt: Date.now(), endedAt: Date.now() });
 assert.deepEqual(bench.stats, { queued: 0, dropped: 1 });
 const converted = spanToBench({ ...otelSpan('ab', '09', 'step', { 'bench.kind': 'CHAIN', 'gen_ai.request.model': 'gpt-4.1' }), parentSpanContext: undefined, parentSpanId: '0000000000000007' });
 assert.equal(converted.kind, 'CHAIN');
 assert.equal(converted.parentSpanId, '0000000000000007');
 assert.equal('bench.kind' in converted.attributes, false);
 await bench.shutdown();
});
