# Contributing

Contributions should keep the Python client, CLI, and REST gateway consistent. The wrapper
discovers upstream capabilities dynamically; new features should preserve MCP response data
and avoid assumptions about particular trading tools.

Follow the [Code of Conduct](CODE_OF_CONDUCT.md) in issues, pull requests, and other project
spaces.

## Set up a checkout

Use Python 3.14 and [uv](https://docs.astral.sh/uv/). Fork the repository for a pull request,
or clone it directly to explore the project:

```bash
git clone https://github.com/maks-emelyanov/robinhood-mcp-wrapper.git
cd robinhood-mcp-wrapper
uv sync --locked --all-groups
uv run --locked robinhood-mcp-wrapper --help
```

No account or credentials are required to develop with the local test fixtures. Configuration
comes from process environment variables; `.env.example` is a reference and is not loaded
automatically. Keep real credential files outside the checkout.

## Make a change

Create a focused branch and keep the change reviewable. Check [architecture](docs/architecture.md)
for module responsibilities. Add regression coverage for behavior changes, especially OAuth
flow state, error mapping, pagination, validation, and connection lifecycle. Documentation-only
changes generally need command and link verification rather than new tests.

Tool calls can place real orders. Preserve the single-invocation behavior of `call_tool`: an
ambiguous transport failure must never trigger an automatic retry. Use injected storage,
in-process MCP servers, and fake clients in tests. Do not add tests that submit live orders.

Run the same checks used by CI:

```bash
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -m 'not live' --cov=robinhood_mcp_wrapper --cov-report=term-missing
uv build --no-sources
uv run --locked python scripts/check_dist.py
```

`scripts/check_dist.py` validates the built wheel and source archive and smoke-tests the wheel
in an isolated environment outside the source checkout. It expects `uv build` to have completed.

Use `uv run --locked ruff format .` to apply formatting. If you intentionally change dependency
metadata, run `uv lock` and include the updated `uv.lock`; `--locked` rejects stale lockfiles.
Check the final diff for secrets, generated artifacts, unrelated changes, and accurate docs.

The opt-in live test performs server metadata and tool discovery only. Run it only when you
have authorized account access and understand the environment:

```bash
RUN_LIVE_ROBINHOOD=1 uv run --locked pytest -m live
```

## Submit a pull request

Explain the concrete problem, the resulting behavior, and how you verified the change. Link
related issues where available. Include user-facing changes in [CHANGELOG.md](CHANGELOG.md)
under `Unreleased`, and update the README or focused documentation when usage changes.

Never attach credential files, callback URLs, authorization URLs, bearer keys, account
identifiers, or unredacted trading data. If a bug concerns credential exposure or unauthorized
access, follow [SECURITY.md](SECURITY.md) before opening a public issue.

Contributions are provided under this repository's [MIT license](LICENSE). Maintainers review
changes as capacity allows; no response or release schedule is promised.
