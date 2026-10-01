from __future__ import annotations

import asyncio

import pytest
from mcp.client.auth import AuthorizationCodeResult
from mcp.shared.auth import OAuthToken

from robinhood_mcp_wrapper.auth import LoopbackCallbackReceiver, parse_oauth_callback
from robinhood_mcp_wrapper.client import RobinhoodMCPClient
from robinhood_mcp_wrapper.config import Settings
from robinhood_mcp_wrapper.errors import AuthFlowConflict, ConfigurationError, InvalidOAuthCallback

REDIRECT = "http://127.0.0.1:8765/oauth/callback"


def test_parse_callback_preserves_state_and_issuer() -> None:
    result = parse_oauth_callback(
        f"{REDIRECT}?code=abc&state=state-value&iss=https%3A%2F%2Fissuer.example",
        REDIRECT,
    )
    assert result.code == "abc"
    assert result.state == "state-value"
    assert result.iss == "https://issuer.example"


@pytest.mark.parametrize(
    "callback",
    [
        "http://localhost:8765/oauth/callback?code=a&state=b",
        "http://127.0.0.1:8765/wrong?code=a&state=b",
        f"{REDIRECT}?code=a",
        f"{REDIRECT}?state=b",
        f"{REDIRECT}?error=access_denied&error_description=Nope",
        f"{REDIRECT}?code=a&code=b&state=c",
        f"{REDIRECT}?code=a&state=b&state=c",
        f"{REDIRECT}?code=a&state=b#fragment",
        "http://127.0.0.1:invalid/oauth/callback?code=a&state=b",
        "http://[invalid/oauth/callback?code=a&state=b",
        f"{REDIRECT};unexpected?code=a&state=b",
    ],
)
def test_invalid_callback_is_rejected(callback: str) -> None:
    with pytest.raises(InvalidOAuthCallback):
        parse_oauth_callback(callback, REDIRECT)


@pytest.mark.anyio
async def test_headless_flow_coordinates_url_and_callback(
    memory_storage: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_storage.tokens = None
    client = RobinhoodMCPClient(Settings(), storage=memory_storage)
    received: list[AuthorizationCodeResult] = []

    async def fake_run(url_future: object, callback_future: object) -> None:
        url_future.set_result("https://example.test/authorize?state=state-value")
        received.append(await callback_future)
        await memory_storage.set_tokens(OAuthToken(access_token="new-token"))

    monkeypatch.setattr(client, "_run_login", fake_run)
    flow = await client.start_login()
    with pytest.raises(AuthFlowConflict):
        await client.start_login()
    with pytest.raises(InvalidOAuthCallback, match="state"):
        await flow.complete(f"{REDIRECT}?code=code-value&state=wrong-state")
    assert (await client.auth_status())["flow_in_progress"] is True
    await flow.complete(f"{REDIRECT}?code=code-value&state=state-value&iss=issuer")

    assert received[0].code == "code-value"
    assert received[0].state == "state-value"
    assert (await client.auth_status())["authenticated"] is True


def test_callback_accepts_explicit_default_port() -> None:
    result = parse_oauth_callback(
        "https://example.test:443/callback?code=a&state=b",
        "https://example.test/callback",
    )
    assert result.code == "a"


def test_callback_rejects_zero_port() -> None:
    with pytest.raises(InvalidOAuthCallback, match="malformed"):
        parse_oauth_callback(
            "http://127.0.0.1:0/callback?code=a&state=b",
            "http://127.0.0.1/callback",
        )


@pytest.mark.anyio
async def test_cancel_login_cleans_up_background_task(
    memory_storage: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory_storage.tokens = None
    client = RobinhoodMCPClient(Settings(), storage=memory_storage)
    cancelled = asyncio.Event()

    async def fake_run(url_future: object, callback_future: object) -> None:
        url_future.set_result("https://example.test/authorize?state=state")
        try:
            await callback_future
        finally:
            cancelled.set()

    monkeypatch.setattr(client, "_run_login", fake_run)
    await client.start_login()
    await client.cancel_login()
    assert cancelled.is_set()
    assert (await client.auth_status())["flow_in_progress"] is False


@pytest.mark.anyio
async def test_browser_callback_bind_failure_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable_port(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("port in use")

    monkeypatch.setattr(asyncio, "start_server", unavailable_port)
    with pytest.raises(ConfigurationError, match="ensure the port is free"):
        async with LoopbackCallbackReceiver(REDIRECT):
            pass


@pytest.mark.anyio
async def test_browser_callback_ignores_probes_and_reports_authorization_denial() -> None:
    receiver = LoopbackCallbackReceiver(REDIRECT)
    receiver._callback = asyncio.get_running_loop().create_future()
    responses: list[bytes] = []

    class MemoryWriter:
        def write(self, data: bytes) -> None:
            responses.append(data)

        async def drain(self) -> None:
            pass

        def close(self) -> None:
            pass

        async def wait_closed(self) -> None:
            pass

    async def send_request(target: str) -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(f"GET {target} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())
        reader.feed_eof()
        await receiver._handle(reader, MemoryWriter())

    await send_request("/oauth/callback")
    assert responses[-1].startswith(b"HTTP/1.1 400")
    assert not receiver._callback.done()
    await send_request("/oauth/callback?error=access_denied&state=state")
    callback = await receiver.wait(1)
    assert "error=access_denied" in callback
    with pytest.raises(InvalidOAuthCallback, match="rejected"):
        parse_oauth_callback(callback, REDIRECT)
