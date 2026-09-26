from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
from mcp_types import CallToolResult, ListToolsResult, TextContent, Tool

from robinhood_mcp.api import create_app
from robinhood_mcp.config import Settings
from robinhood_mcp.errors import ToolValidationError


@dataclass
class FakeClient:
    closed: bool = False
    invalid_arguments: bool = False

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
    paths = app.openapi()["paths"]
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
