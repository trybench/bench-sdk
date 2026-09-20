---
name: bench-sdk
description: Install and verify Bench's server-side SDK for JavaScript, Python, Go or Rust, or update its instrumentation. Use for Bench SDK setup, not arbitrary OpenTelemetry configuration.
---

# Bench SDK setup

Read the selected language package README for its actual API. JavaScript uses
Node.js 20+ ESM. Python, Go and Rust packages live in their named subdirectories.
These are server clients, not browser instrumentation or OTLP collectors.
Use the supplied package or pinned source; do not assume registry publication.

Use the repository, branch, endpoint and key supplied by the user's Bench quick
start. Write the key only into an existing gitignored server environment file.
Do not print it or copy it into client bundles, tests, examples or commits. If
the user has not supplied a key, leave an environment-variable reference and
direct them to Bench's SDK page. Do not mint keys with broader access.

Create one client per process. Preserve existing provider configuration,
prompt behavior and auth. Use the language's trace wrapper or span guard, passing
parent context through asynchronous tasks and goroutines. System name declares a stable AI-system boundary;
it does not automatically discover business objectives. Only use component IDs
read from Bench, never invented ones.

Start metadata-only (`captureContent: false` or the language's equivalent). Content capture and automatic
paid checks are separate opt-ins. A setup key has a zero evaluation cap. Do not
enable automatic evaluation, enable optional external judging, or modify caps
as an installation step. Built-in redaction is not guaranteed anonymization.

Verify with a local HTTP receiver or injected transport and synthetic inputs. Assert original return/error
behavior, no input/output in metadata-only payloads, and no extra model calls.
Native clients require explicit flush. Flush in the platform's serverless lifecycle hook or shut down after in-flight
requests on process exit. Telemetry failure must not break the application.

Report changed files, test outcome, missing credentials and actual limitations.
Never describe recorded runtime metadata as a completed benchmark or treat a
model judgment as a verified golden label. Publication or deployment requires
the user's separate authorization.

Only the JavaScript package currently has application evaluation and scripted
simulation helpers. Do not invent equivalent helper APIs in the native packages.
