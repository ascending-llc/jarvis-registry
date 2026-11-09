"""A loopback-only, read-only MCP fixture with one fabricated ticket.

No credentials, real records, external calls or write tools. This fixture does
not authenticate users: Jarvis' identity/ACL layer is the separate test subject.
Tested with the repository's pinned mcp 1.28.1 SDK.
"""

import argparse

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Settings
from mcp.types import ToolAnnotations


def build_server(port: int = 8879) -> FastMCP:
    # The v1 SDK otherwise reads .env during Settings construction. Disable that
    # source only while constructing this isolated test fixture, then restore it.
    env_file = Settings.model_config.get("env_file")
    Settings.model_config["env_file"] = None
    try:
        app = FastMCP(
            "Jarvis synthetic access-control lab",
            instructions="Local synthetic test fixture; not a business system or identity provider.",
            host="127.0.0.1",
            port=port,
            json_response=True,
            stateless_http=True,
            log_level="WARNING",
        )
    finally:
        Settings.model_config["env_file"] = env_file

    @app.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
    )
    def lookup_synthetic_ticket(ticket_id: str) -> dict[str, str | bool]:
        """Return a fabricated ticket. Only ticket_id='SYNTHETIC-1' is supported."""
        if ticket_id != "SYNTHETIC-1":
            raise ValueError("Unknown synthetic ticket. Only SYNTHETIC-1 is supported.")
        return {"ticket_id": "SYNTHETIC-1", "status": "open", "synthetic": True}

    return app


def valid_port(value: str) -> int:
    port = int(value)
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("Choose a port between 1024 and 65535.")
    return port


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=valid_port, default=8879)
    args = parser.parse_args()
    print(f"Synthetic MCP fixture: http://127.0.0.1:{args.port}/mcp", flush=True)
    build_server(args.port).run(transport="streamable-http")
