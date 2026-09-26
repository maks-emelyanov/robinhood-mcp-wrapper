from __future__ import annotations

from dataclasses import dataclass

import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class MemoryStorage:
    tokens: OAuthToken | None = None
    client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client_info = client_info

    async def clear_tokens(self) -> None:
        self.tokens = None

    async def clear_client_info(self) -> None:
        self.client_info = None


@pytest.fixture
def memory_storage() -> MemoryStorage:
    return MemoryStorage(tokens=OAuthToken(access_token="test-token"))
