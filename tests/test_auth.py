from __future__ import annotations

import pytest
from mcp.client.auth import AuthorizationCodeResult
from mcp.shared.auth import OAuthToken

from robinhood_mcp.auth import parse_oauth_callback
from robinhood_mcp.client import RobinhoodMCPClient
from robinhood_mcp.config import Settings
from robinhood_mcp.errors import AuthFlowConflict, InvalidOAuthCallback

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
        url_future.set_result("https://example.test/authorize")
        received.append(await callback_future)
        await memory_storage.set_tokens(OAuthToken(access_token="new-token"))

    monkeypatch.setattr(client, "_run_login", fake_run)
    flow = await client.start_login()
    with pytest.raises(AuthFlowConflict):
        await client.start_login()
    await flow.complete(f"{REDIRECT}?code=code-value&state=state-value&iss=issuer")

    assert received[0].code == "code-value"
    assert received[0].state == "state-value"
    assert (await client.auth_status())["authenticated"] is True
