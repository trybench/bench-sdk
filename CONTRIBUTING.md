# Contributing

Thanks for helping improve Bench's beta SDKs. Open an issue describing the behavior
before a large change. Keep language packages independent and preserve the shared
privacy/report contract. Include a regression test for behavior changes.

## Local checks

- TypeScript: `npm ci && npm run typecheck && npm test`.
- Python: `PYTHONPATH=python/src python -m unittest discover -s python/tests -v`.
- Go, from `go/`: `go test -race ./... && go vet ./...`.
- Rust, from `rust/`: `cargo test --locked && cargo clippy --locked --all-targets -- -D warnings`.

Use synthetic data and local receivers. Never commit credentials, customer traces,
provider outputs or personal data. Telemetry must preserve application return values
and exceptions. Test missing data, cancellation, redaction and delivery failures.

Read [SECURITY.md](SECURITY.md) for private reporting and [PUBLISHING.md](PUBLISHING.md)
for maintainer release instructions. Package versions are immutable after publication.
Contributions are licensed under the repository's Apache-2.0 license.
