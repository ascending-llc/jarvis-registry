"""A loopback-only, read-only MCP fixture with one fabricated ticket.

No credentials, real records, external calls or write tools. This fixture does
not authenticate users: Jarvis' identity/ACL layer is the separate test subject.
Tested with the repository's pinned mcp 2.x SDK.
"""

import argparse

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from starlette.applications import Starlette

HOST = "127.0.0.1"
# mcp 2.x takes transport options at run/mount time rather than on the server constructor.
HTTP_OPTIONS = {"json_response": True, "stateless_http": True}


def build_server() -> MCPServer:
    app = MCPServer(
        "Jarvis synthetic access-control lab",
        instructions="Local synthetic test fixture; not a business system or identity provider.",
        log_level="WARNING",
    )

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
            # mcp 2.x shows a ToolError message to the caller; other exceptions become a generic error.
            raise ToolError("Unknown synthetic ticket. Only SYNTHETIC-1 is supported.")
        return {"ticket_id": "SYNTHETIC-1", "status": "open", "synthetic": True}

    return app


def build_http_app(app: MCPServer) -> Starlette:
    """The loopback-only streamable-HTTP ASGI app that ``__main__`` serves."""
    return app.streamable_http_app(host=HOST, **HTTP_OPTIONS)


def valid_port(value: str) -> int:
    port = int(value)
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("Choose a port between 1024 and 65535.")
    return port


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=valid_port, default=8879)
    args = parser.parse_args()
    print(f"Synthetic MCP fixture: http://{HOST}:{args.port}/mcp", flush=True)
    build_server().run(transport="streamable-http", host=HOST, port=args.port, **HTTP_OPTIONS)
