"""Probe only the loopback fixture, not a live Registry or an identity provider."""

import argparse
import asyncio
import json

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from synthetic_mcp_server import valid_port


async def probe(port: int) -> None:
    url = f"http://127.0.0.1:{port}/mcp"
    async with httpx.AsyncClient(trust_env=False, timeout=5) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listing = await session.list_tools()
                success = await session.call_tool("lookup_synthetic_ticket", {"ticket_id": "SYNTHETIC-1"})
                unknown = await session.call_tool("lookup_synthetic_ticket", {"ticket_id": "OTHER-1"})
                missing = await session.call_tool("lookup_synthetic_ticket", {})
                assert [tool.name for tool in listing.tools] == ["lookup_synthetic_ticket"]
                assert success.isError is False
                assert success.structuredContent == {"ticket_id": "SYNTHETIC-1", "status": "open", "synthetic": True}
                assert unknown.isError is True
                assert missing.isError is True
                print(
                    json.dumps(
                        {
                            "endpoint": url,
                            "boundary": "real MCP fixture only; not Jarvis authorization",
                            "tools": [tool.model_dump(mode="json", exclude_none=True) for tool in listing.tools],
                            "success": success.model_dump(mode="json", exclude_none=True),
                            "unknown_id_is_error": unknown.isError,
                            "missing_argument_is_error": missing.isError,
                        },
                        indent=2,
                    )
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=valid_port, default=8879)
    asyncio.run(probe(parser.parse_args().port))
