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

This package currently provides tracing. Application evaluation and scripted
simulation helpers are available in JavaScript; native Go helpers and automatic
framework adapters are planned.

```sh
go test -race ./...
go vet ./...
```

See [Bench documentation](https://docs.usebench.ai/sdk/go) and the repository's
[publishing guide](../PUBLISHING.md).
