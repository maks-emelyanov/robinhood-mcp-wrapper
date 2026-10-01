# Changelog

User-visible changes are recorded here. Move reviewed entries from `Unreleased` into a dated
version section when a release is published.

## Unreleased

### Added

- Initial Python async client, Typer CLI, and FastAPI REST gateway for Robinhood Agentic Trading
  MCP, with dynamic tool, resource, prompt, and completion support.
- SDK-backed OAuth discovery, dynamic client registration, PKCE, refresh, browser login, and
  manual callback completion.
- Per-user JSON credential storage, schema validation, structured errors, and single-invocation
  tool calls.
- Offline tests, distribution validation, GitHub CI and contributor templates, and documentation
  for development, operations, security reporting, and releases.
- A Python discovery example that prints server metadata and tool schemas without calling tools.
- OpenAPI bearer authentication metadata for protected gateway routes when an API key is set.

### Changed

- Package metadata now declares the MIT license, Python 3.14 support, and the PEP 561 type marker;
  release archives include contributor documentation and verification resources.
- CLI configuration and JSON input errors now use concise messages, and `--version` reports the
  installed wrapper version.

### Fixed

- MCP connections now enter and exit SDK task groups in one owning task, allowing safe cleanup
  across gateway requests and shutdown.
- OAuth callback parsing rejects malformed, duplicate, and conflicting callback parameters.
- Credential reads validate the opened file descriptor and reject symbolic links and special
  files, including dangling links and named pipes.
- SDK transport timeout and connection failures use the corresponding wrapper errors and HTTP
  statuses; failed tool calls are still invoked only once.
- Incomplete completion arguments are rejected before reaching the upstream MCP server.
- Configuration rejects non-finite timeouts, unsupported log levels, and unsafe or malformed
  endpoint and redirect URLs; bearer-key comparison safely handles non-ASCII input.
- CLI gateway request logging omits OAuth callback query parameters by disabling Uvicorn's
  access log and retaining the wrapper's path-only request log.
