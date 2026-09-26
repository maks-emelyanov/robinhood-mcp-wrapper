"""Stable public exceptions for the wrapper."""

from __future__ import annotations

from typing import Any


class RobinhoodMCPError(Exception):
    """Base class for wrapper failures."""

    code = "robinhood_mcp_error"

    def __init__(self, message: str, *, details: Any | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class AuthenticationRequired(RobinhoodMCPError):
    code = "authentication_required"


class AuthFlowConflict(RobinhoodMCPError):
    code = "authentication_flow_conflict"


class InvalidOAuthCallback(RobinhoodMCPError):
    code = "invalid_oauth_callback"


class CredentialStoreError(RobinhoodMCPError):
    code = "credential_store_error"


class ToolValidationError(RobinhoodMCPError):
    code = "tool_validation_error"


class UpstreamMCPError(RobinhoodMCPError):
    code = "upstream_mcp_error"


class UpstreamTimeoutError(RobinhoodMCPError):
    code = "upstream_timeout"


class UpstreamUnavailableError(RobinhoodMCPError):
    code = "upstream_unavailable"


class ConfigurationError(RobinhoodMCPError):
    code = "configuration_error"
