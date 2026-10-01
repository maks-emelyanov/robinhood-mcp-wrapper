# Security policy

This wrapper can access a real Robinhood trading account. Its OAuth credentials and the REST
gateway API key should be treated as secrets that grant account access.

## Reporting a vulnerability

Use [GitHub's private vulnerability reporting form](https://github.com/maks-emelyanov/robinhood-mcp-wrapper/security/advisories/new).
Private reporting is enabled for this repository. Reports go to the repository maintainers;
the project does not define a separate security email address or guarantee a response time.

Do not post exploit instructions, live credentials, callback URLs, account data, or other
sensitive details in public issues. See [the maintainer guide](docs/maintaining.md) for the
repository's security and release workflow.

A private report should include the affected wrapper version or commit, platform, a minimal
reproduction using synthetic data, impact, and any suggested fix. Report vulnerabilities in
Robinhood's service through Robinhood's own security reporting process.

## Supported scope

Security fixes target the current default branch. There is no guaranteed backport policy for
older releases. Include the exact version or commit in reports so the affected code can be
identified.

The default gateway listens on loopback. Non-loopback binding requires `ROBINHOOD_API_KEY`, but
the gateway does not provide TLS, multiple-user authorization, per-tool permissions, or trade
confirmation. A configured API key grants access to the wrapper's full stored session. The
unauthenticated routes are `/healthz` and `/oauth/callback`; the callback must complete the
active OAuth flow.

OAuth credentials are stored as plaintext JSON in the OS user's data directory. The wrapper
uses atomic writes and owner-only file permissions on POSIX; it does not encrypt the file.
Process-local locks are not cross-process credential-store coordination. Run one gateway
worker, restrict access to the OS account and directory, and avoid sharing one credential file
across concurrent processes.

See [operations](docs/operations.md) for deployment and recovery practices. Financial loss,
strategy performance, account eligibility, and the upstream service's behavior are outside the
wrapper's security-fix scope.
