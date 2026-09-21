# Bench Rust SDK · Beta

**Beta, version 0.1.0.** Pin versions and test upgrades in staging.

Trace Rust applications, agents and tools. Tokio applications, Rust 1.88+.
Apache-2.0. Add the public crate to your application:

```sh
cargo add trybench-sdk
```

```rust
use trybench_sdk::{Bench, Options, SpanInput};

let mut options = Options::new(
    std::env::var("BENCH_API_KEY")?, "your-team/your-app", "main",
);
options.environment = Some("staging".into());
options.system_name = Some("Support agent".into());
let bench = Bench::new(options)?;

let reply = bench.trace(None, SpanInput::new("support-request").kind("AGENT"),
    |context| async move {
        // Pass Some(&context) to nested model and tool traces.
        handle_request().await
    },
).await;
bench.flush().await;
let reply = reply?;
```

Returned outputs must implement `serde::Serialize`. Pass the returned
`TraceContext` explicitly to nested tasks. There is no global task-local state.
For streams, keep a `bench.start_span(...)` guard alive while consuming the
stream. Call `set_output` on success, or `set_error` on failure. Dropping an
unfinished guard records an error. Request error messages are not captured.

Wrap Rig, model-client and custom application calls explicitly. Automatic
framework adapters are not included. Set `SpanInput.component_id` using a real
Bench prompt component to connect events to its criteria.

`capture_content` defaults to `false`. Inputs, outputs and arbitrary attributes
are omitted. Built-in filters remove common secrets, supported personal-data
patterns and sensitive structured fields before sending enabled content.
`Options.redact` accepts a thread-safe callback returning a filtered JSON value
or an error, which drops that span. Pattern matching cannot anonymize all prose.

Call `flush().await` at lifecycle boundaries and `shutdown().await` after active
requests finish. The queue defaults to 200 spans. Transient delivery failures
retry once with the same IDs, then increment `stats().dropped`. HTTPS is required
except on loopback. Redirects are never followed. Capturing events does not run
paid evaluations.

## Test your application

`bench.evaluate_system(options, application).await` runs your application's
request handler on pinned JSON cases and returns a redacted local report.

```rust
use serde_json::json;
use trybench_sdk::{Application, EvaluationOptions, SystemCase};

let mut case = SystemCase::new("outside-refund-policy", json!({"days": 45}));
case.expected_output = Some(json!({"refunded": false}));
case.forbidden_tools = vec!["issue-refund".into()];
let options = EvaluationOptions::new(
    std::env::var("GIT_COMMIT_SHA")?, "refund-policy-v1", vec![case],
);
let app = Application::new(|input, context| async move {
    // Pass context.trace to your actual nested tool/model traces.
    handle_request(input, context).await
});
let report = bench.evaluate_system(options, app).await?;
assert!(report.passed());
// Explicit upload when desired:
bench.publish_system_evaluation(system_id, &report).await?;
```

Use `Application::with_observer` to read authoritative test state independently
of the final reply, and `case.expected_state` to assert it. `Some(Value::Null)`
is an explicit null assertion; `None` omits it. `EvaluationContext` provides the
case ID, parent trace, `is_cancelled()` and `cancelled().await`.

`bench.simulate_system(options, create_session).await` accepts cases with
`input: {"initialState": {...}, "turns": [...]}` and `expected_state`. The async
factory receives initial state and evaluation context, returning
`SimulationSession::new(turn, observe, close)`. The turn callback accepts a JSON
message and context. Observe/close callbacks take no arguments; share fixture
state with `Arc<Mutex<_>>` or your test database. Bench snapshots state before
close/reset, using a fresh session for each 1-to-20-turn scripted conversation.

The default per-case timeout is 30 seconds, configurable up to five minutes.
Missing assertions/state, unfinished spans, capture errors and timeouts remain
incomplete and fail `passed()`. Dropping the evaluation future cancels its task;
callbacks must yield and await child work. This is not an operating-system sandbox.
Session cleanup is attempted on failure or cancellation and is bounded to five
seconds; an abruptly stopped Tokio runtime cannot finish asynchronous cleanup.

Application tests record redacted content locally, even when production tracing
is metadata-only. Use synthetic inputs and isolated test dependencies. Upload
and paid production checks remain separate actions. Automatic framework adapters
are coming soon.

```sh
cargo test
cargo clippy --all-targets -- -D warnings
cargo publish --dry-run
```

See [Bench documentation](https://docs.usebench.ai/sdk/rust) and the repository's
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

## Headless platform management (development)

This branch includes a platform client for all Bench API operations. See the root
README and bench-docs `sdk/platform.mdx` for this language's example. Use the
development API and credentials. Existing tracing, real app evaluation and
simulation APIs are unchanged. Platform calls do not execute the app implicitly.
