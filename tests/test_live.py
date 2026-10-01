from __future__ import annotations

import os

import pytest

from robinhood_mcp_wrapper.client import RobinhoodMCPClient
from robinhood_mcp_wrapper.config import Settings

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_ROBINHOOD") != "1",
        reason="set RUN_LIVE_ROBINHOOD=1 to run read-only live discovery",
    ),
]


@pytest.mark.anyio
async def test_live_read_only_discovery() -> None:
    client = RobinhoodMCPClient(Settings.from_env())
    async with client:
        metadata = await client.server_metadata()
        tools = await client.list_tools(refresh=True)
    assert metadata["protocol_version"]
    assert tools.tools
