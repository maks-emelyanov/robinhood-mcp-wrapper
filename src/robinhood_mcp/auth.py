"""OAuth flow coordination helpers."""

from __future__ import annotations

import asyncio
import contextlib
import html
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import ParseResult, parse_qs, urlparse

from mcp.client.auth import AuthorizationCodeResult

from robinhood_mcp.config import is_loopback_host
from robinhood_mcp.errors import ConfigurationError, InvalidOAuthCallback


def _origin(parsed: ParseResult) -> tuple[str, str, int | None]:
    port = parsed.port
    if port == 0:
        raise ValueError("OAuth callback port must be greater than zero")
    if port is None:
        port = {"http": 80, "https": 443}.get(parsed.scheme.lower())
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), port


def parse_oauth_callback(callback_url: str, expected_redirect_uri: str) -> AuthorizationCodeResult:
    """Parse a callback URL while preserving the OAuth state and issuer verbatim."""

    try:
        callback = urlparse(callback_url.strip())
        expected = urlparse(expected_redirect_uri)
        matches = _origin(callback) == _origin(expected) and callback.path == expected.path
    except ValueError as exc:
        raise InvalidOAuthCallback("Callback URL is malformed") from exc
    if (
        not matches
        or callback.username
        or callback.password
        or callback.fragment
        or callback.params
    ):
        raise InvalidOAuthCallback("Callback URL does not match the registered redirect URI")

    params = parse_qs(callback.query, keep_blank_values=True)
    if any(len(params.get(key, [])) > 1 for key in ("code", "state", "iss", "error")):
        raise InvalidOAuthCallback("Callback URL contains duplicate OAuth parameters")
    if "error" in params:
        description = params.get("error_description", [params["error"][0]])[0]
        raise InvalidOAuthCallback(f"Authorization server rejected the request: {description}")
    code = params.get("code", [None])[0]
    state = params.get("state", [None])[0]
    if not code or not state:
        raise InvalidOAuthCallback("Callback URL must contain non-empty code and state parameters")
    return AuthorizationCodeResult(
        code=code,
        state=state,
        iss=params.get("iss", [None])[0],
    )


@dataclass(frozen=True, slots=True)
class AuthorizationFlow:
    """A pending headless authorization flow."""

    flow_id: str
    authorization_url: str
    redirect_uri: str
    expires_at: datetime
    _complete: Callable[[str, str], Awaitable[None]] = field(repr=False, compare=False)

    async def complete(self, callback_url: str) -> None:
        await self._complete(self.flow_id, callback_url)


class LoopbackCallbackReceiver:
    """Tiny one-shot HTTP receiver used by the desktop browser flow."""

    def __init__(self, redirect_uri: str) -> None:
        self.redirect_uri = redirect_uri
        self._parsed = urlparse(redirect_uri)
        if self._parsed.scheme != "http" or not self._parsed.hostname:
            raise ConfigurationError("Browser login requires an HTTP loopback redirect URI")
        if not is_loopback_host(self._parsed.hostname):
            raise ConfigurationError("Browser login requires a loopback redirect URI")
        self._server: asyncio.Server | None = None
        self._callback: asyncio.Future[str] | None = None

    async def __aenter__(self) -> LoopbackCallbackReceiver:
        loop = asyncio.get_running_loop()
        self._callback = loop.create_future()
        try:
            self._server = await asyncio.start_server(
                self._handle,
                self._parsed.hostname,
                self._parsed.port or 80,
            )
        except OSError as exc:
            raise ConfigurationError(
                f"Unable to bind browser OAuth callback at {self.redirect_uri}; "
                "ensure the port is free"
            ) from exc
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def wait(self, wait_seconds: float) -> str:
        if self._callback is None:
            raise RuntimeError("Callback receiver has not been started")
        async with asyncio.timeout(wait_seconds):
            return await asyncio.shield(self._callback)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        status = "400 Bad Request"
        title = "Authorization callback rejected"
        try:
            request_line = (await reader.readline()).decode("ascii", errors="replace").strip()
            while True:
                line = await reader.readline()
                if line in {b"\r\n", b"\n", b""}:
                    break
            method, target, _ = request_line.split(" ", 2)
            target_parts = urlparse(target)
            if (
                method != "GET"
                or not target.startswith("/")
                or target_parts.netloc
                or target_parts.path != self._parsed.path
            ):
                raise ValueError("Unexpected callback request")
            port = self._parsed.port
            authority = self._parsed.hostname or "127.0.0.1"
            if ":" in authority:
                authority = f"[{authority}]"
            if port is not None:
                authority = f"{authority}:{port}"
            callback_url = f"{self._parsed.scheme}://{authority}{target}"
            # Browser probes and malformed requests must not consume the
            # one callback awaited by the authorization flow.
            if "error" not in parse_qs(target_parts.query):
                parse_oauth_callback(callback_url, self.redirect_uri)
            if self._callback is not None and not self._callback.done():
                self._callback.set_result(callback_url)
            status = "200 OK"
            title = "Authorization received. You can close this window."
        except ValueError, UnicodeError, asyncio.IncompleteReadError, InvalidOAuthCallback:
            pass

        body = (
            "<!doctype html><html><head><meta charset='utf-8'><title>Robinhood MCP</title>"
            f"</head><body><p>{html.escape(title)}</p></body></html>"
        ).encode()
        writer.write(
            f"HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        with contextlib.suppress(ConnectionError):
            await writer.drain()
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
