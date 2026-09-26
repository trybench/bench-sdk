// OpenTelemetry bridge: forward spans from any instrumented framework to Bench.
//
// The Vercel AI SDK (experimental_telemetry), Mastra, OpenAI Agents, LangChain,
// Strands and other frameworks already describe what ran through OpenTelemetry
// GenAI spans. Register this exporter on the application's tracer provider and
// Bench receives the same agent/model/tool tree, metadata-only, without a
// per-framework adapter. Structural types only: no OpenTelemetry dependency.
import type { Bench, ExternalSpanInput } from './index.js';

type HrTime = [number, number];
/** The subset of @opentelemetry/sdk-trace-base ReadableSpan this bridge reads (SDK 1.x and 2.x). */
export interface ReadableSpanLike {
 name: string;
 spanContext(): { traceId: string; spanId: string };
 parentSpanContext?: { spanId: string };
 parentSpanId?: string;
 startTime: HrTime;
 endTime: HrTime;
 status: { code: number; message?: string };
 attributes: Record<string, unknown>;
 events?: { name: string; attributes?: Record<string, unknown> }[];
}
const toMillis = ([seconds, nanos]: HrTime) => seconds * 1000 + nanos / 1e6;

export function spanToBench(span: ReadableSpanLike): ExternalSpanInput {
 const context = span.spanContext();
 const attributes = { ...span.attributes };
 const kind = attributes['bench.kind'];
 delete attributes['bench.kind'];
 const failed = span.status?.code === 2; // SpanStatusCode.ERROR
 if (failed) {
  // Keep what went wrong: the status message and the recorded exception, so
  // Bench can show the error rather than just an error flag.
  const clip = (value: unknown) => (typeof value === 'string' && value.trim() ? value.trim().slice(0, 2000) : undefined);
  const message = clip(span.status?.message);
  if (message && attributes['error.message'] === undefined) attributes['error.message'] = message;
  for (const event of span.events ?? []) {
   if (event.name !== 'exception') continue;
   for (const key of ['exception.type', 'exception.message'] as const) {
    const value = clip(event.attributes?.[key]);
    if (value && attributes[key] === undefined) attributes[key] = value;
   }
  }
 }
 const model = attributes['gen_ai.request.model'];
 return {
  traceId: context.traceId, spanId: context.spanId,
  parentSpanId: span.parentSpanContext?.spanId ?? span.parentSpanId,
  name: span.name,
  kind: typeof kind === 'string' ? (kind as ExternalSpanInput['kind']) : undefined,
  startedAt: toMillis(span.startTime), endedAt: toMillis(span.endTime),
  status: failed ? 'error' : 'ok',
  attributes, model: typeof model === 'string' ? model : undefined,
 };
}

/** Implements the SpanExporter interface of @opentelemetry/sdk-trace-base. */
export class BenchSpanExporter {
 constructor(private readonly bench: Bench) {}
 export(spans: ReadableSpanLike[], resultCallback: (result: { code: number; error?: Error }) => void): void {
  for (const span of spans) {
   try { this.bench.recordExternalSpan(spanToBench(span)) } catch { /* the application's tracer must never fail because of Bench */ }
  }
  resultCallback({ code: 0 }); // ExportResultCode.SUCCESS
 }
 async shutdown(): Promise<void> { await this.bench.flush() }
 async forceFlush(): Promise<void> { await this.bench.flush() }
}
