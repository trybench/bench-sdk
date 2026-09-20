# Bench SDKs

One repository, independently released language packages:

| Language | Package | Source and setup |
| --- | --- | --- |
| JavaScript / TypeScript | `@trybench/sdk` | This page; `src/` |
| Python | `trybench-sdk` (`bench_sdk` import) | [Python guide](python/README.md) |
| Go | `github.com/trybench/bench-sdk/go` | [Go guide](go/README.md) |
| Rust | `trybench-sdk` (`trybench_sdk` import) | [Rust guide](rust/README.md) |

All are unpublished previews. Each client supports server tracing, environments,
nested spans, bounded delivery and default redaction. The JavaScript package
also includes application evaluation and simulation helpers. Native equivalents
and automatic framework adapters are coming soon. See [publishing](PUBLISHING.md)
for independent package releases.

## JavaScript and TypeScript

Capture server-side AI interactions without changing their return values or errors.
Available on every plan. Trace collection never starts a paid check by itself.

This package has **not been published**. Build and install it locally:

```sh
npm ci
npm test
npm pack
# In the app to instrument:
npm install /absolute/path/to/trybench-sdk-0.1.0.tgz
```

Node.js 20+ and ESM are supported. Use the SDK page in Bench to create a
repository-scoped key and copy a complete setup prompt into your coding agent.
The default setup key has a zero evaluation cap. Store it only in a gitignored,
server-side environment file, never in `NEXT_PUBLIC_*` or `VITE_*` variables.

```ts
import { Bench } from '@trybench/sdk'

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
The staging setup can use a commit-pinned Git install from this private repository.
It requires GitHub repository access. No npm release is implied by that preview.

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
an adaptive customer. See the application testing guide for the complete example.
