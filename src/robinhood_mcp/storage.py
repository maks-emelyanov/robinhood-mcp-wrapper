"""Persistent OAuth token and dynamic-client storage."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any, Protocol

import anyio
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from platformdirs import user_data_path

from robinhood_mcp.config import Settings
from robinhood_mcp.errors import CredentialStoreError

_FILE_VERSION = 1
_TOKEN_KEY = "oauth_token"
_CLIENT_KEY = "oauth_client"


class CredentialStorage(Protocol):
    async def get_tokens(self) -> OAuthToken | None: ...

    async def set_tokens(self, tokens: OAuthToken) -> None: ...

    async def get_client_info(self) -> OAuthClientInformationFull | None: ...

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None: ...

    async def clear_tokens(self) -> None: ...

    async def clear_client_info(self) -> None: ...


def credential_fingerprint(settings: Settings) -> str:
    material = "\x00".join(
        (settings.mcp_url, settings.client_name, settings.scope, settings.redirect_uri)
    )
    return hashlib.sha256(material.encode()).hexdigest()[:24]


def default_credentials_file(settings: Settings) -> Path:
    """Return the per-user credential file for this endpoint and OAuth registration."""

    directory = Path(user_data_path("robinhood-mcp-wrapper", appauthor=False))
    return directory / f"credentials-{credential_fingerprint(settings)}.json"


class FileTokenStorage:
    """Passwordless local-file storage for MCP OAuth credentials."""

    def __init__(self, settings: Settings, *, path: Path | None = None) -> None:
        configured = (
            Path(settings.credentials_file).expanduser() if settings.credentials_file else None
        )
        self.path = Path(path or configured or default_credentials_file(settings)).expanduser()
        self.fingerprint = credential_fingerprint(settings)
        self._write_lock = threading.Lock()

    def _read_file(self) -> dict[str, Any]:
        if self.path.is_symlink():
            raise CredentialStoreError("Credential file must not be a symbolic link")

        descriptor: int | None = None
        try:
            # Inspect the opened file rather than a path that can be replaced
            # between the ownership check and reading secrets. O_NONBLOCK also
            # prevents a named pipe from hanging before it is rejected.
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            descriptor = os.open(self.path, flags)
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise CredentialStoreError("Credential path is not a regular file")
            if hasattr(os, "getuid") and file_stat.st_uid != os.getuid():
                raise CredentialStoreError("Credential file is not owned by the current user")
            if stat.S_IMODE(file_stat.st_mode) & 0o077:
                if hasattr(os, "fchmod"):
                    os.fchmod(descriptor, 0o600)
                else:  # Windows has no descriptor-based chmod.
                    os.chmod(self.path, 0o600)
            with os.fdopen(descriptor, encoding="utf-8") as handle:
                descriptor = None
                payload = json.load(handle)
        except FileNotFoundError:
            return {"version": _FILE_VERSION}
        except CredentialStoreError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise CredentialStoreError(f"Unable to read credential file: {self.path}") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

        if not isinstance(payload, dict) or payload.get("version") != _FILE_VERSION:
            raise CredentialStoreError(f"Credential file has an unsupported format: {self.path}")
        return payload

    def _write_file(self, payload: dict[str, Any]) -> None:
        temporary_path: Path | None = None
        descriptor: int | None = None
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                dir=self.path.parent,
            )
            temporary_path = Path(temporary_name)
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            else:
                os.chmod(temporary_path, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = None
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
            os.chmod(self.path, 0o600)
        except OSError as exc:
            raise CredentialStoreError(f"Unable to write credential file: {self.path}") from exc
        finally:
            if descriptor is not None:
                with contextlib.suppress(OSError):
                    os.close(descriptor)
            if temporary_path is not None:
                with contextlib.suppress(OSError):
                    temporary_path.unlink()

    def _get(self, key: str) -> Any | None:
        return self._read_file().get(key)

    def _set(self, key: str, value: dict[str, Any] | None) -> None:
        with self._write_lock:
            payload = self._read_file()
            if value is None:
                payload.pop(key, None)
            else:
                payload[key] = value
            if _TOKEN_KEY not in payload and _CLIENT_KEY not in payload:
                self._delete_file()
            else:
                self._write_file(payload)

    def _delete_file(self) -> None:
        if self.path.is_symlink():
            raise CredentialStoreError("Credential file must not be a symbolic link")
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise CredentialStoreError(f"Unable to delete credential file: {self.path}") from exc

    async def get_tokens(self) -> OAuthToken | None:
        value = await anyio.to_thread.run_sync(self._get, _TOKEN_KEY)
        if value is None:
            return None
        try:
            return OAuthToken.model_validate(value)
        except Exception as exc:
            raise CredentialStoreError("Stored OAuth token data is invalid") from exc

    async def set_tokens(self, tokens: OAuthToken) -> None:
        value = tokens.model_dump(mode="json", exclude_none=True)
        await anyio.to_thread.run_sync(self._set, _TOKEN_KEY, value)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        value = await anyio.to_thread.run_sync(self._get, _CLIENT_KEY)
        if value is None:
            return None
        try:
            return OAuthClientInformationFull.model_validate(value)
        except Exception as exc:
            raise CredentialStoreError("Stored OAuth client registration is invalid") from exc

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        value = client_info.model_dump(mode="json", exclude_none=True)
        await anyio.to_thread.run_sync(self._set, _CLIENT_KEY, value)

    async def clear_tokens(self) -> None:
        await anyio.to_thread.run_sync(self._set, _TOKEN_KEY, None)

    async def clear_client_info(self) -> None:
        await anyio.to_thread.run_sync(self._set, _CLIENT_KEY, None)

    async def clear_all(self) -> None:
        await anyio.to_thread.run_sync(self._clear_all)

    def _clear_all(self) -> None:
        with self._write_lock:
            self._delete_file()
