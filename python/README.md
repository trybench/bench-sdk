# Bench Python SDK

Trace Python applications, agents and tools without changing their behavior.
Python 3.10+. No third-party runtime dependencies. Apache-2.0.

Install with `python -m pip install trybench-sdk`. The Python import is `bench_sdk`.

```python
import os
from bench_sdk import Bench

bench = Bench(
    api_key=os.environ["BENCH_API_KEY"],
    repository="your-team/your-app",
    branch="main",
    environment="staging",
    system_name="Support agent",
    endpoint=os.environ.get("BENCH_API_BASE_URL", "https://api.trybench.ai"),
)

try:
    with bench.trace("support-request", kind="AGENT") as span:
        # Call your existing application here.
        result = handle_request()
        span.set_output(result)
finally:
    # Deliver success and failure events after this application's work finishes.
    bench.shutdown()
```

Use the same `with bench.trace(...)` around `await agent.ainvoke(...)` in an
async application, then `await bench.aflush()`. Context is preserved across
await points and inherited by nested tasks. Wrap tools in nested spans with
`kind="TOOL"`. Separate requests started outside a parent get separate trace IDs.

Works with explicit wrappers around Deep Agents, LangGraph, LangChain, model
clients and custom Python code. It does not automatically instrument framework
internals or consume a streaming result; keep the span open while reading the stream.

Inputs, outputs and custom attributes are omitted by default. With permission,
set `capture_content=True`, pass `input=...` and call `span.set_output(...)`.
Built-in matching removes common secrets, emails, supported phone numbers, IPv4
addresses, card patterns and sensitive structured fields before transmission.
Add a `redact(value)` callback for application-specific data. These rules do not
recognize every personal detail in free text.

Set `component_id` to a real prompt component from Bench to link the event to
its criteria. Use operational attributes such as `gen_ai.usage.input_tokens`
and `gen_ai.usage.output_tokens` for usage counts. Never invent component IDs.

The queue defaults to 200 spans. Flush explicitly at request or process lifecycle
boundaries. Transient delivery failures retry once with unchanged IDs, then drop
the batch. `bench.stats` reports queued and dropped spans. `on_error(message)`
receives a fixed message, without content or keys. A telemetry failure does not
replace an application exception. This is a bounded queue, not durable storage.

## Test your application

`await bench.evaluate_system(...)` calls your application's request handler with
pinned cases. It captures the real nested tool/model traces, compares the final
output and independently observed state, and returns a redacted report locally.
Use a test database and test service credentials.

```python
report = await bench.evaluate_system(
    source_revision=os.environ["GIT_COMMIT_SHA"],  # Full 40-character commit SHA
    context_revision="refund-policy-v1",
    cases=[{
        "id": "outside-refund-policy", "split": "regression",
        "input": {"days": 45}, "expected_output": {"refunded": False},
        "forbidden_tools": ["issue-refund"],
    }],
    run=lambda request, context: handle_request(request),
)
assert report["summary"]["status"] == "completed"
assert all(case["status"] == "passed" for case in report["cases"])
# Explicit upload, only when you want this report saved in Bench:
await bench.publish_system_evaluation(int(os.environ["BENCH_SYSTEM_ID"]), report)
```

`run(input, context)` and `observe(context)` may be synchronous or asynchronous.
When a case has `expected_state`, supply `observe` to read the authoritative test
state. Context provides `case_id`, `cancelled`, `signal`, `deadline` and
`raise_if_cancelled()`. Inputs are JSON snapshots; changes inside the application
do not change the case's assertions.

`await bench.simulate_system(...)` accepts the same revisions and cases, plus
`create_session(initial_state, context)`. Cases use
`input={"initial_state": {...}, "turns": [...]}` and require `expected_state`.
Return an object with `turn(message, context)`, `observe()` and `close()` methods.
A fresh session receives 1 to 20 scripted customer turns. Bench snapshots observed
state before closing the session. `context.turn_index` identifies the turn.

Both helpers use a 30-second timeout per case, configurable with `timeout` up to
300 seconds. Pass a `threading.Event` as `cancel_event` to stop the suite. Missing
assertions, missing state, unfinished traces and timeouts remain incomplete.
Callbacks must honor cancellation; Python cannot forcibly stop a synchronous
thread. Await all child work and isolate external side effects. These helpers
are local execution, not a process sandbox or a hosted verification claim.

Tests record redacted content even when production capture is metadata-only, so
use synthetic inputs. Reports are not uploaded and paid checks are not started
unless you take a separate explicit action. Automatic framework adapters are
coming soon.

Run checks from this directory:

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
python -m build
python -m twine check dist/*
```

See [Bench documentation](https://docs.usebench.ai/sdk/python) and the repository's
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
