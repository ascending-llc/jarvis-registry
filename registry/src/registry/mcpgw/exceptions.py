class McpGatewayException(Exception):
    """
    Base class of all exceptions raised by the `mcpgw` subpackage at runtime.
    """

    pass


class InternalServerException(McpGatewayException):
    """
    This class represents exception that is either:
    - theoretically possible but in practice rarely happens, e.g. an MCPServerDocument missing the `.config.url` field;
    - runtime exceptions caused by rare edge cases that we have never seen before.

    MCP resource/prompt/tool handler functions should let this exception bubble up and be turned into
    MCP JSON-RPC error responses by the framework.
    """

    pass


class UrlElicitationRequiredException(McpGatewayException):
    """
    Re-raised when catching an OAuthReAuthRequiredError from MCPOAuthService.
    """

    auth_url: str
    server_name: str

    def __init__(self, msg: str, /, *, auth_url: str, server_name: str):
        super().__init__(msg)

        self.auth_url = auth_url
        self.server_name = server_name


class DownstreamHttpFailureException(McpGatewayException):
    """
    Raised when receiving a >=300 status code for the HTTP request to downstream MCP server.
    """

    pass


class MisimplementedSpecException(McpGatewayException):
    """
    Raised when detecting that the downstream server misimplements a certain aspect of the 2025-11-25 MCP spec.
    Reference: https://modelcontextprotocol.io/specification/2025-11-25
    """

    pass
