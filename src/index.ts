import { AsyncLocalStorage } from "node:async_hooks";
import { randomBytes } from "node:crypto";

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
interface Span {
 span_id: string; parent_span_id?: string; name: string; kind: SpanKind;
 started_at: string; ended_at: string; status: string;
 attributes: Record<string, unknown>; input_value?: string; output_value?: string; model_name?: string;
}
interface Trace { trace_id: string; source: "bench_sdk"; spans: Span[] }
const sensitiveKey = /authorization|cookie|password|secret|token|api.?key|email|phone|address|user.?id/i;
const allowedMetadata = /^(code\.(filepath|lineno)|gen_ai\.(system|operation\.name|usage\.(input_tokens|output_tokens))|bench\.(component_id|environment|prompt_version))$/;
function redact(value: unknown, depth = 0): unknown {
 if (depth > 12) return "[DEPTH_LIMIT]";
 if (typeof value === "string") return value
  .replace(/(?:bench_sk_|apikey_|e2b_|sk-)[a-zA-Z0-9_-]{8,}|Bearer\s+[a-zA-Z0-9._~+\/-]+/gi,"[REDACTED_SECRET]")
  .replace(/[a-z0-9.!#$%&'*+\/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+/gi,"[REDACTED_EMAIL]")
  .replace(/\+\d[\d ()-]{8,}\d/g,"[REDACTED_PHONE]").slice(0,16000);
 if (Array.isArray(value)) return value.slice(0,100).map(v=>redact(v,depth+1));
 if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).slice(0,100).map(([k,v])=>[k,sensitiveKey.test(k)&&!(/^gen_ai\.usage\.(input_tokens|output_tokens)$/.test(k)&&typeof v==="number")?"[REDACTED]":redact(v,depth+1)]));
 if (value === null || ["number","boolean"].includes(typeof value)) return value;
 return undefined;
}

/** Node/server only. No inference, model interception, or paid evaluation on capture. */
export class Bench {
 private readonly options: BenchOptions;
 private readonly endpoint: string;
 private readonly storage = new AsyncLocalStorage<{traceId:string;spanId:string;sampled:boolean}>();
 private queue: Trace[] = [];
 private timer: ReturnType<typeof setInterval>;
 private flushing?: Promise<void>;
 private closed = false;
 private dropped = 0;
 constructor(options: BenchOptions) {
  if (typeof window !== "undefined") throw new Error("Bench API keys must stay on the server.");
  if (!options.apiKey.startsWith("bench_sk_")) throw new Error("A Bench API key is required.");
  if (!options.repository || !options.branch) throw new Error("repository and branch are required.");
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
   const content=(value:unknown)=>value===undefined?undefined:JSON.stringify(this.safe(value));
   const span:Span={span_id:spanId,parent_span_id:parent,name:String(this.safe(input.name)).slice(0,200),kind:input.kind??"LLM",started_at:new Date(start).toISOString(),ended_at:new Date().toISOString(),status,attributes:this.safe(attrs) as Record<string,unknown>,model_name:input.model?String(this.safe(input.model)).slice(0,200):undefined};
   if(this.options.captureContent){span.input_value=content(input.input);span.output_value=content(input.output)}
   const trace:Trace={trace_id:traceId,source:"bench_sdk",spans:[span]};
   if(Buffer.byteLength(JSON.stringify(trace))>200000){this.dropped++;this.report("Trace too large; dropped.");return}
   if(this.queue.length >= (this.options.maxQueueSize??200)){this.dropped++;return}
   this.queue.push(trace);
  }catch{this.dropped++;this.report("Trace could not be serialized; dropped.")}
 }
 async trace<T>(input:SpanInput,fn:()=>T|Promise<T>):Promise<T>{
  const parent=this.storage.getStore();
  const traceId=parent?.traceId??randomBytes(16).toString("hex"),spanId=randomBytes(8).toString("hex");
  const sampled=parent?.sampled??Math.random()<(this.options.sampleRate??1),start=Date.now();
  return this.storage.run({traceId,spanId,sampled},async()=>{
   try{const output=await fn();if(sampled)this.capture({...input,output:input.output === undefined ? output : input.output},traceId,spanId,parent?.spanId,start,"ok");return output}
   catch(error){if(sampled)this.capture(input,traceId,spanId,parent?.spanId,start,"error");throw error}
  });
 }
 record(input:SpanInput):void{
  const parent=this.storage.getStore();
  if(!(parent?.sampled??Math.random()<(this.options.sampleRate??1)))return;
  this.capture(input,parent?.traceId??randomBytes(16).toString("hex"),randomBytes(8).toString("hex"),parent?.spanId,Date.now(),"ok");
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
