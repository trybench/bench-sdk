---
name: bench-sdk
description: Install and verify Bench's Node.js server-side production tracing SDK in an application, or update its instrumentation. Use for Bench SDK setup, not arbitrary OpenTelemetry configuration.
---

# Bench SDK setup

Read the installed package README for the actual supported API. This preview is
Node.js 20+ ESM, not browser/Python instrumentation or an OTLP collector.
Use the supplied tarball/path; do not assume `@trybench/sdk` is publicly published.

Use the repository, branch, endpoint and key supplied by the user's Bench quick
start. Write the key only into an existing gitignored server environment file.
Do not print it or copy it into client bundles, tests, examples or commits. If
the user has not supplied a key, leave an environment-variable reference and
direct them to Bench's SDK page. Do not mint keys with broader access.

Create one Bench instance per process. Preserve existing provider configuration,
prompt behavior and auth. Wrap existing calls with `bench.trace`; use nested
calls for parent relationships. `systemName` declares a stable AI-system boundary;
it does not automatically discover business objectives. Only use component IDs
read from Bench, never invented ones.

Start metadata-only (`captureContent: false`). Content capture and automatic
paid checks are separate opt-ins. A setup key has a zero evaluation cap. Do not
enable automatic evaluation, enable optional external judging, or modify caps
as an installation step. Built-in redaction is not guaranteed anonymization.

Verify with injected fetch and synthetic inputs. Assert original return/error
behavior, no input/output in metadata-only payloads, and no extra model calls.
Flush in the platform's serverless lifecycle hook or shut down after in-flight
requests on process exit. Telemetry failure must not break the application.

Report changed files, test outcome, missing credentials and actual limitations.
Never describe recorded runtime metadata as a completed benchmark or treat a
model judgment as a verified golden label. Publication or deployment requires
the user's separate authorization.
