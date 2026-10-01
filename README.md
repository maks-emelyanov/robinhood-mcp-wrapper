# Robinhood MCP Wrapper

[![CI](https://github.com/maks-emelyanov/robinhood-mcp-wrapper/actions/workflows/ci.yml/badge.svg)](https://github.com/maks-emelyanov/robinhood-mcp-wrapper/actions/workflows/ci.yml)
[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-blue)](https://github.com/maks-emelyanov/robinhood-mcp-wrapper/blob/main/pyproject.toml)
[![MIT license](https://img.shields.io/github/license/maks-emelyanov/robinhood-mcp-wrapper)](LICENSE)

A Python library, REST API, and CLI for Robinhood's official Agentic Trading MCP server. It uses the official MCP Python SDK for Streamable HTTP, OAuth discovery, dynamic client registration, PKCE, token refresh, and MCP protocol negotiation.

This is an independent community project, unaffiliated with and not endorsed by Robinhood.

Use the async client in a Python application, inspect available capabilities from the CLI, or
run a local REST gateway for another application. Tool names and schemas come from the upstream
server at runtime. This repository provides the wrapper; account eligibility, available tools,
and order behavior are controlled by Robinhood.

> **Risk warning:** authenticated MCP tools can place real equity, option, or crypto orders in your Robinhood Agentic Account. This wrapper intentionally does not add a trade-confirmation gate. Inspect the discovered tool schema and arguments before every call.

The project is designed for one local user and one gateway process. It does not provide strategy
execution, portfolio risk limits, a paper-trading environment, or multi-tenant account isolation.

- [Architecture and extension points](docs/architecture.md)
- [Gateway operation and credential handling](docs/operations.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Contributing](CONTRIBUTING.md), [security reporting](SECURITY.md), and [changelog](CHANGELOG.md)
- [Maintainer and release workflow](docs/maintaining.md)

## Requirements

- Python 3.14 on Linux or macOS (the CI platforms)
- The [uv](https://docs.astral.sh/uv/) package manager
- A Robinhood account eligible for an Agentic Trading account
- A desktop browser for normal login, or access to a browser for the manual flow

Robinhood documents account setup and supported clients in its [Agentic Trading guide](https://robinhood.com/us/en/support/articles/agentic-trading-overview/).

## Quick start

Clone the repository, install the locked dependencies, and inspect the CLI:

```bash
git clone https://github.com/maks-emelyanov/robinhood-mcp-wrapper.git
cd robinhood-mcp-wrapper
uv sync --locked --all-groups
uv run --locked robinhood-mcp-wrapper --help
uv run --locked robinhood-mcp-wrapper --version
```

Install from this checkout rather than assuming the package has been published to a package
index. `uv sync` installs the package in editable mode. All commands below run from the
repository root. The default test suite and `--help` require no Robinhood credentials.

The wrapper reads configuration from the process environment; it does not load `.env` files
itself. `.env.example` lists every supported variable. Export any overrides in your shell or load
them with your preferred environment/process manager before running the CLI or REST gateway.

### Migrate an existing checkout

After pulling the rename, run `uv sync --locked --all-groups` again to refresh the editable
installation. Update scripts, imports, and service commands to use the new names:

| Previous name | Current name |
| --- | --- |
| `robinhood-mcp` | `robinhood-mcp-wrapper` |
| `robinhood_mcp` imports | `robinhood_mcp_wrapper` imports |
| `python -m robinhood_mcp` | `python -m robinhood_mcp_wrapper` |

The previous names have no compatibility aliases. The distribution name remains
`robinhood-mcp-wrapper`. Environment variables, the default OAuth client name, and credential
storage are unchanged, so existing tokens and client registration remain usable with the same
settings; no credential move or new login is required by the rename.

### Local credential storage

OAuth tokens and dynamic client-registration data are stored in a passwordless JSON file under the platform's per-user data directory. Print the exact path with:

```bash
uv run --locked python -c 'from robinhood_mcp_wrapper.config import Settings; from robinhood_mcp_wrapper.storage import FileTokenStorage; print(FileTokenStorage(Settings.from_env()).path)'
```

The wrapper creates the file atomically and restricts it to the current OS user with mode `0600` on POSIX systems. No keyring password or D-Bus service is required.

> **Credential warning:** the file is not encrypted. Anyone who can read files as your OS user can use its OAuth credentials. Do not commit, copy, or share it.

Override the location when needed:

```bash
export ROBINHOOD_CREDENTIALS_FILE=/absolute/path/to/robinhood-credentials.json
```

## Authenticate

Desktop login starts a one-shot callback listener at `127.0.0.1:8765` and opens Robinhood in the default browser:

```bash
uv run --locked robinhood-mcp-wrapper auth login
uv run --locked robinhood-mcp-wrapper auth status
uv run --locked robinhood-mcp-wrapper tools list --refresh
```

For SSH or headless use, open the displayed URL in any browser. After Robinhood redirects, copy the complete callback URL from the address bar—even if the browser cannot load it—and paste it into the prompt:

```bash
uv run --locked robinhood-mcp-wrapper auth login --manual
```

`auth logout` deletes tokens but retains the dynamically registered client so future logins reuse it. Add `--forget-client` to delete both.

Logging out removes local credentials; it does not revoke authorization at Robinhood. See
[operations](docs/operations.md) for revocation and credential recovery. `auth status` checks
local state; successful discovery verifies that the server accepts the credentials.

## CLI

Options that accept JSON (`--arguments` and `--context`) can receive either inline JSON or
`@path/to/file.json`.

```bash
# Discover current tools; no Robinhood tool names are hard-coded.
uv run --locked robinhood-mcp-wrapper tools list

# CLI listing returns one page. Follow nextCursor using --cursor when present.
uv run --locked robinhood-mcp-wrapper tools list --cursor CURSOR

# Call a discovered tool. This is a transparent pass-through and may trade.
uv run --locked robinhood-mcp-wrapper tools call TOOL_NAME --arguments '{"field":"value"}'

uv run --locked robinhood-mcp-wrapper resources list
uv run --locked robinhood-mcp-wrapper resources list --templates
uv run --locked robinhood-mcp-wrapper resources read 'RESOURCE_URI'
uv run --locked robinhood-mcp-wrapper prompts list
uv run --locked robinhood-mcp-wrapper prompts get PROMPT_NAME --arguments '{"name":"value"}'
uv run --locked robinhood-mcp-wrapper prompts complete \
  --ref-type prompt \
  --ref PROMPT_NAME \
  --argument-name ARGUMENT_NAME \
  --value PREFIX \
  --context '{"other_argument":"value"}'
```

The wrapper validates tool arguments against the server's current JSON Schema. It preserves MCP content blocks, structured content, metadata, pagination cursors, and `isError`. It never retries a tool call after an ambiguous transport failure. Check the account outcome before deciding whether to resubmit.

## Python

```python
import asyncio

from robinhood_mcp_wrapper import RobinhoodMCPClient


async def main() -> None:
    client = RobinhoodMCPClient()

    # Required once. For headless use, call start_login() and flow.complete(url).
    # await client.login_browser()

    async with client:
        tools = await client.list_all_tools(refresh=True)
        print([tool.name for tool in tools])


asyncio.run(main())
```

`RobinhoodMCPClient` also exposes `server_metadata`, paginated and all-page tool discovery,
`list_resources`, `list_resource_templates`, `read_resource`, `list_prompts`, `get_prompt`, and
`complete`. Protocol methods return native `mcp-types` models.

After login, run [examples/discover_tools.py](examples/discover_tools.py) to print the negotiated
server metadata and every tool's schema:

```bash
uv run --locked python examples/discover_tools.py
```

The example performs discovery only and never invokes a tool. When integrating tool execution,
inspect `result.is_error` as well as `result.structured_content` and `result.content`; an MCP
tool can report an error in an otherwise successful protocol response.

## REST API

Start the single-user, single-worker gateway:

```bash
uv run --locked robinhood-mcp-wrapper serve
```

OpenAPI documentation is at <http://127.0.0.1:8765/docs>. The API is loopback-only by default. To bind another interface, set a strong bearer key first; startup is refused otherwise:

```bash
export ROBINHOOD_API_KEY='replace-with-a-long-random-value'
uv run --locked robinhood-mcp-wrapper serve --host 0.0.0.0 --port 8765
```

When a key is configured, send `Authorization: Bearer $ROBINHOOD_API_KEY` to every route except `/healthz` and `/oauth/callback`.

The API key authorizes access to the wrapper's single stored Robinhood session. Keep remote
access behind TLS and a controlled network. For a keyed gateway, add the bearer header to the
REST examples below. See [operations](docs/operations.md) for a concrete setup.

### REST authorization

Start a flow and open the returned `authorization_url`:

```bash
curl -sS -X POST http://127.0.0.1:8765/v1/auth/start \
  -H 'Content-Type: application/json' \
  -d '{"force":false}'
```

The browser callback finishes automatically when it can reach the wrapper. In a headless deployment, submit the copied callback URL with the returned flow ID:

```bash
curl -sS -X POST http://127.0.0.1:8765/v1/auth/complete \
  -H 'Content-Type: application/json' \
  -d '{"flow_id":"FLOW_ID","callback_url":"COMPLETE_CALLBACK_URL"}'
```

### Gateway routes

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/healthz` | Check gateway process health without authentication |
| `GET` | `/v1/auth/status` | Inspect stored and in-progress authorization state |
| `POST` | `/v1/auth/start` | Start an OAuth flow and return its authorization URL |
| `POST` | `/v1/auth/complete` | Complete a headless OAuth flow with its callback URL |
| `DELETE` | `/v1/auth/session` | Log out; use `?forgetClient=true` to also remove registration |
| `GET` | `/oauth/callback` | Receive the browser OAuth redirect |
| `GET` | `/v1/mcp` | Negotiated server identity, protocol, and capabilities |
| `GET` | `/v1/tools` | Discover a page of tools and JSON Schemas |
| `POST` | `/v1/tools/{name}/call` | Validate and invoke a tool exactly once |
| `GET` | `/v1/resources` | List resources |
| `GET` | `/v1/resource-templates` | List resource templates |
| `POST` | `/v1/resources/read` | Read a resource URI |
| `GET` | `/v1/prompts` | List prompts |
| `POST` | `/v1/prompts/{name}/get` | Render a prompt |
| `POST` | `/v1/completions` | Complete a prompt/template argument |

Example discovery and tool call:

```bash
curl -sS http://127.0.0.1:8765/v1/tools
curl -sS -X POST http://127.0.0.1:8765/v1/tools/TOOL_NAME/call \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{"field":"value"}}'
```

An upstream MCP tool failure remains HTTP 200 with `isError: true`. Wrapper failures use
structured error bodies: HTTP 401 for missing authentication, 409 for authorization-flow or
callback conflicts, 422 for tool validation, 502 for upstream failures, 503 for credential-store
failures, and 504 for timeouts.

For example, a missing OAuth session returns:

```json
{"error":{"code":"authentication_required","message":"Run an OAuth login before connecting"}}
```

REST and CLI JSON preserve MCP aliases such as `inputSchema`, `structuredContent`, and
`nextCursor`; Python models expose fields such as `input_schema`, `structured_content`, and
`next_cursor`.

## Configuration

| Environment variable | Default |
| --- | --- |
| `ROBINHOOD_MCP_URL` | `https://agent.robinhood.com/mcp/trading` |
| `ROBINHOOD_MCP_CLIENT_NAME` | `Robinhood MCP Wrapper` |
| `ROBINHOOD_MCP_SCOPE` | `internal` |
| `ROBINHOOD_MCP_REDIRECT_URI` | `http://127.0.0.1:8765/oauth/callback` |
| `ROBINHOOD_CREDENTIALS_FILE` | platform user-data directory |
| `ROBINHOOD_API_HOST` | `127.0.0.1` |
| `ROBINHOOD_API_PORT` | `8765` |
| `ROBINHOOD_API_KEY` | unset |
| `ROBINHOOD_CONNECT_TIMEOUT` | `30` seconds |
| `ROBINHOOD_READ_TIMEOUT` | `300` seconds |
| `ROBINHOOD_OAUTH_TIMEOUT` | `600` seconds |
| `ROBINHOOD_LOG_LEVEL` | `INFO` |

Changing the endpoint, client name, scope, or redirect URI deliberately selects a separate default credential file and dynamic client registration.

## Development and tests

```bash
uv sync --locked --all-groups
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -m 'not live' --cov=robinhood_mcp_wrapper --cov-report=term-missing
uv build --no-sources
uv run --locked python scripts/check_dist.py
```

Tests use local/in-process MCP fixtures and never trade. After authenticating, an explicitly enabled live test performs discovery only:

```bash
RUN_LIVE_ROBINHOOD=1 uv run --locked pytest -m live
```

Live discovery requires existing credentials and network access. CI uses local fixtures and
does not need account secrets. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow
and [docs/maintaining.md](docs/maintaining.md) for release checks.

## Support and license

For reproducible wrapper bugs and feature requests, use
[GitHub Issues](https://github.com/maks-emelyanov/robinhood-mcp-wrapper/issues).
Include your Python version, wrapper version, command, and redacted error code; see
[troubleshooting](docs/troubleshooting.md) before posting logs. Account access, account
eligibility, and executed orders should be handled through Robinhood's support channels.
Report security issues using [SECURITY.md](SECURITY.md).

Distributed under the [MIT license](LICENSE).
