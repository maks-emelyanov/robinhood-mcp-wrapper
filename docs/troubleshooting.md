# Troubleshooting

Start by recording the wrapper version, Python version, command or route, and structured error
code. Keep authorization URLs, callback URLs, credential files, API keys, account identifiers,
and trading data out of shared logs.

```bash
uv run --locked robinhood-mcp --version
uv run --locked python --version
```

## Installation and configuration

The project requires Python 3.14. Use `uv sync --locked --all-groups` from the repository root
and `uv run --locked robinhood-mcp --help` to verify the CLI without authenticating. If a
dependency change makes the lockfile stale, update `uv.lock` deliberately with `uv lock`; do not
ignore the mismatch during routine installation.

`.env` files are not loaded by the wrapper. If an override appears to be ignored, check that
your shell or process manager exports it. Check the variable name against
[the configuration table](../README.md#configuration), and restart a running gateway after
changing its environment.

`configuration_error` indicates an invalid setting. Check the MCP HTTPS URL, redirect URI,
gateway port, timeouts, and bind host. Non-loopback gateway binding requires an API key.

## Login and callback failures

| Symptom | Action |
| --- | --- |
| `authentication_required` | Run `auth login`; use `--force` if stored credentials need reauthorization |
| Credentials already exist | Inspect `auth status`; reauthorize with `auth login --force` if needed |
| An authorization flow is already in progress | Complete that flow, allow it to expire, or restart the gateway before starting another |
| Browser cannot be opened | Use `auth login --manual` and open the displayed URL in a browser |
| Port `8765` is in use | Stop the conflicting listener or use the running gateway's OAuth routes |
| Callback does not match the redirect | Paste the complete callback URL without editing its scheme, host, port, path, or query |
| Missing code/state or a mismatched state | Submit the valid callback for the current flow; malformed callbacks do not complete it |
| Callback authorization was rejected | Begin a fresh flow and complete it once before it expires |
| Callback has no active flow | A restart or expiry removed the pending flow; begin a new flow in the running process |

Manual login works even when the browser cannot load the loopback callback page: copy the full
URL from its address bar after the redirect. Treat that URL as a secret. Never copy a callback
from an older flow into a newer one.

The CLI browser flow uses a one-shot listener, while REST authorization uses the gateway's
listener. Avoid running both on the default callback port. Changing the gateway port alone
does not change the registered redirect URI.

`auth status` inspects local storage and does not contact Robinhood. A true `authenticated`
field can coexist with expired or revoked credentials. Run `tools list --refresh` to verify
the session. Account eligibility and upstream access restrictions need Robinhood support.

## Credential-store failures

`credential_store_error` covers inaccessible paths, unsafe file ownership/type, corrupt JSON,
or invalid stored data. Print the configured path using the command in
[operations](operations.md#credential-lifecycle); do not print the file itself.

The file must be a regular file owned by the current OS user, and symbolic links are rejected.
Ensure its parent directory is writable by that user. On POSIX, the wrapper tightens overly
broad file permissions to `0600`; failure to change them also prevents reading.

If the file is corrupt, stop processes using it and move it into a private location outside the
repository before reauthenticating. Logout may also fail when the store cannot be read. Do not
paste the corrupted file into an issue. A fresh file creates a fresh local registration when
none is stored. To intentionally discard a healthy registration, use `auth logout --forget-client`.

## Gateway authentication

`invalid_api_key` is the gateway bearer-key check. Include
`Authorization: Bearer $ROBINHOOD_API_KEY` when a key is configured; this also protects `/docs`
and OpenAPI routes. `/healthz` is intentionally public and is not evidence of OAuth access.

`authentication_required` is the Robinhood OAuth session check. Supplying a wrapper API key
does not log in to Robinhood. Complete OAuth using the CLI before starting the gateway or use
the REST authorization flow.

## Tool schemas, pagination, and upstream errors

- `tool_validation_error`: use the exact discovered name and inspect `inputSchema` for required
  fields and accepted values. JSON arguments must be an object. Refresh discovery after an
  upstream schema change.
- An incomplete tool/resource/prompt list: CLI and REST listing return one page. Follow
  `nextCursor` with `--cursor` or the REST `cursor` query parameter. Python's `list_all_tools()`
  collects all tool pages.
- `upstream_mcp_error`: capture the redacted error code/details and check server capability
  discovery. A resource or prompt operation may not be offered by the server.
- `upstream_unavailable` or `upstream_timeout`: verify network access and endpoint configuration.
  Discovery may be attempted again; a mutating tool call requires checking its account outcome
  before any resubmission.
- HTTP 200 with `isError: true`: the MCP tool reported a failure. Inspect content and structured
  content; HTTP status alone is insufficient.

Use `--arguments @path/to/file.json` for complex payloads to avoid shell-quoting mistakes. Never
turn a discovery command into a live order merely to diagnose connectivity.

When opening a GitHub issue, include a minimal reproduction using synthetic data, the wrapper
version or commit, OS and Python version, and redacted error code. Use
[SECURITY.md](../SECURITY.md) for vulnerabilities instead of a public issue.
