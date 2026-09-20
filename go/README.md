# Bench Go SDK

Trace Go applications, agents and tools. Go 1.22+. Standard library only. Apache-2.0.
This is an unpublished preview. In your test application's `go.mod`, use:

```go
require github.com/trybench/bench-sdk/go v0.0.0
replace github.com/trybench/bench-sdk/go => /absolute/path/to/bench-sdk/go
```

```go
client, err := bench.New(bench.Options{
    APIKey: os.Getenv("BENCH_API_KEY"),
    Repository: "your-team/your-app",
    Branch: "main",
    Environment: "staging",
    SystemName: "Support agent",
})
if err != nil { return err }

reply, err := bench.Trace(ctx, client,
    bench.SpanInput{Name: "support-request", Kind: "AGENT"},
    func(ctx context.Context) (string, error) {
        return handleRequest(ctx)
    },
)
client.Flush(ctx)
```

Import `bench "github.com/trybench/bench-sdk/go"`. Pass the callback's `ctx`
into nested tool and model traces, including goroutines, to preserve parentage.
The result and original error are returned unchanged. For streaming, use
`ctx, span := client.StartSpan(ctx, input)`, defer `span.End()`, and call
`span.SetOutput(value)` or `span.SetError()` when the operation finishes.

Wrap calls in Google ADK, LangChainGo or custom applications explicitly. Automatic
framework instrumentation is not included. Set `ComponentID` to an existing Bench
prompt component to connect events to its saved criteria.

Content capture is off by default. To enable it, set `CaptureContent: true` and
supply `SpanInput.Input`. Returned output is captured automatically by `Trace`.
Built-in filtering removes common secrets and personal-data patterns before
transmission. `Redact func(any) any` can remove extra fields. It runs before the
built-in filters. Unrecognized personal information can remain in free text.

Call `Flush(ctx)` explicitly, and `Shutdown(ctx)` after application requests
finish. Use a fresh, bounded context when the request context has been canceled.
The queue defaults to 200 spans. Delivery retries once with the same IDs for
transient failures. `Stats()` exposes dropped events; `OnError` receives only
fixed messages. Capturing traces does not run paid evaluations.

## Test your application

`client.EvaluateSystem(ctx, options)` calls your real application with pinned JSON
cases and returns a redacted report without uploading it.

```go
report, err := client.EvaluateSystem(ctx, bench.SystemEvaluationOptions{
    SourceRevision: os.Getenv("GIT_COMMIT_SHA"), // Full 40-character commit SHA
    ContextRevision: "refund-policy-v1",
    Cases: []bench.SystemCase{{
        ID: "outside-refund-policy", Split: "regression",
        Input: map[string]any{"days": 45},
        ExpectedOutput: bench.Expect(map[string]any{"refunded": false}),
        ForbiddenTools: []string{"issue-refund"},
    }},
    Run: handleRequest, // func(context.Context, any) (any, error)
})
if err != nil { return err }
if !report.Passed() { return fmt.Errorf("application checks failed or are incomplete") }
// Explicit upload when desired:
err = client.PublishSystemEvaluation(ctx, systemID, report)
```

Use `bench.Expect(value)` to assert an output/state, including explicit JSON null
with `bench.Expect(nil)`. Omitted pointers mean no assertion. For state checks,
set `ExpectedState` and `Observe func(context.Context, string) (any, error)`.
It must read the authoritative test state independently of the final reply.
JSON input numbers arrive as `json.Number` to preserve large identifiers. Pass the
provided context into every nested tool/model trace and await all goroutines.

`client.SimulateSystem(ctx, bench.SimulationOptions{...})` takes the same cases and
revisions plus `CreateSession func(context.Context, any) (bench.SimulationSession,
error)`. Use `bench.SimulationInput{InitialState: ..., Turns: []any{...}}` as case
input and require `ExpectedState`. The returned session implements `Turn`,
`Observe` and `Close`, each accepting `context.Context`. A fresh session handles
1 to 20 scripted turns; observed state is snapshotted before `Close` resets it.
`bench.EvaluationCaseID(ctx)` identifies the current case.

Timeout defaults to 30 seconds per case; set `Timeout` up to five minutes.
Cancel the supplied context to stop. Missing assertions/observations, unfinished
spans and timeouts are incomplete and fail `Passed()`. Go cannot forcibly stop an
uncooperative goroutine. Use isolated test dependencies and cooperative callbacks.
Cleanup gets a fresh five-second context, which it must honor.

Application tests record redacted content locally, even when production tracing
is metadata-only. They do not upload reports or spend evaluation credits by
implicitly running a judge. Use synthetic test data. Automatic framework adapters
are coming soon.

```sh
go test -race ./...
go vet ./...
```

See [Bench documentation](https://docs.usebench.ai/sdk/go) and the repository's
[publishing guide](../PUBLISHING.md).

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
