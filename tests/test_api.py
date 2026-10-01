from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp.server import MCPServer
from mcp.shared.auth import OAuthToken
from mcp_types import CallToolResult, ListToolsResult, TextContent, Tool

from robinhood_mcp_wrapper.api import create_app
from robinhood_mcp_wrapper.client import RobinhoodMCPClient
from robinhood_mcp_wrapper.config import Settings
from robinhood_mcp_wrapper.errors import (
    AuthenticationRequired,
    AuthFlowConflict,
    CredentialStoreError,
    InvalidOAuthCallback,
    RobinhoodMCPError,
    ToolValidationError,
    UpstreamMCPError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)


@dataclass
class FakeClient:
    closed: bool = False
    invalid_arguments: bool = False
    error: RobinhoodMCPError | None = None

    async def close(self) -> None:
        self.closed = True

    async def cancel_login(self) -> None:
        return None

    async def auth_status(self) -> dict[str, Any]:
        return {
            "authenticated": True,
            "registered": True,
            "connected": True,
            "flow_in_progress": False,
            "flow_id": None,
            "flow_expires_at": None,
        }

    async def start_login(self, *, force: bool = False) -> Any:
        del force
        return SimpleNamespace(
            flow_id="flow",
            authorization_url="https://example.test/authorize",
            redirect_uri="http://127.0.0.1:8765/oauth/callback",
            expires_at=datetime.now(UTC),
        )

    async def complete_login(self, flow_id: str, callback_url: str) -> None:
        del flow_id, callback_url

    async def connect(self) -> None:
        return None

    async def logout(self) -> None:
        return None

    async def reset_credentials(self) -> None:
        return None

    async def server_metadata(self) -> dict[str, Any]:
        return {"protocol_version": "test", "capabilities": {"tools": {}}}

    async def list_tools(self, *, cursor: str | None, refresh: bool) -> ListToolsResult:
        del cursor, refresh
        return ListToolsResult(
            tools=[Tool(name="echo", description="Echo", inputSchema={"type": "object"})]
        )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        del name, arguments
        if self.error is not None:
            raise self.error
        if self.invalid_arguments:
            raise ToolValidationError("Bad arguments", details=[{"path": ["count"]}])
        return CallToolResult(
            content=[TextContent(text="upstream rejected it")],
            isError=True,
        )


def test_api_key_and_health_exemption() -> None:
    fake = FakeClient()
    app = create_app(Settings(api_key="secret"), client=fake)  # type: ignore[arg-type]
    with TestClient(app) as http:
        assert http.get("/healthz").status_code == 200
        assert http.get("/v1/auth/status").status_code == 401
        response = http.get(
            "/v1/auth/status",
            headers={"Authorization": "Bearer secret", "X-Request-ID": "request-1"},
        )
        assert response.status_code == 200
        assert response.headers["x-request-id"] == "request-1"


def test_tool_error_result_remains_http_200() -> None:
    fake = FakeClient()
    app = create_app(Settings(), client=fake)  # type: ignore[arg-type]
    with TestClient(app) as http:
        response = http.post("/v1/tools/echo/call", json={"arguments": {}})
        assert response.status_code == 200
        assert response.json()["isError"] is True


def test_local_validation_error_maps_to_422() -> None:
    fake = FakeClient(invalid_arguments=True)
    app = create_app(Settings(), client=fake)  # type: ignore[arg-type]
    with TestClient(app) as http:
        response = http.post("/v1/tools/echo/call", json={"arguments": {}})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "tool_validation_error"


def test_openapi_contains_stable_gateway_routes() -> None:
    app = create_app(Settings(), client=FakeClient())  # type: ignore[arg-type]
    schema = app.openapi()
    assert schema["info"]["title"] == "Robinhood MCP Wrapper"
    paths = schema["paths"]
    expected = {
        "/healthz",
        "/v1/auth/status",
        "/v1/auth/start",
        "/v1/auth/complete",
        "/v1/auth/session",
        "/oauth/callback",
        "/v1/mcp",
        "/v1/tools",
        "/v1/tools/{name}/call",
        "/v1/resources",
        "/v1/resource-templates",
        "/v1/resources/read",
        "/v1/prompts",
        "/v1/prompts/{name}/get",
        "/v1/completions",
    }
    assert expected <= set(paths)


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (AuthenticationRequired("login required"), 401),
        (AuthFlowConflict("already authorizing"), 409),
        (InvalidOAuthCallback("invalid callback"), 409),
        (CredentialStoreError("unreadable credentials"), 503),
        (UpstreamTimeoutError("timeout"), 504),
        (UpstreamUnavailableError("offline"), 502),
        (UpstreamMCPError("invalid protocol", details={"code": -32600}), 502),
    ],
)
def test_wrapper_errors_have_stable_http_responses(error: RobinhoodMCPError, status: int) -> None:
    app = create_app(Settings(), client=FakeClient(error=error))  # type: ignore[arg-type]
    with TestClient(app) as http:
        response = http.post("/v1/tools/echo/call", json={"arguments": {}})
        assert response.status_code == status
        assert response.json()["error"]["code"] == error.code
        assert response.json()["error"]["message"] == error.message
        assert response.headers["x-request-id"]


@pytest.mark.parametrize(
    "reference",
    [{"type": "unknown"}, {"type": "ref/prompt"}, {"type": "ref/resource"}],
)
def test_invalid_completion_reference_is_rejected(reference: dict[str, str]) -> None:
    app = create_app(Settings(), client=FakeClient())  # type: ignore[arg-type]
    with TestClient(app) as http:
        response = http.post(
            "/v1/completions",
            json={"ref": reference, "argument": {"name": "name", "value": "a"}},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "tool_validation_error"


def test_invalid_non_ascii_bearer_is_rejected_without_server_error() -> None:
    app = create_app(Settings(api_key="secret"), client=FakeClient())  # type: ignore[arg-type]
    with TestClient(app) as http:
        response = http.get("/v1/auth/status", headers={"authorization": b"Bearer \xff"})
        assert response.status_code == 401


def test_api_closes_reusable_mcp_session_on_logout_and_shutdown(
    memory_storage: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrapper = RobinhoodMCPClient(Settings(), storage=memory_storage)
    server = MCPServer("API lifecycle test")

    @server.tool()
    def echo(text: str) -> str:
        return text

    exits: list[bool] = []

    @asynccontextmanager
    async def fake_http_client(provider: object):
        del provider
        try:
            yield object()
        finally:
            exits.append(True)

    monkeypatch.setattr(wrapper, "_http_client", fake_http_client)
    monkeypatch.setattr(wrapper, "_transport", lambda _: server)
    app = create_app(Settings(), client=wrapper)
    with TestClient(app) as http:
        assert http.get("/v1/tools").status_code == 200
        upstream = wrapper._client
        assert http.delete("/v1/auth/session").status_code == 200
        assert upstream._session is None
        assert exits == [True]

        memory_storage.tokens = OAuthToken(access_token="test-token")
        assert http.get("/v1/tools").status_code == 200
        upstream = wrapper._client
    assert upstream._session is None
    assert exits == [True, True]
    assert not wrapper.connected


def test_openapi_describes_bearer_security_only_for_protected_routes() -> None:
    app = create_app(Settings(api_key="secret"), client=FakeClient())  # type: ignore[arg-type]
    schema = app.openapi()
    assert schema["components"]["securitySchemes"]["HTTPBearer"] == {
        "type": "http",
        "scheme": "bearer",
    }
    assert schema["paths"]["/v1/tools"]["get"]["security"] == [{"HTTPBearer": []}]
    assert "security" not in schema["paths"]["/healthz"]["get"]
    assert "security" not in schema["paths"]["/oauth/callback"]["get"]


@pytest.mark.parametrize("argument", [{}, {"name": "name"}, {"value": "a"}])
def test_incomplete_completion_argument_is_rejected(argument: dict[str, str]) -> None:
    app = create_app(Settings(), client=FakeClient())  # type: ignore[arg-type]
    with TestClient(app) as http:
        response = http.post(
            "/v1/completions",
            json={"ref": {"type": "ref/prompt", "name": "greet"}, "argument": argument},
        )
        assert response.status_code == 422
