from __future__ import annotations

import pytest

from robinhood_mcp.config import Settings, is_loopback_host
from robinhood_mcp.errors import ConfigurationError


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "[::1]", "localhost"])
def test_loopback_hosts(host: str) -> None:
    assert is_loopback_host(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.4", "example.com"])
def test_non_loopback_hosts(host: str) -> None:
    assert not is_loopback_host(host)


def test_settings_environment_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBINHOOD_API_PORT", "9000")
    monkeypatch.setenv("ROBINHOOD_MCP_CLIENT_NAME", "Environment client")
    monkeypatch.setenv("ROBINHOOD_CREDENTIALS_FILE", "/tmp/robinhood-test-credentials.json")
    settings = Settings.from_env(port=9100)
    assert settings.port == 9100
    assert settings.client_name == "Environment client"
    assert settings.credentials_file == "/tmp/robinhood-test-credentials.json"


def test_non_loopback_binding_requires_key() -> None:
    with pytest.raises(ConfigurationError, match="API_KEY"):
        Settings(host="0.0.0.0").validate_server_binding()
    Settings(host="0.0.0.0", api_key="secret").validate_server_binding()


def test_insecure_non_loopback_redirect_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="loopback"):
        Settings(redirect_uri="http://example.com/callback").validate()
