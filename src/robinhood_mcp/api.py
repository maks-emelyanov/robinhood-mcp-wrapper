"""FastAPI gateway for the Robinhood MCP client."""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from mcp_types import PromptReference, ResourceTemplateReference
from pydantic import BaseModel, ConfigDict, Field

from robinhood_mcp import __version__
from robinhood_mcp.client import RobinhoodMCPClient
from robinhood_mcp.config import Settings
from robinhood_mcp.errors import (
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
from robinhood_mcp.serialization import to_jsonable

logger = logging.getLogger(__name__)


class AuthStartRequest(BaseModel):
    force: bool = False


class AuthCompleteRequest(BaseModel):
    flow_id: str
    callback_url: str


class ToolCallRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


class ResourceReadRequest(BaseModel):
    uri: str
    refresh: bool = False


class PromptGetRequest(BaseModel):
    arguments: dict[str, str] | None = None


class CompletionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ref: dict[str, Any]
    argument: dict[str, str]
    context_arguments: dict[str, str] | None = Field(default=None, alias="contextArguments")

    def parsed_ref(self) -> PromptReference | ResourceTemplateReference:
        if self.ref.get("type") == "ref/prompt":
            return PromptReference.model_validate(self.ref)
        if self.ref.get("type") == "ref/resource":
            return ResourceTemplateReference.model_validate(self.ref)
        raise ValueError("ref.type must be 'ref/prompt' or 'ref/resource'")


def _status_for(exc: RobinhoodMCPError) -> int:
    if isinstance(exc, AuthenticationRequired):
        return 401
    if isinstance(exc, (AuthFlowConflict, InvalidOAuthCallback)):
        return 409
    if isinstance(exc, ToolValidationError):
        return 422
    if isinstance(exc, UpstreamTimeoutError):
        return 504
    if isinstance(exc, (UpstreamMCPError, UpstreamUnavailableError)):
        return 502
    if isinstance(exc, CredentialStoreError):
        return 503
    return 500


def _error_body(exc: RobinhoodMCPError) -> dict[str, Any]:
    error: dict[str, Any] = {"code": exc.code, "message": exc.message}
    if exc.details is not None:
        error["details"] = to_jsonable(exc.details)
    return {"error": error}


def create_app(
    settings: Settings | None = None,
    *,
    client: RobinhoodMCPClient | None = None,
) -> FastAPI:
    """Create an API application, optionally with an injected client for testing."""

    resolved_settings = settings or Settings.from_env()
    resolved_settings.validate()
    resolved_settings.validate_server_binding()
    shared_client = client or RobinhoodMCPClient(resolved_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await shared_client.cancel_login()
        await shared_client.close()

    app = FastAPI(
        title="Robinhood Agentic Trading MCP Wrapper",
        version=__version__,
        description=(
            "A generic typed gateway to Robinhood's Agentic Trading MCP server. "
            "Tool calls may place real trades."
        ),
        lifespan=lifespan,
    )
    app.state.robinhood_client = shared_client
    app.state.settings = resolved_settings

    @app.middleware("http")
    async def access_control_and_logging(request: Request, call_next: Any) -> Any:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        started = time.monotonic()
        exempt = request.url.path in {"/healthz", "/oauth/callback"}
        if resolved_settings.api_key and not exempt:
            header = request.headers.get("authorization", "")
            scheme, _, supplied = header.partition(" ")
            valid = scheme.lower() == "bearer" and secrets.compare_digest(
                supplied,
                resolved_settings.api_key,
            )
            if not valid:
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": {
                            "code": "invalid_api_key",
                            "message": "A valid wrapper bearer API key is required",
                        }
                    },
                    headers={"x-request-id": request_id},
                )
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        logger.info(
            "%s %s -> %s in %.1fms request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            (time.monotonic() - started) * 1000,
            request_id,
        )
        return response

    @app.exception_handler(RobinhoodMCPError)
    async def wrapper_error_handler(_: Request, exc: RobinhoodMCPError) -> JSONResponse:
        return JSONResponse(status_code=_status_for(exc), content=_error_body(exc))

    @app.get("/healthz", tags=["service"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/auth/status", tags=["authentication"])
    async def auth_status() -> dict[str, Any]:
        return await shared_client.auth_status()

    @app.post("/v1/auth/start", tags=["authentication"])
    async def auth_start(body: AuthStartRequest) -> dict[str, Any]:
        flow = await shared_client.start_login(force=body.force)
        return {
            "flow_id": flow.flow_id,
            "authorization_url": flow.authorization_url,
            "redirect_uri": flow.redirect_uri,
            "expires_at": flow.expires_at.isoformat(),
        }

    @app.post("/v1/auth/complete", tags=["authentication"])
    async def auth_complete(body: AuthCompleteRequest) -> dict[str, Any]:
        await shared_client.complete_login(body.flow_id, body.callback_url)
        await shared_client.connect()
        return await shared_client.auth_status()

    @app.get("/oauth/callback", response_class=HTMLResponse, tags=["authentication"])
    async def oauth_callback(request: Request) -> HTMLResponse:
        status = await shared_client.auth_status()
        flow_id = status.get("flow_id")
        if not flow_id:
            return HTMLResponse("No authorization flow is active.", status_code=409)
        try:
            await shared_client.complete_login(flow_id, str(request.url))
            await shared_client.connect()
        except RobinhoodMCPError:
            logger.warning("OAuth callback was rejected")
            return HTMLResponse("Authorization could not be completed.", status_code=400)
        return HTMLResponse("Authorization complete. You can close this window.")

    @app.delete("/v1/auth/session", tags=["authentication"])
    async def auth_delete(
        forget_client: Annotated[bool, Query(alias="forgetClient")] = False,
    ) -> dict[str, Any]:
        if forget_client:
            await shared_client.reset_credentials()
        else:
            await shared_client.logout()
        return await shared_client.auth_status()

    @app.get("/v1/mcp", tags=["mcp"])
    async def mcp_metadata() -> dict[str, Any]:
        return await shared_client.server_metadata()

    @app.get("/v1/tools", tags=["tools"])
    async def list_tools(
        cursor: str | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        return to_jsonable(await shared_client.list_tools(cursor=cursor, refresh=refresh))

    @app.post("/v1/tools/{name}/call", tags=["tools"])
    async def call_tool(name: str, body: ToolCallRequest) -> dict[str, Any]:
        return to_jsonable(await shared_client.call_tool(name, body.arguments))

    @app.get("/v1/resources", tags=["resources"])
    async def list_resources(
        cursor: str | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        return to_jsonable(await shared_client.list_resources(cursor=cursor, refresh=refresh))

    @app.get("/v1/resource-templates", tags=["resources"])
    async def list_resource_templates(
        cursor: str | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        return to_jsonable(
            await shared_client.list_resource_templates(cursor=cursor, refresh=refresh)
        )

    @app.post("/v1/resources/read", tags=["resources"])
    async def read_resource(body: ResourceReadRequest) -> dict[str, Any]:
        return to_jsonable(await shared_client.read_resource(body.uri, refresh=body.refresh))

    @app.get("/v1/prompts", tags=["prompts"])
    async def list_prompts(
        cursor: str | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        return to_jsonable(await shared_client.list_prompts(cursor=cursor, refresh=refresh))

    @app.post("/v1/prompts/{name}/get", tags=["prompts"])
    async def get_prompt(name: str, body: PromptGetRequest) -> dict[str, Any]:
        return to_jsonable(await shared_client.get_prompt(name, body.arguments))

    @app.post("/v1/completions", tags=["prompts"])
    async def complete(body: CompletionRequest) -> dict[str, Any]:
        try:
            ref = body.parsed_ref()
        except ValueError as exc:
            raise ToolValidationError(str(exc)) from exc
        return to_jsonable(await shared_client.complete(ref, body.argument, body.context_arguments))

    return app
