# Bench Python SDK

Trace Python applications, agents and tools without changing their behavior.
Python 3.10+. No third-party runtime dependencies. Apache-2.0.

This is an unpublished preview. From the SDK checkout, run `python -m pip install ./python`.
The planned PyPI distribution is `trybench-sdk`; the import is `bench_sdk`.

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

This package supports tracing. The JavaScript package currently also provides
application evaluation and scripted simulation helpers. Native Python helpers
and automatic framework adapters are planned; tracing does not imply those helpers
are available here.

Run checks from this directory:

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
python -m build
python -m twine check dist/*
```

See [Bench documentation](https://docs.usebench.ai/sdk/python) and the repository's
[publishing guide](../PUBLISHING.md).
