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


@pytest.mark.parametrize("field", ["connect_timeout", "read_timeout", "oauth_timeout"])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), float("-inf")])
def test_timeouts_must_be_finite_and_positive(field: str, value: float) -> None:
    with pytest.raises(ConfigurationError, match="Timeouts"):
        Settings(**{field: value}).validate()


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/mcp",
        "https://",
        "https://[invalid/mcp",
        "https://example.com:invalid/mcp",
        "https://example.com:65536/mcp",
        "https://example.com:0/mcp",
        "https://user:secret@example.com/mcp",
        "https://example.com/mcp#fragment",
        "https://example.com/mcp\n",
        "https://example .com/mcp",
    ],
)
def test_invalid_mcp_urls_have_configuration_errors(url: str) -> None:
    with pytest.raises(ConfigurationError, match="ROBINHOOD_MCP_URL"):
        Settings(mcp_url=url).validate()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:invalid/oauth/callback",
        "http://[invalid/oauth/callback",
        "http://user:secret@127.0.0.1/oauth/callback",
        "http://127.0.0.1/oauth/callback#fragment",
        "http://127.0.0.1/oauth/callback?state=fixed",
        "http://127.0.0.1/oauth/callback;parameter",
    ],
)
def test_invalid_redirect_urls_have_configuration_errors(url: str) -> None:
    with pytest.raises(ConfigurationError, match="ROBINHOOD_MCP_REDIRECT_URI"):
        Settings(redirect_uri=url).validate()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("client_name", "  ", "CLIENT_NAME"),
        ("host", "", "API_HOST"),
        ("host", "127.0.0.1 ", "API_HOST"),
        ("api_key", "  ", "API_KEY"),
        ("log_level", "unsupported", "LOG_LEVEL"),
        ("port", 0, "API_PORT"),
        ("port", 65536, "API_PORT"),
    ],
)
def test_invalid_scalar_settings(field: str, value: object, message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        Settings(**{field: value}).validate()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("ROBINHOOD_API_PORT", "invalid", "integer"),
        ("ROBINHOOD_CONNECT_TIMEOUT", "invalid", "number"),
        ("ROBINHOOD_READ_TIMEOUT", "nan", "finite"),
        ("ROBINHOOD_OAUTH_TIMEOUT", "inf", "finite"),
    ],
)
def test_invalid_environment_configuration(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str, message: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigurationError, match=message):
        Settings.from_env()


def test_unknown_setting_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="Unknown settings: typo"):
        Settings.from_env(typo="value")
