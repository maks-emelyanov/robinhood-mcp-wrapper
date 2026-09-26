# Robinhood Agentic Trading MCP Wrapper

A Python library, REST API, and CLI for Robinhood's official Agentic Trading MCP server. It uses the official MCP Python SDK for Streamable HTTP, OAuth discovery, dynamic client registration, PKCE, token refresh, and MCP protocol negotiation.

> **Risk warning:** authenticated MCP tools can place real equity, option, or crypto orders in your Robinhood Agentic Account. This wrapper intentionally does not add a trade-confirmation gate. Inspect the discovered tool schema and arguments before every call.

## Requirements

- Python 3.14
- The [uv](https://docs.astral.sh/uv/) package manager
- A Robinhood account eligible for an Agentic Trading account
- A desktop browser for normal login, or access to a browser for the manual flow

Robinhood documents account setup and supported clients in its [Agentic Trading guide](https://robinhood.com/us/en/support/articles/agentic-trading-overview/).

## Quick start

From a repository checkout, install the locked dependencies and inspect the CLI:

```bash
uv sync --all-groups
uv run robinhood-mcp --help
```

The wrapper reads configuration from the process environment; it does not load `.env` files
itself. `.env.example` lists every supported variable. Export any overrides in your shell or load
them with your preferred environment/process manager before running the CLI or REST gateway.

### Local credential storage

OAuth tokens and dynamic client-registration data are stored in a passwordless JSON file under the platform's per-user data directory. Print the exact path with:

```bash
uv run python -c 'from robinhood_mcp.config import Settings; from robinhood_mcp.storage import FileTokenStorage; print(FileTokenStorage(Settings.from_env()).path)'
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
uv run robinhood-mcp auth login
uv run robinhood-mcp auth status
```

For SSH or headless use, open the displayed URL in any browser. After Robinhood redirects, copy the complete callback URL from the address bar—even if the browser cannot load it—and paste it into the prompt:

```bash
uv run robinhood-mcp auth login --manual
```

`auth logout` deletes tokens but retains the dynamically registered client so future logins reuse it. Add `--forget-client` to delete both.

## CLI

Options that accept JSON (`--arguments` and `--context`) can receive either inline JSON or
`@path/to/file.json`.

```bash
# Discover current tools; no Robinhood tool names are hard-coded.
uv run robinhood-mcp tools list

# Call a discovered tool. This is a transparent pass-through and may trade.
uv run robinhood-mcp tools call TOOL_NAME --arguments '{"field":"value"}'

uv run robinhood-mcp resources list
uv run robinhood-mcp resources list --templates
uv run robinhood-mcp resources read 'RESOURCE_URI'
uv run robinhood-mcp prompts list
uv run robinhood-mcp prompts get PROMPT_NAME --arguments '{"name":"value"}'
uv run robinhood-mcp prompts complete \
  --ref-type prompt \
  --ref PROMPT_NAME \
  --argument-name ARGUMENT_NAME \
  --value PREFIX \
  --context '{"other_argument":"value"}'
```

The wrapper validates tool arguments against the server's current JSON Schema. It preserves MCP content blocks, structured content, metadata, pagination cursors, and `isError`. It never retries a tool call after an ambiguous transport failure, preventing accidental order duplication.

## Python

```python
import asyncio

from robinhood_mcp import RobinhoodMCPClient


async def main() -> None:
    client = RobinhoodMCPClient()

    # Required once. For headless use, call start_login() and flow.complete(url).
    # await client.login_browser()

    async with client:
        tools = await client.list_tools(refresh=True)
        print([tool.name for tool in tools.tools])

        # result = await client.call_tool("a_discovered_tool", {"argument": "value"})
        # print(result.is_error, result.structured_content, result.content)


asyncio.run(main())
```

`RobinhoodMCPClient` also exposes `server_metadata`, paginated and all-page tool discovery,
`list_resources`, `list_resource_templates`, `read_resource`, `list_prompts`, `get_prompt`, and
`complete`. Protocol methods return native `mcp-types` models.

## REST API

Start the single-user, single-worker gateway:

```bash
uv run robinhood-mcp serve
```

OpenAPI documentation is at <http://127.0.0.1:8765/docs>. The API is loopback-only by default. To bind another interface, set a strong bearer key first; startup is refused otherwise:

```bash
export ROBINHOOD_API_KEY='replace-with-a-long-random-value'
uv run robinhood-mcp serve --host 0.0.0.0 --port 8765
```

When a key is configured, send `Authorization: Bearer $ROBINHOOD_API_KEY` to every route except `/healthz` and `/oauth/callback`.

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
uv run pytest
uv run pytest --cov
uv run ruff check .
uv run ruff format --check .
uv build
```

Tests use local/in-process MCP fixtures and never trade. After authenticating, an explicitly enabled live test performs discovery only:

```bash
RUN_LIVE_ROBINHOOD=1 uv run pytest -m live
```

The service is intentionally single-user and single-process. Multi-tenant credential isolation and multiple API workers are out of scope.
