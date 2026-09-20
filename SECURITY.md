# Security

The SDKs are in beta. Keep keys on trusted servers and use repository-scoped keys.
Metadata-only capture is the default. Built-in filtering removes common secret and
personal-data patterns; add application-specific filtering before enabling content.
Never use real customer data in examples or public bug reports.

Report suspected vulnerabilities privately to [contact@usebench.ai](mailto:contact@usebench.ai)
with the affected package/version, a minimal synthetic reproduction and impact.
Do not include live credentials or customer data. Do not open a public issue for an
unfixed vulnerability. Latest beta releases receive security fixes; pin and update
versions deliberately.

HTTPS is required except for loopback development receivers. Redirects must not
receive authentication headers. Local evaluation helpers are not process sandboxes:
use isolated test dependencies and callbacks that honor cancellation.
