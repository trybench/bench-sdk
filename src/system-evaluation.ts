import { createHash } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import type { Span } from './index.js';

export interface SystemCase<Input = unknown> {
  id: string;
  input: Input;
  split?: 'regression' | 'incident' | 'holdout' | 'capability';
  expectedOutput?: unknown;
  expectedState?: unknown;
  businessOutcome?: string;
  requiredTools?: string[];
  forbiddenTools?: string[];
  maxToolCalls?: number;
  maxModelCalls?: number;
}

export interface SystemEvaluationOptions<Input, Output> {
  sourceRevision: string;
  contextRevision: string;
  cases: SystemCase<Input>[];
  /** Invoke the real application entry point. Never a prompt reconstructed by Bench. */
  run: (input: Input, context: { caseId: string; signal: AbortSignal }) => Promise<Output> | Output;
  /** Read test state from your fixture service or test database, independently of the answer. */
  observe?: (context: { caseId: string; signal: AbortSignal }) => Promise<unknown> | unknown;
  /** Cooperative timeout. Use a separate sandbox for an untrusted or non-cooperative application. */
  timeoutMs?: number;
  signal?: AbortSignal;
}

export interface SystemCheck { id: string; passed: boolean; reason: string }
export interface RuntimeFinding {
  category: 'tool' | 'harness' | 'quality';
  title: string;
  evidence_span_ids: string[];
  confidence: 'observed_failure' | 'hypothesis';
  fix_brief: string;
}
export interface SystemCaseResult {
  id: string;
  case_definition?: SystemCase;
  split: string;
  status: 'passed' | 'failed' | 'error' | 'unscored';
  output?: unknown;
  observedState?: unknown;
  spans: Span[];
  checks: SystemCheck[];
  findings: RuntimeFinding[];
  error?: string;
}
export interface SystemEvaluationReport {
  environment?: string;
  schema_version: 1;
  execution_mode: 'application_runtime';
  evidence_origin: 'sdk_client_reported';
  source_revision: string;
  context_revision: string;
  suite_hash: string;
  planned_case_count: number;
  coverage: { instrumentation: 'explicit_spans'; tool_dependencies: 'application_configured'; hosted_validation: false };
  cases: SystemCaseResult[];
  summary: { status: 'completed' | 'incomplete'; score: number | null; passed: number; failed: number; errors: number; unscored: number };
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + canonical((value as Record<string, unknown>)[k])).join(',') + '}';
  return JSON.stringify(value) ?? 'null';
}

export function validateSystemOptions<Input, Output>(options: SystemEvaluationOptions<Input, Output>): void {
  if (!/^[a-f0-9]{40}$/.test(options.sourceRevision) || !options.contextRevision || options.contextRevision.length > 200) throw new Error('Pin a full source revision and a context revision.');
  if (!Array.isArray(options.cases) || options.cases.length < 1 || options.cases.length > 100) throw new Error('Provide between 1 and 100 cases.');
  if (typeof options.run !== 'function') throw new Error('A real application entry point is required.');
  const timeout = options.timeoutMs ?? 30000;
  if (!Number.isInteger(timeout) || timeout < 1 || timeout > 300000) throw new Error('timeoutMs must be between 1 and 300000.');
  const ids = new Set<string>();
  for (const item of options.cases) {
    if (!item.id || item.id.length > 100 || ids.has(item.id)) throw new Error('Case IDs must be unique and nonempty.');
    ids.add(item.id);
    if (item.businessOutcome !== undefined && (typeof item.businessOutcome !== 'string' || !item.businessOutcome || item.businessOutcome.length > 2000)) throw new Error('Provide a bounded business outcome.');
    if (item.split && !['regression', 'incident', 'holdout', 'capability'].includes(item.split)) throw new Error('Invalid case split.');
    for (const limit of [item.maxModelCalls, item.maxToolCalls]) if (limit !== undefined && (!Number.isInteger(limit) || limit < 0 || limit > 10000)) throw new Error('Call limits must be nonnegative integers up to 10000.');
    for (const names of [item.requiredTools, item.forbiddenTools]) if (names && (!Array.isArray(names) || names.length > 100 || names.some(n => typeof n !== 'string' || !n || n.length > 200))) throw new Error('Invalid tool names.');
    if ((item.requiredTools?.length ?? 0) + (item.forbiddenTools?.length ?? 0) > 90) throw new Error('Use at most 90 tool assertions per case.');
  }
  // Only bounded JSON cases travel across execution and judge seams.
  if (Buffer.byteLength(JSON.stringify(options.cases)) > 500000) throw new Error('Case suite exceeds 500 KB.');
}

export function scoreSystemCase(item: SystemCase, output: unknown, spans: Span[], error?: string, observedState?: unknown): SystemCaseResult {
  if (!spans.some(s => !s.parent_span_id && s.kind === 'AGENT' && s.status === 'ok')) error = error ?? 'Application root was not recorded. No complete score.';
  if (Object.hasOwn(item, 'expectedState') && observedState === undefined) error = error ?? 'Application state was not observed. No complete score.';
  const tools = spans.filter(s => s.kind === 'TOOL');
  const models = spans.filter(s => s.kind === 'LLM');
  const checks: SystemCheck[] = [];
  const findings: RuntimeFinding[] = [];
  if (Object.hasOwn(item, 'expectedOutput')) checks.push({ id: 'expected-output', passed: isDeepStrictEqual(output, item.expectedOutput), reason: 'Compare the application final output with the case expected output.' });
  if (Object.hasOwn(item, 'expectedState') && observedState !== undefined) checks.push({ id: 'expected-state', passed: isDeepStrictEqual(observedState, item.expectedState), reason: 'Compare observed tool effects with the required business outcome, independently of the final reply.' });
  for (const name of item.requiredTools ?? []) checks.push({ id: 'required-tool:' + name, passed: tools.some(t => t.name === name && t.status === 'ok'), reason: 'A successful instrumented tool invocation is required.' });
  for (const name of item.forbiddenTools ?? []) checks.push({ id: 'forbidden-tool:' + name, passed: !tools.some(t => t.name === name), reason: 'This instrumented tool must not be invoked.' });
  for (const [id, actual, limit] of [['tool-call-limit', tools.length, item.maxToolCalls], ['model-call-limit', models.length, item.maxModelCalls]] as const) {
    if (limit !== undefined) checks.push({ id, passed: actual <= limit, reason: `${actual} recorded calls; limit ${limit}.` });
  }
  for (const span of tools.filter(s => s.status === 'error')) findings.push({ category: 'tool', title: `${span.name} failed`, evidence_span_ids: [span.span_id], confidence: 'observed_failure', fix_brief: 'Inspect this tool invocation, its inputs and source location. Add a failing tool contract test, fix the implementation or dependency, then rerun the incident and regression suite. A failed call alone does not identify the root cause.' });
  for (const check of checks.filter(c => !c.passed)) findings.push({ category: ['expected-output', 'expected-state'].includes(check.id) ? 'quality' : 'harness', title: check.id === 'expected-output' ? 'Application output differs from the expected outcome' : check.id, evidence_span_ids: spans.map(s => s.span_id), confidence: 'hypothesis', fix_brief: 'Trace the real application path: rendered instructions, state, model response, tool arguments and results, routing, retries and final response. Change one suspected cause at a time. Re-run identical incident, holdout and regression cases. Do not infer a prompt or code defect from the final answer alone.' });
  return { id: item.id, split: item.split ?? 'regression', status: error ? 'error' : checks.length === 0 ? 'unscored' : checks.every(c => c.passed) ? 'passed' : 'failed', output, ...(observedState !== undefined ? { observedState } : {}), spans, checks, findings, ...(error ? { error } : {}) };
}

export function systemReport<Input, Output>(options: SystemEvaluationOptions<Input, Output>, cases: SystemCaseResult[]): SystemEvaluationReport {
  const counts = { passed: 0, failed: 0, errors: 0, unscored: 0 };
  for (const item of cases) item.status === 'error' ? counts.errors++ : counts[item.status]++;
  const complete = cases.length === options.cases.length && !counts.errors && !counts.unscored;
  return { schema_version: 1, execution_mode: 'application_runtime', evidence_origin: 'sdk_client_reported', source_revision: options.sourceRevision, context_revision: options.contextRevision, suite_hash: createHash('sha256').update(canonical({ context: options.contextRevision, cases: options.cases })).digest('hex'), planned_case_count: options.cases.length, coverage: { instrumentation: 'explicit_spans', tool_dependencies: 'application_configured', hosted_validation: false }, cases, summary: { status: complete ? 'completed' : 'incomplete', score: complete ? 100 * counts.passed / cases.length : null, ...counts } };
}
