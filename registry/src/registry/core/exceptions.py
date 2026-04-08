"""Shared exception types for MCP gateway and core client logic."""


class McpGatewayException(Exception):
    """Base class for gateway and MCP proxy runtime exceptions."""


class InternalServerException(McpGatewayException):
    """Represents rare, unexpected internal runtime failures."""


class UrlElicitationRequiredException(McpGatewayException):
    """Raised when the caller must complete URL elicitation for OAuth flow."""

    auth_url: str
    server_name: str

    def __init__(self, msg: str, /, *, auth_url: str, server_name: str):
        super().__init__(msg)
        self.auth_url = auth_url
        self.server_name = server_name


class DownstreamHttpFailureException(McpGatewayException):
    """Raised on >=300 downstream HTTP responses from proxied MCP calls."""


class MisimplementedSpecException(McpGatewayException):
    """Raised when a downstream server violates the MCP protocol contract."""


class A2AAgentCardNotFoundException(McpGatewayException):
    """Raised only when all known A2A well-known card endpoints return 404."""


class A2AAgentCardTransportException(McpGatewayException):
    """Raised when network or transport errors prevent fetching A2A agent card."""


class A2AAgentCardUpstreamException(McpGatewayException):
    """Raised when upstream returns a non-404 error for A2A agent card fetch."""


class A2AAgentCardParseException(McpGatewayException):
    """Raised when upstream response exists but cannot be parsed as a valid agent card."""
