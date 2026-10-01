from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

from robinhood_mcp.config import Settings
from robinhood_mcp.errors import CredentialStoreError
from robinhood_mcp.storage import (
    FileTokenStorage,
    credential_fingerprint,
    default_credentials_file,
)


@pytest.mark.anyio
async def test_file_storage_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "credentials" / "oauth.json"
    storage = FileTokenStorage(Settings(), path=path)
    token = OAuthToken(access_token="access", refresh_token="refresh", expires_in=3600)
    client_info = OAuthClientInformationFull(
        client_id="dynamic-client",
        redirect_uris=[AnyUrl("http://127.0.0.1:8765/oauth/callback")],
        token_endpoint_auth_method="none",
    )

    assert await storage.get_tokens() is None
    assert await storage.get_client_info() is None

    await storage.set_tokens(token)
    await storage.set_client_info(client_info)

    assert await storage.get_tokens() == token
    assert await storage.get_client_info() == client_info
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["version"] == 1

    await storage.clear_tokens()
    assert await storage.get_tokens() is None
    assert await storage.get_client_info() == client_info
    assert path.exists()

    await storage.clear_client_info()
    assert await storage.get_client_info() is None
    assert not path.exists()


@pytest.mark.anyio
async def test_file_storage_repairs_permissive_mode(tmp_path: Path) -> None:
    path = tmp_path / "oauth.json"
    storage = FileTokenStorage(Settings(), path=path)
    await storage.set_tokens(OAuthToken(access_token="access"))
    path.chmod(0o644)

    assert await storage.get_tokens() is not None
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.anyio
async def test_invalid_credential_file_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "oauth.json"
    path.write_text("{not-json", encoding="utf-8")
    path.chmod(0o600)
    storage = FileTokenStorage(Settings(), path=path)

    with pytest.raises(CredentialStoreError, match="Unable to read"):
        await storage.get_tokens()


def test_configured_credentials_path_is_used(tmp_path: Path) -> None:
    path = tmp_path / "custom.json"
    settings = Settings(credentials_file=str(path))
    assert FileTokenStorage(settings).path == path


def test_default_path_is_namespaced_by_registration() -> None:
    base = default_credentials_file(Settings())
    other = default_credentials_file(Settings(client_name="Other client"))
    assert base != other
    assert base.name.startswith("credentials-")
    assert base.suffix == ".json"


def test_fingerprint_changes_with_registration_metadata() -> None:
    base = credential_fingerprint(Settings())
    assert base != credential_fingerprint(Settings(client_name="Other client"))
    assert base != credential_fingerprint(
        Settings(redirect_uri="http://127.0.0.1:9999/oauth/callback")
    )


@pytest.mark.anyio
@pytest.mark.parametrize("dangling", [False, True])
async def test_symlink_credentials_are_rejected(tmp_path: Path, dangling: bool) -> None:
    target = tmp_path / "target.json"
    if not dangling:
        target.write_text('{"version": 1}', encoding="utf-8")
    path = tmp_path / "oauth.json"
    path.symlink_to(target)
    storage = FileTokenStorage(Settings(), path=path)
    for operation in (
        storage.get_tokens,
        storage.clear_tokens,
        storage.clear_all,
    ):
        with pytest.raises(CredentialStoreError, match="symbolic link"):
            await operation()
    with pytest.raises(CredentialStoreError, match="symbolic link"):
        await storage.set_tokens(OAuthToken(access_token="secret"))
    assert path.is_symlink()


@pytest.mark.anyio
async def test_non_regular_credentials_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "oauth.json"
    path.mkdir()
    storage = FileTokenStorage(Settings(), path=path)
    with pytest.raises(CredentialStoreError, match="regular file"):
        await storage.get_tokens()


@pytest.mark.anyio
async def test_storage_works_without_fchmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(os, "fchmod", raising=False)
    path = tmp_path / "oauth.json"
    storage = FileTokenStorage(Settings(), path=path)
    await storage.set_tokens(OAuthToken(access_token="access"))
    path.chmod(0o644)
    assert (await storage.get_tokens()).access_token == "access"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
