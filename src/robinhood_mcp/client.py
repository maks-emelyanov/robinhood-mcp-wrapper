"""Typed asynchronous client for Robinhood's Agentic Trading MCP server."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
import webbrowser
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

import anyio
import httpx2
from jsonschema import SchemaError, validators
from mcp import Client
from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider, OAuthFlowError
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientMetadata
from mcp.shared.exceptions import MCPError
from mcp_types import (
    CallToolResult,
    CompleteResult,
    GetPromptResult,
    Implementation,
    ListPromptsResult,
    ListResourcesResult,
    ListResourceTemplatesResult,
    ListToolsResult,
    PromptReference,
    ReadResourceResult,
    ResourceTemplateReference,
)
from pydantic import AnyUrl

from robinhood_mcp import __version__
from robinhood_mcp.auth import (
    AuthorizationFlow,
    LoopbackCallbackReceiver,
    parse_oauth_callback,
)
from robinhood_mcp.config import Settings
from robinhood_mcp.errors import (
    AuthenticationRequired,
    AuthFlowConflict,
    InvalidOAuthCallback,
    ToolValidationError,
    UpstreamMCPError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from robinhood_mcp.serialization import to_jsonable
from robinhood_mcp.storage import CredentialStorage, FileTokenStorage

logger = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass(slots=True)
class _ActiveAuthFlow:
    flow_id: str
    expires_at: datetime
    url_future: asyncio.Future[str]
    callback_future: asyncio.Future[AuthorizationCodeResult]
    task: asyncio.Task[None]


class RobinhoodMCPClient:
    """Reusable async wrapper around the official MCP client and OAuth provider."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        storage: CredentialStorage | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.settings.validate()
        self.storage = storage or FileTokenStorage(self.settings)
        self._connection_lock = asyncio.Lock()
        self._auth_lock = asyncio.Lock()
        self._stack: AsyncExitStack | None = None
        self._client: Client | None = None
        self._active_auth: _ActiveAuthFlow | None = None

    @property
    def connected(self) -> bool:
        return self._client is not None

    async def __aenter__(self) -> RobinhoodMCPClient:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    def _metadata(self) -> OAuthClientMetadata:
        return OAuthClientMetadata(
            client_name=self.settings.client_name,
            software_version=__version__,
            redirect_uris=[AnyUrl(self.settings.redirect_uri)],
            scope=self.settings.scope,
            token_endpoint_auth_method="none",
        )

    def _http_client(self, provider: OAuthClientProvider) -> httpx2.AsyncClient:
        timeout = httpx2.Timeout(
            self.settings.connect_timeout,
            read=self.settings.read_timeout,
        )
        return httpx2.AsyncClient(auth=provider, timeout=timeout)

    def _transport(self, http_client: httpx2.AsyncClient) -> Any:
        # Robinhood currently rejects the SDK's optional session-termination
        # DELETE even when Mcp-Session-Id is present. The transport context still
        # closes its local streams and tasks when remote termination is disabled.
        return streamable_http_client(
            self.settings.mcp_url,
            http_client=http_client,
            terminate_on_close=False,
        )

    async def start_login(self, *, force: bool = False) -> AuthorizationFlow:
        """Start a dynamic-registration OAuth flow and return its authorization URL."""

        async with self._auth_lock:
            if self._active_auth is not None and not self._active_auth.task.done():
                raise AuthFlowConflict("An authorization flow is already in progress")
            self._active_auth = None
            if await self.storage.get_tokens() is not None:
                if not force:
                    raise AuthFlowConflict("Credentials already exist; use force to reauthorize")
                await self.close()
                await self.storage.clear_tokens()

            loop = asyncio.get_running_loop()
            url_future: asyncio.Future[str] = loop.create_future()
            callback_future: asyncio.Future[AuthorizationCodeResult] = loop.create_future()
            flow_id = uuid.uuid4().hex
            expires_at = datetime.now(UTC) + timedelta(seconds=self.settings.oauth_timeout)
            task = asyncio.create_task(
                self._run_login(url_future, callback_future),
                name=f"robinhood-oauth-{flow_id}",
            )
            task.add_done_callback(self._consume_auth_task_result)
            active = _ActiveAuthFlow(
                flow_id=flow_id,
                expires_at=expires_at,
                url_future=url_future,
                callback_future=callback_future,
                task=task,
            )
            self._active_auth = active

        done, _ = await asyncio.wait((url_future, task), return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            try:
                await task
            except OAuthFlowError as exc:
                raise UpstreamMCPError("OAuth authorization failed") from exc
            raise UpstreamMCPError("OAuth completed without an authorization redirect")
        return AuthorizationFlow(
            flow_id=flow_id,
            authorization_url=url_future.result(),
            redirect_uri=self.settings.redirect_uri,
            expires_at=expires_at,
            _complete=self.complete_login,
        )

    @staticmethod
    def _consume_auth_task_result(task: asyncio.Task[None]) -> None:
        """Retrieve background failures even if an abandoned flow expires."""

        if task.cancelled():
            return
        task.exception()

    async def _run_login(
        self,
        url_future: asyncio.Future[str],
        callback_future: asyncio.Future[AuthorizationCodeResult],
    ) -> None:
        async def redirect_handler(url: str) -> None:
            if not url_future.done():
                url_future.set_result(url)

        async def callback_handler() -> AuthorizationCodeResult:
            return await callback_future

        provider = OAuthClientProvider(
            server_url=self.settings.mcp_url,
            client_metadata=self._metadata(),
            storage=self.storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
        )
        try:
            async with asyncio.timeout(self.settings.oauth_timeout):
                async with self._http_client(provider) as http_client:
                    transport = self._transport(http_client)
                    async with Client(
                        transport,
                        mode="auto",
                        read_timeout_seconds=self.settings.read_timeout,
                        client_info=Implementation(
                            name="robinhood-mcp-wrapper",
                            version=__version__,
                        ),
                    ) as client:
                        await client.list_tools(cache_mode="bypass")
        except TimeoutError as exc:
            raise UpstreamTimeoutError("OAuth authorization timed out") from exc
        except httpx2.TimeoutException as exc:
            raise UpstreamTimeoutError("Robinhood OAuth request timed out") from exc
        except httpx2.HTTPError as exc:
            raise UpstreamUnavailableError("Robinhood OAuth endpoint is unavailable") from exc

    async def complete_login(self, flow_id: str, callback_url: str) -> None:
        async with self._auth_lock:
            active = self._active_auth
            if active is None or active.task.done():
                self._active_auth = None
                raise AuthFlowConflict("No authorization flow is awaiting a callback")
            if active.flow_id != flow_id:
                raise InvalidOAuthCallback("Callback flow ID does not match the active flow")
            result = parse_oauth_callback(callback_url, self.settings.redirect_uri)
            if active.callback_future.done():
                raise AuthFlowConflict("The authorization callback was already submitted")
            active.callback_future.set_result(result)

        try:
            await active.task
        except OAuthFlowError as exc:
            raise InvalidOAuthCallback("Robinhood rejected the OAuth callback") from exc
        finally:
            async with self._auth_lock:
                if self._active_auth is active:
                    self._active_auth = None

    async def login_browser(self, *, force: bool = False) -> None:
        """Authorize through the configured HTTP loopback callback."""

        try:
            async with LoopbackCallbackReceiver(self.settings.redirect_uri) as receiver:
                flow = await self.start_login(force=force)
                opened = await anyio.to_thread.run_sync(webbrowser.open, flow.authorization_url)
                if not opened:
                    raise InvalidOAuthCallback(
                        "No browser could be opened; use the manual authorization flow"
                    )
                callback_url = await receiver.wait(self.settings.oauth_timeout)
                await flow.complete(callback_url)
        except BaseException:
            if self._active_auth is not None:
                await self.cancel_login()
            raise

    async def cancel_login(self) -> None:
        async with self._auth_lock:
            active = self._active_auth
            self._active_auth = None
        if active is not None and not active.task.done():
            active.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await active.task

    async def auth_status(self) -> dict[str, Any]:
        tokens = await self.storage.get_tokens()
        client_info = await self.storage.get_client_info()
        active = self._active_auth
        return {
            "authenticated": tokens is not None,
            "registered": client_info is not None,
            "connected": self.connected,
            "flow_in_progress": active is not None and not active.task.done(),
            "flow_id": active.flow_id if active is not None and not active.task.done() else None,
            "flow_expires_at": (
                active.expires_at.isoformat()
                if active is not None and not active.task.done()
                else None
            ),
        }

    async def logout(self) -> None:
        await self.cancel_login()
        await self.close()
        await self.storage.clear_tokens()

    async def reset_credentials(self) -> None:
        await self.logout()
        await self.storage.clear_client_info()

    async def connect(self) -> None:
        """Connect with stored credentials; never starts an interactive flow."""

        if self._client is not None:
            return
        async with self._connection_lock:
            if self._client is not None:
                return
            if await self.storage.get_tokens() is None:
                raise AuthenticationRequired("Run an OAuth login before connecting")

            async def redirect_handler(_: str) -> None:
                raise AuthenticationRequired("Stored credentials require reauthorization")

            async def callback_handler() -> AuthorizationCodeResult:
                raise AuthenticationRequired("Stored credentials require reauthorization")

            provider = OAuthClientProvider(
                server_url=self.settings.mcp_url,
                client_metadata=self._metadata(),
                storage=self.storage,
                redirect_handler=redirect_handler,
                callback_handler=callback_handler,
            )
            stack = AsyncExitStack()
            try:
                http_client = await stack.enter_async_context(self._http_client(provider))
                transport = self._transport(http_client)
                client = await stack.enter_async_context(
                    Client(
                        transport,
                        mode="auto",
                        read_timeout_seconds=self.settings.read_timeout,
                        client_info=Implementation(
                            name="robinhood-mcp-wrapper",
                            version=__version__,
                        ),
                    )
                )
            except AuthenticationRequired:
                await stack.aclose()
                raise
            except OAuthFlowError as exc:
                await stack.aclose()
                raise AuthenticationRequired("Stored credentials could not be refreshed") from exc
            except httpx2.TimeoutException as exc:
                await stack.aclose()
                raise UpstreamTimeoutError("Timed out connecting to Robinhood MCP") from exc
            except (httpx2.HTTPError, OSError) as exc:
                await stack.aclose()
                raise UpstreamUnavailableError("Unable to connect to Robinhood MCP") from exc
            self._stack = stack
            self._client = client

    async def close(self) -> None:
        async with self._connection_lock:
            stack = self._stack
            self._stack = None
            self._client = None
            if stack is not None:
                with contextlib.suppress(Exception):
                    await stack.aclose()

    async def _invalidate_connection(self) -> None:
        await self.close()

    async def _invoke(self, operation: Callable[[Client], Awaitable[T]]) -> T:
        await self.connect()
        client = self._client
        if client is None:  # pragma: no cover - narrowed by connect()
            raise UpstreamUnavailableError("MCP session is not connected")
        try:
            return await operation(client)
        except AuthenticationRequired:
            await self._invalidate_connection()
            raise
        except httpx2.TimeoutException as exc:
            await self._invalidate_connection()
            raise UpstreamTimeoutError("Robinhood MCP request timed out") from exc
        except MCPError as exc:
            raise UpstreamMCPError(
                "Robinhood MCP returned a protocol error",
                details=to_jsonable(getattr(exc, "error", None)),
            ) from exc
        except (httpx2.HTTPError, OSError, anyio.BrokenResourceError, anyio.EndOfStream) as exc:
            await self._invalidate_connection()
            raise UpstreamUnavailableError("Robinhood MCP connection failed") from exc

    async def server_metadata(self) -> dict[str, Any]:
        async def operation(client: Client) -> dict[str, Any]:
            return {
                "server_info": to_jsonable(client.server_info),
                "capabilities": to_jsonable(client.server_capabilities),
                "protocol_version": client.protocol_version,
                "instructions": client.instructions,
            }

        return await self._invoke(operation)

    async def list_tools(
        self, *, cursor: str | None = None, refresh: bool = False
    ) -> ListToolsResult:
        return await self._invoke(
            lambda client: client.list_tools(
                cursor=cursor,
                cache_mode="bypass" if refresh else "use",
            )
        )

    async def list_all_tools(self, *, refresh: bool = False) -> list[Any]:
        tools: list[Any] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            result = await self.list_tools(cursor=cursor, refresh=refresh)
            tools.extend(result.tools)
            cursor = result.next_cursor
            if cursor is None:
                return tools
            if cursor in seen:
                raise UpstreamMCPError("Robinhood MCP repeated a pagination cursor")
            seen.add(cursor)

    async def refresh_tools(self) -> list[Any]:
        return await self.list_all_tools(refresh=True)

    async def _validate_tool_arguments(self, name: str, arguments: dict[str, Any]) -> None:
        tools = await self.list_all_tools()
        tool = next((candidate for candidate in tools if candidate.name == name), None)
        if tool is None:
            tools = await self.list_all_tools(refresh=True)
            tool = next((candidate for candidate in tools if candidate.name == name), None)
        if tool is None:
            raise ToolValidationError(f"Unknown Robinhood MCP tool: {name}")
        schema = tool.input_schema
        try:
            validator_type = validators.validator_for(schema)
            validator_type.check_schema(schema)
            validator = validator_type(schema, format_checker=validator_type.FORMAT_CHECKER)
        except SchemaError as exc:
            raise UpstreamMCPError(f"Tool {name} published an invalid input schema") from exc
        errors = sorted(validator.iter_errors(arguments), key=lambda error: list(error.path))
        if errors:
            details = [
                {
                    "path": list(error.absolute_path),
                    "message": error.message,
                    "validator": error.validator,
                }
                for error in errors
            ]
            raise ToolValidationError(
                f"Arguments do not match the schema for tool {name}",
                details=details,
            )

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
        payload = arguments or {}
        await self._validate_tool_arguments(name, payload)
        # Deliberately invoke exactly once. Retrying here could duplicate an order.
        return await self._invoke(lambda client: client.call_tool(name, payload))

    async def list_resources(
        self, *, cursor: str | None = None, refresh: bool = False
    ) -> ListResourcesResult:
        return await self._invoke(
            lambda client: client.list_resources(
                cursor=cursor,
                cache_mode="bypass" if refresh else "use",
            )
        )

    async def list_resource_templates(
        self, *, cursor: str | None = None, refresh: bool = False
    ) -> ListResourceTemplatesResult:
        return await self._invoke(
            lambda client: client.list_resource_templates(
                cursor=cursor,
                cache_mode="bypass" if refresh else "use",
            )
        )

    async def read_resource(self, uri: str, *, refresh: bool = False) -> ReadResourceResult:
        return await self._invoke(
            lambda client: client.read_resource(
                uri,
                cache_mode="bypass" if refresh else "use",
            )
        )

    async def list_prompts(
        self, *, cursor: str | None = None, refresh: bool = False
    ) -> ListPromptsResult:
        return await self._invoke(
            lambda client: client.list_prompts(
                cursor=cursor,
                cache_mode="bypass" if refresh else "use",
            )
        )

    async def get_prompt(
        self,
        name: str,
        arguments: dict[str, str] | None = None,
    ) -> GetPromptResult:
        return await self._invoke(lambda client: client.get_prompt(name, arguments))

    async def complete(
        self,
        ref: PromptReference | ResourceTemplateReference,
        argument: dict[str, str],
        context_arguments: dict[str, str] | None = None,
    ) -> CompleteResult:
        return await self._invoke(lambda client: client.complete(ref, argument, context_arguments))
