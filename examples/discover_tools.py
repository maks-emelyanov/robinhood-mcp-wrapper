"""Print server metadata and tool schemas without invoking trading tools.

Authenticate first with ``uv run --locked robinhood-mcp-wrapper auth login``,
then run from the root:
``uv run --locked python examples/discover_tools.py``.
"""

from __future__ import annotations

import asyncio
import json

from robinhood_mcp_wrapper import RobinhoodMCPClient
from robinhood_mcp_wrapper.errors import RobinhoodMCPError
from robinhood_mcp_wrapper.serialization import to_jsonable


async def main() -> None:
    async with RobinhoodMCPClient() as client:
        metadata = await client.server_metadata()
        tools = await client.list_all_tools(refresh=True)
    print(json.dumps({"server": metadata, "tools": to_jsonable(tools)}, indent=2))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RobinhoodMCPError as exc:
        raise SystemExit(f"Error [{exc.code}]: {exc.message}") from exc
