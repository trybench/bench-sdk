/** Runtime evaluations use server decisions; clients never reconstruct holdouts. */
export type EvaluationDecision = 'validated_improvement' | 'observed_improvement' | 'no_demonstrated_improvement' | 'insufficient_evidence' | 'regression';
export interface EvaluationPolicy {
  objective?: 'quality' | 'cost' | 'latency'; confidence?: number; power?: number;
  minimum_quality_gain?: number; quality_tolerance?: number; minimum_resource_saving?: number;
  max_cost_ratio?: number; max_latency_ratio?: number; repetitions?: number; pilot_groups?: number;
}
export interface EvaluationCase {
  id: string; input: unknown; source_group_id: string;
  role?: 'development' | 'calibration' | 'confirmation'; expectedOutput?: unknown;
  expectedState?: unknown; critical?: boolean; requiredTools?: string[]; forbiddenTools?: string[];
  maxToolCalls?: number; maxModelCalls?: number;
}
export interface EvaluationSpec {
  repository: string; branch: string; source_revision: string; context_revision: string;
  dataset_version: string; scorer_version: string; environment_version: string; pricing_version: string;
  policy?: EvaluationPolicy; cases: EvaluationCase[];
  configurations: { id: string; overrides?: Record<string, unknown> }[];
  runtime: { argv: string[]; template: string; timeout_s: number; max_trial_cost_usd: number };
  budget: { max_cost_usd: number; max_duration_s: number; max_trials: number };
}
export interface EvaluationPlan { id: string; spec_hash: string; plan: { schema_version: 2; status: string; counts: Record<string, unknown>; estimated_pilot_trials: number; confirmation_trial_reserve: number } }
export interface EvaluationResult { schema_version: 2; status: string; phase: string; decision: EvaluationDecision | null; completed_trials: number; comparisons?: Record<string, unknown>[]; metrics?: Record<string, unknown>; reason?: string }
export interface EvaluationRun { id: string; plan_id: string; status: string; result: EvaluationResult }
export class EvaluationClient {
  constructor(private baseUrl: string, private token: string, private systemId: number, private fetcher: typeof fetch = fetch) {
    const url = new URL(baseUrl);
    if (url.protocol !== 'https:' && !(['localhost','127.0.0.1','[::1]'].includes(url.hostname) && url.protocol === 'http:')) throw new Error('Use HTTPS or loopback for Bench credentials');
    if (!Number.isInteger(systemId) || systemId < 1) throw new Error('Invalid system ID');
  }
  private async request<T>(path: string, method = 'GET', body?: unknown, key?: string): Promise<T> {
    const response = await this.fetcher(`${this.baseUrl.replace(/\/$/,'')}/api/ai-systems/${this.systemId}/${path}`, {
      method, redirect: 'error', signal: AbortSignal.timeout(120000),
      headers: { Authorization: `Bearer ${this.token}`, 'Content-Type': 'application/json', ...(key ? {'Idempotency-Key': key} : {}) },
      ...(body === undefined ? {} : {body: JSON.stringify(body)}),
    });
    if (!response.ok) throw new Error(`Bench evaluation request failed (${response.status})`);
    return response.json() as Promise<T>;
  }
  plan(spec: EvaluationSpec) { return this.request<EvaluationPlan>('evaluation-plans','POST',spec); }
  getPlan(id: string) { return this.request<EvaluationPlan>(`evaluation-plans/${encodeURIComponent(id)}`); }
  run(planId: string, idempotencyKey: string) { return this.request<EvaluationRun>('evaluation-runs','POST',{plan_id:planId},idempotencyKey); }
  status(id: string) { return this.request<EvaluationRun>(`evaluation-runs/${encodeURIComponent(id)}`); }
  events(id: string, after = 0) { return this.request<{events: {cursor:number;type:string}[];status:string}>(`evaluation-runs/${encodeURIComponent(id)}/events?after=${after}`); }
  cancel(id: string) { return this.request<EvaluationRun>(`evaluation-runs/${encodeURIComponent(id)}/cancel`,'POST',{}); }
  resume(id: string, idempotencyKey: string) { return this.request<EvaluationRun>(`evaluation-runs/${encodeURIComponent(id)}/resume`,'POST',{},idempotencyKey); }
  reviewCases(id: string) { return this.request<{cases:EvaluationCase[]}>(`evaluation-plans/${encodeURIComponent(id)}/review`); }
  review(id: string, caseId: string, verdict: 'good'|'bad'|'skip', note = '') { return this.request<{saved:boolean}>(`evaluation-plans/${encodeURIComponent(id)}/review`,'POST',{case_id:caseId,verdict,note}); }
}
