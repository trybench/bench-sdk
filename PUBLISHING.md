# Publishing Bench SDKs

These instructions prepare separate package releases from one repository. Nothing
is published by the test workflow. Keep platform deployment on staging until
manual acceptance is complete.

## Package ownership and versions

| Package | Manifest | Release tag |
| --- | --- | --- |
| npm `@benchai/sdk` | `package.json` | `js/v0.1.0` |
| PyPI `trybench-sdk` | `python/pyproject.toml` | `python/v0.1.0` |
| Go `github.com/trybench/bench-sdk/go` | `go/go.mod` | **`go/v0.1.0`** |
| crates.io `trybench-sdk` | `rust/Cargo.toml` | `rust/v0.1.0` |

Confirm control of the npm organization and registry names before the first
release. A missing package search result does not reserve its name. Each language
can release independently. Keep its version, tag, installation instructions and
changelog aligned. Once published, release a new version for corrections.

## Automated publishing (preferred)

`.github/workflows/publish.yml` publishes on release tags through registry
trusted publishing, with no stored tokens: `js/vX.Y.Z` to npm, `python/vX.Y.Z`
to PyPI, `rust/vX.Y.Z` to crates.io. `go/vX.Y.Z` needs no publish step. Each
registry must list this repository and workflow as a trusted publisher first:

- npm, package `@benchai/sdk`: GitHub Actions, organization `trybench`,
  repository `bench-sdk`, workflow `publish.yml`, no environment, allow
  `npm publish`.
- PyPI, project `trybench-sdk`: owner `trybench`, repository `bench-sdk`,
  workflow `publish.yml`, environment `pypi`.
- crates.io, crate `trybench-sdk`: repository `trybench/bench-sdk`, workflow
  `publish.yml`, environment `crates-io`.

Create the `pypi` and `crates-io` GitHub environments in the repository settings
(no secrets needed). The manual commands below remain the fallback.

## Before publishing

- Merge reviewed SDK changes after CI passes. Use the exact tested commit.
- Test the packed artifacts in fresh consumer projects and against staging.
- Review package contents for keys, `.env` files, customer payloads and internal
  reports. Verify the README, license and declaration/type files are included.
- Confirm the public documentation describes the package's actual capabilities.
  All four packages provide tracing, local application evaluation, scripted
  simulations and explicit report publication.
- Confirm the intended API endpoint and authenticate with a newly scoped test key.
- Enable registry account protection and protected release approvals. Prefer
  short-lived trusted publishing credentials where supported.

## License

Keep **Apache-2.0**, which the SDK repository already uses. Each language package
includes the same license. Retain applicable third-party notices and attribution.
The license grants copyright and patent permissions under its terms; it does not
grant trademark rights. Review dependency licenses before distribution.
[Apache license](https://www.apache.org/licenses/LICENSE-2.0).

## JavaScript and TypeScript

From the repository root:

```sh
npm ci
npm run typecheck
npm test
npm pack --dry-run
npm pack
```

Install the resulting tarball in a clean Node application and run a staging trace.
For a release, set the approved version, update the lockfile and inspect a fresh
tarball. The npm package is configured for public access under `@benchai`. Publish
the exact archive tested in the fresh consumer:

```sh
npm publish ./benchai-sdk-0.1.0.tgz --access public
```

For recurring releases, configure npm trusted publishing for the exact repository
and protected workflow. Use the Node/npm versions required by that workflow.
[npm trusted publishing](https://docs.npmjs.com/trusted-publishers/).

## Python

Use a fresh virtual environment, then run from `python/`:

```sh
python -m pip install build twine
python -m build
python -m twine check dist/*
python -m pip install dist/trybench_sdk-0.1.0-py3-none-any.whl
python -m unittest discover -s tests -v
```

For a rehearsal, upload the approved artifacts to TestPyPI and install the wheel
in another environment. TestPyPI has separate accounts and publishing settings.
After approval, upload the same reviewed release artifacts to PyPI:

```sh
python -m twine upload --repository testpypi dist/*
# Separate, approved public release:
python -m twine upload dist/*
```

Configure trusted publishing for the package and protected workflow when using
CI. [Packaging guide](https://packaging.python.org/en/latest/tutorials/packaging-projects/),
[PyPI trusted publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/).

## Go

Run `go test -race ./...` and `go vet ./...` in `go/`. Confirm the module path
matches its public source location. Because the module lives in a subdirectory,
its tag **must include `go/`**:

```sh
# At the repository root, on the approved commit:
git tag go/v0.1.0
git push origin go/v0.1.0
# In a fresh consumer module:
go get github.com/trybench/bench-sdk/go@v0.1.0
```

Public proxy installation requires publicly accessible source. While the
repository is private, use authenticated access with `GOPRIVATE` or a local
`replace` directive. Changing repository visibility is a separate release
decision. [Module publication](https://go.dev/doc/modules/publishing),
[subdirectory version tags](https://go.dev/doc/modules/managing-source).

## Rust

From `rust/`:

```sh
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
cargo package --list
cargo publish --dry-run --locked
```

Install the packaged crate into a fresh consumer and send a staging event.
Confirm ownership of the crate name, version and package metadata. Once approved,
authenticate with the registry and run `cargo publish --locked`. The dry run
packages and verifies the crate without uploading it.
[Cargo publishing guide](https://doc.rust-lang.org/cargo/reference/publishing.html).

## After each release

Install from the public registry or Go proxy with no local path overrides. Send
a synthetic event, verify the correct environment and inspect redaction in Bench.
Update the setup prompt's installation source and the docs to the released version.
Revoke temporary test keys. SDK publication does not deploy the Bench application.
