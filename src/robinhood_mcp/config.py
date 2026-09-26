"""Environment-backed wrapper configuration."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, fields
from urllib.parse import urlparse

from robinhood_mcp.errors import ConfigurationError

DEFAULT_MCP_URL = "https://agent.robinhood.com/mcp/trading"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/oauth/callback"


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


def is_loopback_host(host: str) -> bool:
    """Return whether a bind hostname is unambiguously loopback-only."""

    normalized = host.strip().lower().strip("[]")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuration shared by the Python client, REST API, and CLI."""

    mcp_url: str = DEFAULT_MCP_URL
    client_name: str = "Robinhood MCP Wrapper"
    scope: str = "internal"
    redirect_uri: str = DEFAULT_REDIRECT_URI
    host: str = "127.0.0.1"
    port: int = 8765
    api_key: str | None = None
    credentials_file: str | None = None
    connect_timeout: float = 30.0
    read_timeout: float = 300.0
    oauth_timeout: float = 600.0
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, **overrides: object) -> Settings:
        values: dict[str, object] = {
            "mcp_url": os.getenv("ROBINHOOD_MCP_URL", DEFAULT_MCP_URL),
            "client_name": os.getenv("ROBINHOOD_MCP_CLIENT_NAME", "Robinhood MCP Wrapper"),
            "scope": os.getenv("ROBINHOOD_MCP_SCOPE", "internal"),
            "redirect_uri": os.getenv("ROBINHOOD_MCP_REDIRECT_URI", DEFAULT_REDIRECT_URI),
            "host": os.getenv("ROBINHOOD_API_HOST", "127.0.0.1"),
            "port": _env_int("ROBINHOOD_API_PORT", 8765),
            "api_key": os.getenv("ROBINHOOD_API_KEY") or None,
            "credentials_file": os.getenv("ROBINHOOD_CREDENTIALS_FILE") or None,
            "connect_timeout": _env_float("ROBINHOOD_CONNECT_TIMEOUT", 30.0),
            "read_timeout": _env_float("ROBINHOOD_READ_TIMEOUT", 300.0),
            "oauth_timeout": _env_float("ROBINHOOD_OAUTH_TIMEOUT", 600.0),
            "log_level": os.getenv("ROBINHOOD_LOG_LEVEL", "INFO").upper(),
        }
        valid = {item.name for item in fields(cls)}
        unknown = set(overrides) - valid
        if unknown:
            raise ConfigurationError(f"Unknown settings: {', '.join(sorted(unknown))}")
        values.update({key: value for key, value in overrides.items() if value is not None})
        settings = cls(**values)
        settings.validate()
        return settings

    def validate(self) -> None:
        mcp = urlparse(self.mcp_url)
        if mcp.scheme != "https" or not mcp.netloc:
            raise ConfigurationError("ROBINHOOD_MCP_URL must be an HTTPS URL")
        redirect = urlparse(self.redirect_uri)
        if redirect.scheme not in {"http", "https"} or not redirect.hostname:
            raise ConfigurationError("ROBINHOOD_MCP_REDIRECT_URI must be an HTTP(S) URL")
        if redirect.scheme == "http" and not is_loopback_host(redirect.hostname):
            raise ConfigurationError("An HTTP OAuth redirect must use a loopback host")
        if not 1 <= self.port <= 65535:
            raise ConfigurationError("ROBINHOOD_API_PORT must be between 1 and 65535")
        if min(self.connect_timeout, self.read_timeout, self.oauth_timeout) <= 0:
            raise ConfigurationError("Timeouts must be greater than zero")

    def validate_server_binding(self) -> None:
        if not is_loopback_host(self.host) and not self.api_key:
            raise ConfigurationError(
                "ROBINHOOD_API_KEY is required when binding the REST API beyond loopback"
            )
