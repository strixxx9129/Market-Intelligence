# Security policy

## Reporting a vulnerability

Please **do not open a public issue**. Report privately through
[GitHub security advisories](https://github.com/strixxx9129/Market-Intelligence/security/advisories/new)
with steps to reproduce and the impact you expect. You'll get an acknowledgement within a few
days; fixes are released as patch versions and credited in the advisory (unless you prefer
otherwise).

## Supported versions

Only the latest release and `main` receive security fixes.

## Scope

In scope: the API (auth, API keys, rate limits, data exposure), the website, CI/CD and the
container images in this repository. Out of scope: the third-party platforms themselves (Fly.io,
Cloudflare, Kaggle), denial of service by volume, and findings that need a compromised
maintainer account.

How the deployment is hardened is described in
[ADR 0012](docs/adr/0012-deployment.md#security).
