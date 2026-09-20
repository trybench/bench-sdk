# Bench SDKs · Beta

Official clients for [Bench](https://usebench.ai). Record AI applications and
run repeatable checks on their prompts, tools and resulting state.

**Beta, version 0.1.0.** APIs may evolve. Pin versions and test upgrades in staging.

| Language | Install | Guide | Example |
| --- | --- | --- | --- |
| TypeScript / JavaScript | `npm install @benchai/sdk` | [TypeScript and JavaScript](https://docs.usebench.ai/sdk/typescript) | [Refund simulation](https://github.com/trybench/bench-sdk/blob/main/examples/typescript/simulate-refund.mjs) |
| Python | `python -m pip install trybench-sdk` | [Python](https://docs.usebench.ai/sdk/python) | [Refund simulation](https://github.com/trybench/bench-sdk/blob/main/python/examples/simulate_refund.py) |
| Go | `go get github.com/trybench/bench-sdk/go@v0.1.0` | [Go](https://docs.usebench.ai/sdk/go) | [Refund simulation](https://github.com/trybench/bench-sdk/blob/main/go/examples/refund/main.go) |
| Rust | `cargo add trybench-sdk` | [Rust](https://docs.usebench.ai/sdk/rust) | [Refund simulation](https://github.com/trybench/bench-sdk/blob/main/rust/examples/refund.rs) |

All four packages support tracing, local application evaluation, scripted
simulations and explicit report publication. They share a report format and
privacy contract. Use explicit wrappers for your framework; more automatic
framework adapters are coming soon.

This repository contains client libraries and local test helpers. Bench's hosted
scanning, judging, optimization and repair services are separate server software.

[Documentation](https://docs.usebench.ai) · [Releases](https://github.com/trybench/bench-sdk/releases) · [Contributing](https://github.com/trybench/bench-sdk/blob/main/CONTRIBUTING.md) · [Security](https://github.com/trybench/bench-sdk/blob/main/SECURITY.md) · [Apache-2.0](https://github.com/trybench/bench-sdk/blob/main/LICENSE)

## JavaScript and TypeScript

Capture server-side AI interactions without changing their return values or errors.
Available on every plan. Trace collection never starts a paid check by itself.

Install the JavaScript and TypeScript package:

```sh
npm install @benchai/sdk
```

Node.js 20+ and ESM are supported. Use the SDK page in Bench to create a
repository-scoped key and copy a complete setup prompt into your coding agent.
The default setup key has a zero evaluation cap. Store it only in a gitignored,
server-side environment file, never in `NEXT_PUBLIC_*` or `VITE_*` variables.

```ts
import { Bench } from '@benchai/sdk'

const bench = new Bench({
  apiKey: process.env.BENCH_API_KEY!,
  repository: process.env.BENCH_REPOSITORY!, // owner/repository
  branch: process.env.BENCH_BRANCH!,
  endpoint: process.env.BENCH_API_BASE_URL, // http://127.0.0.1:8080 locally
  systemName: 'Customer support',
  captureContent: false,
})

const result = await bench.trace({ name: 'Reply', kind: 'LLM' }, () => existingCall())
// On process shutdown, after in-flight application requests finish:
await bench.shutdown()
```

`systemName` declares an application boundary. Bench creates a runtime system
for that repository, branch and name on the first trace. It does not infer
business intent or assert that the runtime has already been evaluated. Connect
GitHub or upload prompts to discover evaluable prompt components. Pass their
actual `componentId` in a span, or link recorded spans to a prompt in Production.
Nested `bench.trace` calls preserve trace/parent IDs through async execution.

For AI SDK, Mastra, OpenAI Agents or custom Node workflows, wrap the existing
server call. This release does not automatically patch those frameworks.

## Content and lifecycle

Metadata-only is the default. Request/response capture requires explicit
`captureContent: true` plus `input` on the span. The callback's returned value
becomes the output unless an explicit output was supplied. Prefer a minimal
output projection to whole provider responses. Token usage can be attached with
`gen_ai.usage.input_tokens` and `gen_ai.usage.output_tokens` attributes.

Use `redact(value)` for your domain's identifiers. Built-in redaction handles
common secrets, email addresses and structured sensitive fields, but cannot
guarantee anonymization of arbitrary prose. Never send data without authority.

Exports run in the background in bounded batches. The default queue holds 200
span records, retries once with stable IDs, then drops failed batches. Inspect
`bench.stats` or supply `onError` for telemetry health. It is intentionally not
a durable local spool. Run `await bench.flush()` in a supported serverless
lifecycle hook before the environment freezes; don't call `shutdown` per request
on a reused process. Delivery is not guaranteed after abrupt process exit.

## Checks and feedback

The API retains accepted, redacted traces for 30 days. This is Bench's JSON
endpoint. Browser instrumentation and OTLP exporter adapters are not included.

Production checks run as durable background jobs, using pinned prompt criteria.
They consume one evaluation each and share web/MCP account and key limits.
Automatic checks require per-component opt-in and a key with a nonzero allowance.
Captured model output is not a reference answer. Review a failure, then add its
expected behavior to the test library to include it in future benches.

Independent checks run in Bench and require no extra SDK dependency or provider
key. No automatic code deployment or business-policy rewrite occurs.

The setup skill is at `skills/bench-sdk/SKILL.md`. Documentation is maintained in
the separate `bench-docs` repository and published at https://docs.usebench.ai/sdk/quickstart.
Use the API address shown on your Bench SDK setup page. Installing this package
does not deploy your application or enable automatic paid evaluations.

### Scripted customer simulations

`bench.simulateSystem` runs 1–20 scripted turns per case against a fresh application
session. Each case provides `input: { initialState, turns }`, `expectedState`, and
optionally `businessOutcome`, `expectedOutput`, tool assertions and suite split.
Provide `createSession(initialState, { caseId, signal })` returning `turn(message,
{ signal, turnIndex })`, `observe()` and `close()` callbacks. Keep your real app and
tool wrappers; replace external services with test fixtures. `observe` reads the
fixture's actual state independently of the reply, and `close` resets/releases it.

For single-call tests, `evaluateSystem` also accepts an `observe({ caseId, signal })`
callback and `expectedState` assertions. Missing state observation produces an
incomplete result. Reports retain the SDK environment, observed state and business
outcome alongside the reply and traces. Use unchanged cases and context for baseline
and candidate comparisons. These are scripted simulations and client-reported
observations; the SDK does not automatically clone external services or synthesize
an adaptive customer. See [application testing](https://docs.usebench.ai/sdk/system-evaluation) for the complete example.

## Latency, tool calls and cost

Every recorded call carries start/end timestamps, status, parent span ID and an
automatically measured `bench.duration_ms` from a monotonic clock. Wrap each tool
execution, including retries, with a TOOL span to retain its individual timing.
Use `gen_ai.operation.name=execute_tool` and `gen_ai.tool.name` for tool identity.
Production sampling can omit traces; a rate of 1 records each instrumented call.
The bounded delivery queue is not a guarantee against network or process loss.

Add `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`,
`gen_ai.usage.input_tokens` and `gen_ai.usage.output_tokens` when your provider
returns them. Add `bench.cost.usd` for the cost of that individual call and
`bench.cost.source` as `reported` or `estimated`. For estimates, also include
`bench.cost.pricing_version`. These fields survive metadata-only capture, so you
can measure usage without recording prompts or responses. Missing cost is unknown,
not zero. Do not repeat a child cost on its parent or count overlapping token
categories twice. The SDK does not guess provider prices or a tool's own charges.

The `gen_ai.*` names follow selected OpenTelemetry conventions. `bench.cost.*` and
`bench.duration_ms` are Bench extensions. Events currently use Bench JSON over
HTTPS; this release is not an OTLP exporter or collector.
