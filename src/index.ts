import { simulateSystem, type SimulationOptions } from './simulation.js';
export type { SimulationOptions, SimulationSession, SimulationInput } from './simulation.js';
import { AsyncLocalStorage } from "node:async_hooks";
import { randomBytes } from "node:crypto";
import { isIP } from "node:net";
import { scoreSystemCase, systemReport, validateSystemOptions, type SystemEvaluationOptions, type SystemEvaluationReport } from './system-evaluation.js';
export * from './system-evaluation.js';

export type SpanKind = "LLM" | "TOOL" | "CHAIN" | "RETRIEVER" | "AGENT" | "EMBEDDING";
export interface SpanInput {
 name: string;
 kind?: SpanKind;
 componentId?: number;
 model?: string;
 input?: unknown;
 output?: unknown;
 attributes?: Record<string, unknown>;
}
export interface BenchOptions {
 apiKey: string;
 repository: string;
 branch: string;
 systemName?: string;
 environment?: string;
 endpoint?: string;
 captureContent?: boolean;
 sampleRate?: number;
 maxQueueSize?: number;
 flushIntervalMs?: number;
 timeoutMs?: number;
 redact?: (value: unknown) => unknown;
 onError?: (error: Error) => void;
 fetch?: typeof globalThis.fetch;
}
export interface Span {
 span_id: string; parent_span_id?: string; name: string; kind: SpanKind;
 started_at: string; ended_at: string; status: string;
 attributes: Record<string, unknown>; input_value?: string; output_value?: string; model_name?: string;
}
interface Trace { trace_id: string; source: "bench_sdk"; spans: Span[] }
const sensitiveKey = /authorization|cookie|password|secret|token|api.?key|email|phone|address|user.?id|(?:first|last|full).?name|card.?number/i;
const allowedMetadata = /^(code\.(filepath|lineno)|gen_ai\.(system|operation\.name|usage\.(input_tokens|output_tokens))|bench\.(component_id|environment|prompt_version))$/;
function cardChecksum(digits: string): boolean {
 const sum = [...digits].reverse().map(Number).reduce((sum,n,i) => sum + (i % 2 ? n * 2 - (n > 4 ? 9 : 0) : n), 0);
 return sum > 0 && sum % 10 === 0;
}
function redact(value: unknown, depth = 0, maxItems = 100): unknown {
 if (depth > 12) return "[DEPTH_LIMIT]";
 if (typeof value === "string") {
  if (/^[\s]*[\[{]/.test(value)) {
   try { return JSON.stringify(redact(JSON.parse(value), depth + 1, maxItems)).slice(0,16000) } catch { /* Plain text still receives pattern filtering. */ }
  }
  return value
  .replace(/(?:bench_sk_|apikey_|e2b_|sk-)[a-zA-Z0-9_-]{8,}|Bearer\s+[a-zA-Z0-9._~+\/-]+/gi,"[REDACTED_SECRET]")
  .replace(/[a-z0-9.!#$%&'*+\/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+/gi,"[REDACTED_EMAIL]")
  .replace(/\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b/g, value => isIP(value) === 4 ? '[REDACTED_IP]' : value)
  .replace(/\b(?:[0-9]{4}(?:[ -][0-9]{4}){3}[ -][0-9]{3}|[0-9]{4}(?:[ -][0-9]{4}){3}|[0-9]{4}[ -][0-9]{6}[ -][0-9]{5}|[0-9]{13,19})\b/g, value => {
   const digits = value.replace(/[ -]/g, '');
   return cardChecksum(digits) || (digits.length === 19 && value.length > 19 && cardChecksum(digits.slice(0,16))) ? '[REDACTED_PAYMENT_NUMBER]' : value;
  }).replace(/\+\d[\d ()-]{8,}\d/g,"[REDACTED_PHONE]").slice(0,16000);
 }
 if (Array.isArray(value)) return value.slice(0,maxItems).map(v=>redact(v,depth+1,maxItems));
 if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).slice(0,maxItems).map(([k,v])=>[redact(k,depth+1,maxItems),sensitiveKey.test(k)&&!(/^gen_ai\.usage\.(input_tokens|output_tokens)$/.test(k)&&typeof v==="number")?"[REDACTED]":redact(v,depth+1,maxItems)]));
 if (typeof value === "number" && Number.isInteger(value) && /^[0-9]{13,19}$/.test(String(value)) && (!Number.isSafeInteger(value) || cardChecksum(String(value)))) return "[REDACTED_PAYMENT_NUMBER]";
 if (value === null || ["number","boolean"].includes(typeof value)) return value;
 return undefined;
}

/** Node/server only. No inference, model interception, or paid evaluation on capture. */
export class Bench {
 private readonly options: BenchOptions;
 private readonly endpoint: string;
 private readonly storage = new AsyncLocalStorage<{traceId:string;spanId:string;sampled:boolean; evaluation?: { spans: Span[]; active: boolean; limited: boolean; pending: number }}>();
 private queue: Trace[] = [];
 private timer: ReturnType<typeof setInterval>;
 private flushing?: Promise<void>;
 private closed = false;
 private dropped = 0;
 constructor(options: BenchOptions) {
  if (typeof window !== "undefined") throw new Error("Bench API keys must stay on the server.");
  if (!options.apiKey.startsWith("bench_sk_")) throw new Error("A Bench API key is required.");
  if (!options.repository || !options.branch) throw new Error("repository and branch are required.");
  if (options.environment !== undefined && !/^[a-zA-Z0-9_.-]{1,64}$/.test(options.environment)) throw new Error("environment must be a short deployment name.");
  const endpoint = new URL(options.endpoint ?? "https://api.trybench.ai");
  if (endpoint.username || endpoint.password || endpoint.search || endpoint.hash || (endpoint.protocol !== "https:" && !(endpoint.protocol === "http:" && ["localhost","127.0.0.1","[::1]"].includes(endpoint.hostname)))) throw new Error("Use HTTPS or a loopback HTTP endpoint.");
  for (const [name,value,min,max] of [["sampleRate",options.sampleRate??1,0,1],["maxQueueSize",options.maxQueueSize??200,1,2000],["flushIntervalMs",options.flushIntervalMs??2000,100,60000],["timeoutMs",options.timeoutMs??5000,100,30000]] as const) {
   if (!Number.isFinite(value)||value<min||value>max) throw new Error(name+" is out of range.");
  }
  this.endpoint=endpoint.toString().replace(/\/$/,"")+"/api/traces";
  this.options=options;
  this.timer=setInterval(()=>{void this.flush()},options.flushIntervalMs??2000);
  this.timer.unref();
 }
 private safe(value: unknown): unknown { return redact(this.options.redact ? this.options.redact(value) : value) }
 private report(message:string){try{this.options.onError?.(new Error(message))}catch{/* Telemetry callbacks cannot break the application. */}}
 private capture(input:SpanInput, traceId:string,spanId:string,parent:string|undefined,start:number,status:string){
  if(this.closed)return;
  try {
   const attrs=Object.fromEntries(Object.entries(input.attributes??{}).filter(([key])=>this.options.captureContent||allowedMetadata.test(key)));
   if(input.componentId)attrs["bench.component_id"]=input.componentId;
   if(this.options.environment)attrs["bench.environment"]=this.options.environment;
   const content=(value:unknown)=>value===undefined?undefined:JSON.stringify(this.safe(value));
   const span:Span={span_id:spanId,parent_span_id:parent,name:String(this.safe(input.name)).slice(0,200),kind:input.kind??"LLM",started_at:new Date(start).toISOString(),ended_at:new Date().toISOString(),status,attributes:this.safe(attrs) as Record<string,unknown>,model_name:input.model?String(this.safe(input.model)).slice(0,200):undefined};
   const evaluation = this.storage.getStore()?.evaluation;
   if(this.options.captureContent || evaluation){span.input_value=content(input.input);span.output_value=content(input.output)}
   if(evaluation){
    if(!evaluation.active)return;
    if(evaluation.spans.length >= 100 || Buffer.byteLength(JSON.stringify([...evaluation.spans, span])) > 500000){evaluation.limited=true;return}
    evaluation.spans.push(span);
    return;
   }
   const trace:Trace={trace_id:traceId,source:"bench_sdk",spans:[span]};
   if(Buffer.byteLength(JSON.stringify(trace))>200000){this.dropped++;this.report("Trace too large; dropped.");return}
   if(this.queue.length >= (this.options.maxQueueSize??200)){this.dropped++;return}
   this.queue.push(trace);
  }catch{const evaluation=this.storage.getStore()?.evaluation;if(evaluation)evaluation.limited=true;this.dropped++;this.report("Trace could not be serialized; dropped.")}
 }
 async trace<T>(input:SpanInput,fn:()=>T|Promise<T>):Promise<T>{
  const parent=this.storage.getStore();
  const traceId=parent?.traceId??randomBytes(16).toString("hex"),spanId=randomBytes(8).toString("hex");
  const sampled=parent?.sampled??Math.random()<(this.options.sampleRate??1),start=Date.now();
  return this.storage.run({traceId,spanId,sampled,evaluation:parent?.evaluation},async()=>{
   if(parent?.evaluation)parent.evaluation.pending++;
   try{const output=await fn();if(sampled)this.capture({...input,output:input.output === undefined ? output : input.output},traceId,spanId,parent?.spanId,start,"ok");return output}
   catch(error){if(sampled)this.capture(input,traceId,spanId,parent?.spanId,start,"error");throw error}
   finally{if(parent?.evaluation)parent.evaluation.pending--}
  });
 }
 record(input:SpanInput):void{
  const parent=this.storage.getStore();
  if(!(parent?.sampled??Math.random()<(this.options.sampleRate??1)))return;
  this.capture(input,parent?.traceId??randomBytes(16).toString("hex"),randomBytes(8).toString("hex"),parent?.spanId,Date.now(),"ok");
 }
 /** Execute the real application. Local reports are evidence, not server-verified results.
  * Captures redacted content for these test calls only. Does not upload or spend Bench credits.
  * Use a test tenant and sandboxed/mocked external side effects in your application adapter.
  */
 async evaluateSystem<Input, Output>(options: SystemEvaluationOptions<Input, Output>): Promise<SystemEvaluationReport> {
  if(this.closed)throw new Error('Bench is shut down.');
  validateSystemOptions(options);
  // Snapshot cases before application execution so runtime code cannot mutate the oracle.
  const pinned = {...options, cases: structuredClone(options.cases)};
  const results = [];
  for (const item of pinned.cases) {
   if(options.signal?.aborted)break;
   const evaluation = {spans: [] as Span[], active: true, limited: false, pending: 0};
   const controller = new AbortController();
   let timer: ReturnType<typeof setTimeout> | undefined;
   let output: unknown;
   let observedState: unknown;
   let failure: string | undefined;
   const aborted = () => controller.abort();
   options.signal?.addEventListener('abort', aborted, {once:true});
   try {
    const execution = await Promise.race([
     (async () => {
      const value = await this.storage.run({traceId:randomBytes(16).toString('hex'),spanId:'',sampled:true,evaluation}, () => this.trace({name:'system-entrypoint',kind:'AGENT',input:structuredClone(item.input)}, () => options.run(structuredClone(item.input),{caseId:item.id,signal:controller.signal})));
      if(controller.signal.aborted)throw new Error('Stopped');
      const state = options.observe ? await options.observe({caseId:item.id,signal:controller.signal}) : undefined;
      return {value,state};
     })(),
     new Promise<never>((_,reject) => {
      controller.signal.addEventListener('abort',()=>reject(new Error('Application evaluation stopped.')), {once:true});
      timer = setTimeout(()=>controller.abort(), options.timeoutMs ?? 30000);
     }),
    ]);
    output = execution.value;
    observedState = execution.state;
   } catch { failure = controller.signal.aborted ? 'Application timed out or was stopped. No complete score.' : 'Application execution failed. Inspect recorded spans.'; }
   finally { evaluation.active=false;clearTimeout(timer);options.signal?.removeEventListener('abort',aborted); }
   if(evaluation.limited)failure='Runtime evidence exceeded its limit or could not be captured. No complete score.';
   if(evaluation.pending){failure='Application returned with unfinished work. Await all tools and model calls before returning.';controller.abort()}
   const result = scoreSystemCase(item, output, evaluation.spans, failure, observedState);
   result.case_definition = this.safe(item) as typeof item;
   result.output = this.safe(result.output);
   if (result.observedState !== undefined) result.observedState = this.safe(result.observedState);
   results.push(result);
   // A callback cannot be forcibly killed inside Node. Never start more cases after timeout.
   if(controller.signal.aborted)break;
  }
  return {...systemReport(pinned,results), environment: this.options.environment ?? 'unspecified'};
 }
 /** Replay bounded customer turns against a fresh application session and fixture state. */
 async simulateSystem<State, Turn, Reply>(options: SimulationOptions<State, Turn, Reply>): Promise<SystemEvaluationReport> {
  return simulateSystem(options, (evaluation) => this.evaluateSystem(evaluation));
 }
 /** Explicit upload of redacted runtime evidence. Does not run a judge or spend Bench credits. */
 async publishSystemEvaluation(systemId: number, report: SystemEvaluationReport): Promise<{ evaluation: { id: number; ai_system_id: number } }> {
  if(!Number.isSafeInteger(systemId) || systemId<1)throw new Error('Select a system.');
  // Case content was redacted at capture. Preserve bounded structural arrays,
  // including failure checks; truncating them could change the stored verdict.
  const body=JSON.stringify(redact(this.options.redact ? this.options.redact(report) : report,0,300));
  if(Buffer.byteLength(body)>500000)throw new Error('Runtime report exceeds 500 KB. Retain it locally or split the suite.');
  const response=await(this.options.fetch??globalThis.fetch)(this.endpoint.replace(/\/api\/traces$/,`/api/ai-systems/${systemId}/runtime-evaluations`),{method:'POST',redirect:'error',headers:{'Content-Type':'application/json',Authorization:'Bearer '+this.options.apiKey},body,signal:AbortSignal.timeout(this.options.timeoutMs??5000)});
  if(!response.ok)throw new Error(`Could not save runtime results (HTTP ${response.status}). Local results are still available.`);
  return response.json() as Promise<{evaluation:{id:number;ai_system_id:number}}>;
 }
 get stats(){return {queued:this.queue.length,dropped:this.dropped}}
 /** Flush on serverless shutdown. Network failures are reported, never thrown into app requests. */
 flush():Promise<void>{
  if(this.flushing)return this.flushing;
  this.flushing=this.send().finally(()=>{this.flushing=undefined});
  return this.flushing;
 }
 private async send(){
  while(this.queue.length){
   const batch:Trace[]=[];let bytes=0;
   while(this.queue.length&&batch.length<20){const size=Buffer.byteLength(JSON.stringify(this.queue[0]));if(bytes+size>800000)break;bytes+=size;batch.push(this.queue.shift()!)}
   const body=JSON.stringify({repo_full_name:this.options.repository,branch:this.options.branch,system_name:this.options.systemName??this.options.repository.split('/').pop(),capture_content:this.options.captureContent??false,traces:batch});
   let delivered=false;
   for(let attempt=0;attempt<2;attempt++){
    try{
     const result=await(this.options.fetch??globalThis.fetch)(this.endpoint,{method:"POST",redirect:"error",headers:{"Content-Type":"application/json",Authorization:"Bearer "+this.options.apiKey},body,signal:AbortSignal.timeout(this.options.timeoutMs??5000)});
     if(result.ok){delivered=true;break}
     if(result.status!==429&&result.status<500)break;
    }catch{/* Retry once, with the same trace/span IDs. No payloads or credentials are logged. */}
    if(attempt===0)await new Promise(resolve=>setTimeout(resolve,250));
   }
   if(!delivered){this.dropped+=batch.length;this.report("Bench trace delivery failed; batch dropped.")}
  }
 }
 async shutdown(){this.closed=true;clearInterval(this.timer);await this.flush()}
}
