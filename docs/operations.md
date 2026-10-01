# Operations

The REST gateway is designed for a single user and a single process. Start with local access,
complete OAuth, and verify discovery before integrating another application.

## Run locally

From the repository root:

```bash
uv sync --locked --all-groups
uv run --locked robinhood-mcp-wrapper auth login
uv run --locked robinhood-mcp-wrapper tools list --refresh
uv run --locked robinhood-mcp-wrapper serve
```

CLI browser login and the gateway both use port `8765` by default. Finish CLI login before
starting the gateway. If the gateway is already running, use its `/v1/auth/start` endpoint and
callback rather than launching another receiver on the same port.

Check process health with `curl -fsS http://127.0.0.1:8765/healthz`. Health reports only that the
gateway process can answer HTTP; it does not check OAuth, network access, or the upstream MCP
server. Inspect `/v1/auth/status` for local state and `/v1/mcp` or `/v1/tools` for an authenticated
upstream request. OpenAPI is available at `/docs`.

## Configure the process

The wrapper reads process environment variables only. `.env.example` documents them; copying
it to `.env` does not load it. Export values or configure your process manager to provide them.
Resolve relative credential paths against the process's working directory; an absolute path
outside the repository is easier to manage.

The API bind host/port and OAuth redirect URI are separate settings. Changing `--port` changes
the gateway port but does not rewrite `ROBINHOOD_MCP_REDIRECT_URI`. For gateway browser callbacks,
the registered redirect must reach the running gateway. Changing the redirect also selects a
new default credential file and registration.

The default credential filename is derived from the MCP URL, client name, scope, and redirect
URI. If you explicitly reuse `ROBINHOOD_CREDENTIALS_FILE` across changed OAuth settings, reset
the client registration and authenticate again. The file override takes precedence over
automatic filename separation.

The package and CLI rename preserves the default data directory (`robinhood-mcp-wrapper`),
credential filename calculation, and file format. Existing credentials are reused when the
OAuth settings and any file override remain the same. Update service commands using the
[migration steps](../README.md#migrate-an-existing-checkout).

## Protect gateway access

Even on loopback, an API key is useful when other local processes should not have unrestricted
access to the session. Generate a key without placing a literal secret in shell history:

```bash
export ROBINHOOD_API_KEY="$(uv run --locked python -c 'import secrets; print(secrets.token_urlsafe(32))')"
uv run --locked robinhood-mcp-wrapper serve
```

In another trusted shell, provide the same key through your secret manager or process
environment and check access:

```bash
curl -fsS http://127.0.0.1:8765/v1/auth/status \
  -H "Authorization: Bearer $ROBINHOOD_API_KEY"
```

A non-loopback bind is refused unless a key is configured. The key grants access to all wrapper
routes and the single stored account; there are no separate scopes or per-tool permissions.
`/healthz` and `/oauth/callback` remain exempt. Use a trusted network and TLS termination for
remote access. The built-in server does not terminate TLS or provide rate limiting.

Run one worker under a dedicated OS user. Configure a service manager to provide environment
variables, restart the process when needed, and send termination signals for graceful shutdown.
Do not enable multiple Uvicorn workers or share one credential file among independent services:
pending OAuth flows and MCP connections exist only in one process, and file locks do not span
processes.

If a reverse proxy exposes an OAuth callback, ensure the callback URL presented to the app
matches the registered scheme, hostname, port, and path. Proxy header handling must trust only
the actual proxy. An HTTPS redirect requires the manual or gateway flow; the CLI's built-in
browser receiver accepts HTTP loopback redirects only.

## Credential lifecycle

Print the credential path without opening or exposing its contents:

```bash
uv run --locked python -c 'from robinhood_mcp_wrapper.config import Settings; from robinhood_mcp_wrapper.storage import FileTokenStorage; print(FileTokenStorage(Settings.from_env()).path)'
```

The JSON file contains OAuth tokens and dynamic client registration and is not encrypted.
On POSIX, writes use mode `0600`, and newly created directories request mode `0700`. Existing
parent directories keep their permissions; protect them and the OS user account separately.
Do not put the file in a checkout, synchronized folder, public backup, or container image.

`auth status` reports whether credentials and registration are stored; `authenticated: true`
does not prove the token is currently accepted by Robinhood. Successful tool discovery verifies
upstream access. The SDK refreshes tokens when supported. To reauthorize deliberately:

```bash
uv run --locked robinhood-mcp-wrapper auth login --force
```

To remove tokens while retaining client registration:

```bash
uv run --locked robinhood-mcp-wrapper auth logout
```

To remove tokens and registration:

```bash
uv run --locked robinhood-mcp-wrapper auth logout --forget-client
```

These commands remove local state only. Revoke upstream authorization using the controls
provided by Robinhood if credentials may have been exposed. Rotate the gateway API key and
restart the process after an API-key exposure. Stop other processes using the same credentials
before logging out or changing storage.

## Errors, logs, and ambiguous calls

REST responses include `x-request-id`; provide a safe request ID when reporting an error.
Application request logs include method, URL path, status, duration, and request ID, without
request bodies or query strings. The CLI disables Uvicorn's access log so OAuth callback codes
and state do not appear in its default request logging. If you deploy the application with a
different ASGI server or reverse proxy, configure those access logs to omit callback query
parameters, restrict their access and retention, and redact them before sharing.

HTTP 200 on a tool call does not guarantee success: inspect MCP `isError` and the returned
content. A 502 or 504 during a mutating call may mean the outcome is unknown. Check Robinhood's
order/account state before resubmitting; the wrapper deliberately does not retry the call.
It does not maintain an order reconciliation or transaction journal.

See [troubleshooting](troubleshooting.md), [security reporting](../SECURITY.md), and
[architecture](architecture.md) for further details.
