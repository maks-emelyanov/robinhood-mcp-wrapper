from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx2
import pytest
from mcp import Client
from mcp.server import MCPServer
from mcp.shared.exceptions import MCPError
from mcp.types import ToolAnnotations
from mcp_types import (
    CONNECTION_CLOSED,
    REQUEST_TIMEOUT,
    Completion,
    ListToolsResult,
    PromptReference,
    ResourceTemplateReference,
)

from robinhood_mcp.client import RobinhoodMCPClient
from robinhood_mcp.config import Settings
from robinhood_mcp.errors import (
    AuthenticationRequired,
    ToolValidationError,
    UpstreamMCPError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)


def build_server() -> MCPServer:
    server = MCPServer("Wrapper test server")

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def echo(text: str, count: int = 1) -> dict[str, object]:
        """Echo text a fixed number of times."""

        return {"text": text, "count": count, "rendered": text * count}

    @server.tool()
    def broken() -> str:
        """Always fail as a normal MCP tool error."""

        raise ValueError("not exposed to the client")

    @server.resource("demo://static")
    def static_resource() -> str:
        return "static contents"

    @server.resource("demo://items/{name}")
    def item_resource(name: str) -> str:
        return f"item:{name}"

    @server.prompt()
    def greet(name: str) -> str:
        return f"Hello, {name}!"

    @server.completion()
    async def complete_reference(ref: object, argument: object, context: object) -> Completion:
        del ref, context
        return Completion(values=[f"{argument.value}-one", f"{argument.value}-two"])

    return server


def test_robinhood_transport_skips_unsupported_session_delete(
    memory_storage: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = RobinhoodMCPClient(Settings(), storage=memory_storage)
    sentinel = object()
    captured: dict[str, object] = {}

    def fake_streamable_http_client(url: str, **kwargs: object) -> object:
        captured["url"] = url
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(
        "robinhood_mcp.client.streamable_http_client",
        fake_streamable_http_client,
    )
    http_client = object()

    assert wrapper._transport(http_client) is sentinel
    assert captured == {
        "url": wrapper.settings.mcp_url,
        "http_client": http_client,
        "terminate_on_close": False,
    }


@pytest.mark.anyio
async def test_wrapper_exposes_all_mcp_capabilities(memory_storage: object) -> None:
    wrapper = RobinhoodMCPClient(Settings(), storage=memory_storage)
    server = build_server()
    async with Client(server) as upstream:
        wrapper._client = upstream

        tools = await wrapper.list_tools()
        assert {tool.name for tool in tools.tools} == {"echo", "broken"}
        result = await wrapper.call_tool("echo", {"text": "ha", "count": 2})
        assert result.is_error is False
        assert result.structured_content == {"text": "ha", "count": 2, "rendered": "haha"}

        failed = await wrapper.call_tool("broken", {})
        assert failed.is_error is True

        resources = await wrapper.list_resources()
        assert str(resources.resources[0].uri) == "demo://static"
        templates = await wrapper.list_resource_templates()
        assert "{name}" in str(templates.resource_templates[0].uri_template)
        read = await wrapper.read_resource("demo://items/widget")
        assert read.contents[0].text == "item:widget"

        prompts = await wrapper.list_prompts()
        assert prompts.prompts[0].name == "greet"
        rendered = await wrapper.get_prompt("greet", {"name": "Ada"})
        assert rendered.messages[0].content.text == "Hello, Ada!"

        completion = await wrapper.complete(
            PromptReference(name="greet"),
            {"name": "name", "value": "ad"},
        )
        assert completion.completion.values == ["ad-one", "ad-two"]
        resource_completion = await wrapper.complete(
            ResourceTemplateReference(uri="demo://items/{name}"),
            {"name": "name", "value": "wi"},
        )
        assert resource_completion.completion.values[0] == "wi-one"

        metadata = await wrapper.server_metadata()
        assert metadata["server_info"]["name"] == "Wrapper test server"

        wrapper._client = None


@pytest.mark.anyio
async def test_tool_arguments_are_validated_before_call(memory_storage: object) -> None:
    wrapper = RobinhoodMCPClient(Settings(), storage=memory_storage)
    server = build_server()
    async with Client(server) as upstream:
        wrapper._client = upstream
        with pytest.raises(ToolValidationError) as captured:
            await wrapper.call_tool("echo", {"text": "hi", "count": "two"})
        assert captured.value.details[0]["path"] == ["count"]
        with pytest.raises(ToolValidationError, match="Unknown"):
            await wrapper.call_tool("not-a-tool", {})
        wrapper._client = None


@pytest.mark.anyio
async def test_logout_retains_registration(memory_storage: object) -> None:
    memory_storage.client_info = object()
    wrapper = RobinhoodMCPClient(Settings(), storage=memory_storage)
    await wrapper.logout()
    assert memory_storage.tokens is None
    assert memory_storage.client_info is not None
    await wrapper.reset_credentials()
    assert memory_storage.client_info is None


@pytest.mark.anyio
async def test_connection_can_be_closed_by_a_different_request_task(
    memory_storage: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrapper = RobinhoodMCPClient(Settings(), storage=memory_storage)
    server = build_server()
    entered: list[asyncio.Task[object]] = []
    exited: list[asyncio.Task[object]] = []

    @asynccontextmanager
    async def fake_http_client(provider: object):
        del provider
        entered.append(asyncio.current_task())
        try:
            yield object()
        finally:
            exited.append(asyncio.current_task())

    monkeypatch.setattr(wrapper, "_http_client", fake_http_client)
    monkeypatch.setattr(wrapper, "_transport", lambda _: server)

    await asyncio.wait_for(asyncio.create_task(wrapper.connect()), timeout=5)
    upstream = wrapper._client
    assert wrapper.connected
    assert (await wrapper.call_tool("echo", {"text": "ready"})).is_error is False
    await asyncio.wait_for(asyncio.create_task(wrapper.close()), timeout=5)

    assert not wrapper.connected
    assert entered == exited
    assert upstream._session is None
    assert wrapper._connection_task is None


@pytest.mark.anyio
async def test_cancelled_startup_does_not_leak_connection_owner(
    memory_storage: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrapper = RobinhoodMCPClient(Settings(), storage=memory_storage)
    starting = asyncio.Event()
    cleaned_up = asyncio.Event()

    @asynccontextmanager
    async def delayed_http_client(provider: object):
        del provider
        try:
            starting.set()
            await asyncio.Event().wait()
            yield object()
        finally:
            cleaned_up.set()

    monkeypatch.setattr(wrapper, "_http_client", delayed_http_client)
    connecting = asyncio.create_task(wrapper.connect())
    await asyncio.wait_for(starting.wait(), timeout=5)
    connecting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(connecting, timeout=5)
    assert cleaned_up.is_set()
    assert wrapper._connection_task is None
    assert not wrapper.connected


@pytest.mark.anyio
async def test_startup_failure_is_mapped_and_cleaned_up(
    memory_storage: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrapper = RobinhoodMCPClient(Settings(), storage=memory_storage)

    @asynccontextmanager
    async def failing_http_client(provider: object):
        del provider
        raise httpx2.ConnectError("local failure")
        yield  # pragma: no cover - makes this an async context manager

    monkeypatch.setattr(wrapper, "_http_client", failing_http_client)
    with pytest.raises(UpstreamUnavailableError):
        await wrapper.connect()
    assert wrapper._connection_task is None
    assert not wrapper.connected


@pytest.mark.anyio
async def test_cancelled_close_finishes_owner_cleanup(
    memory_storage: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrapper = RobinhoodMCPClient(Settings(), storage=memory_storage)
    closing = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleaned_up = asyncio.Event()

    @asynccontextmanager
    async def delayed_http_cleanup(provider: object):
        del provider
        try:
            yield object()
        finally:
            closing.set()
            await release_cleanup.wait()
            cleaned_up.set()

    monkeypatch.setattr(wrapper, "_http_client", delayed_http_cleanup)
    monkeypatch.setattr(wrapper, "_transport", lambda _: build_server())
    await wrapper.connect()
    closing_task = asyncio.create_task(wrapper.close())
    await asyncio.wait_for(closing.wait(), timeout=5)
    closing_task.cancel()
    await asyncio.sleep(0)
    assert not closing_task.done()
    release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(closing_task, timeout=5)
    assert cleaned_up.is_set()
    assert wrapper._connection_task is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "expected", "invalidated"),
    [
        (httpx2.ReadTimeout("timeout"), UpstreamTimeoutError, True),
        (MCPError(REQUEST_TIMEOUT, "timeout"), UpstreamTimeoutError, True),
        (MCPError(CONNECTION_CLOSED, "closed"), UpstreamUnavailableError, True),
        (MCPError(-32602, "invalid request"), UpstreamMCPError, False),
    ],
)
async def test_transport_failures_are_mapped_and_never_retried(
    memory_storage: object,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected: type[Exception],
    invalidated: bool,
) -> None:
    wrapper = RobinhoodMCPClient(Settings(), storage=memory_storage)
    attempts: list[str] = []

    async def call_tool(name: str, arguments: object) -> None:
        del arguments
        attempts.append(name)
        raise error

    async def valid_arguments(name: str, arguments: object) -> None:
        del name, arguments

    monkeypatch.setattr(wrapper, "_validate_tool_arguments", valid_arguments)
    upstream = SimpleNamespace(call_tool=call_tool)
    wrapper._client = upstream
    with pytest.raises(expected):
        await wrapper.call_tool("place_order", {"quantity": 1})
    assert attempts == ["place_order"]
    assert wrapper.connected is not invalidated


@pytest.mark.anyio
async def test_connect_without_credentials_does_not_open_transport(memory_storage: object) -> None:
    memory_storage.tokens = None
    wrapper = RobinhoodMCPClient(Settings(), storage=memory_storage)
    with pytest.raises(AuthenticationRequired):
        await wrapper.connect()
    assert not wrapper.connected
    assert wrapper._connection_task is None


@pytest.mark.anyio
async def test_repeated_tool_cursor_is_rejected(
    memory_storage: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrapper = RobinhoodMCPClient(Settings(), storage=memory_storage)

    async def repeated_page(**kwargs: object) -> ListToolsResult:
        del kwargs
        return ListToolsResult(tools=[], nextCursor="same")

    monkeypatch.setattr(wrapper, "list_tools", repeated_page)
    with pytest.raises(UpstreamMCPError, match="pagination"):
        await wrapper.list_all_tools()
