# Bench Rust SDK

Trace Rust applications, agents and tools. Tokio applications, Rust 1.88+.
Apache-2.0. This is an unpublished preview. In the application's Cargo manifest:

```toml
[dependencies]
trybench-sdk = { path = "/absolute/path/to/bench-sdk/rust" }
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

This crate currently provides tracing. Application evaluation and scripted
simulation helpers are available in JavaScript; native Rust helpers and automatic
framework adapters are planned.

```sh
cargo test
cargo clippy --all-targets -- -D warnings
cargo publish --dry-run
```

See [Bench documentation](https://docs.usebench.ai/sdk/rust) and the repository's
[publishing guide](../PUBLISHING.md).
