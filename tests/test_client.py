from __future__ import annotations

import pytest
from mcp import Client
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from mcp_types import Completion, PromptReference, ResourceTemplateReference

from robinhood_mcp.client import RobinhoodMCPClient
from robinhood_mcp.config import Settings
from robinhood_mcp.errors import ToolValidationError


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
