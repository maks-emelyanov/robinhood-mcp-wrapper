from __future__ import annotations

import json
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
