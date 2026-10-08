class McpGatewayException(Exception):
    """Base class for gateway and MCP proxy runtime exceptions."""


class InternalServerException(McpGatewayException):
    """Represents rare, unexpected internal runtime failures."""


class EmbeddingReindexInProgressException(McpGatewayException):
    """Raised when an embedding-dependent write or search is attempted while an
    embedding-model reindex is in progress."""


class UrlElicitationRequiredException(McpGatewayException):
    """Raised when the caller must complete URL elicitation for OAuth flow."""

    auth_url: str
    server_name: str

    def __init__(self, msg: str, /, *, auth_url: str, server_name: str):
        super().__init__(msg)
        self.auth_url = auth_url
        self.server_name = server_name


class ConsentRequiredException(McpGatewayException):
    """Raised when the user must consent before this client calls a downstream MCP server."""

    auth_url: str
    server_name: str
    elicitation_id: str

    def __init__(self, msg: str, /, *, auth_url: str, server_name: str, elicitation_id: str):
        super().__init__(msg)
        self.auth_url = auth_url
        self.server_name = server_name
        self.elicitation_id = elicitation_id


class DownstreamHttpFailureException(McpGatewayException):
    """Raised on >=300 downstream HTTP responses from proxied MCP calls."""


class DownstreamUnauthorizedException(DownstreamHttpFailureException):
    """Raised when a downstream MCP server answers 401, so the caller can run OAuth recovery."""

    www_authenticate: str | None

    def __init__(self, msg: str, /, *, www_authenticate: str | None = None):
        super().__init__(msg)
        self.www_authenticate = www_authenticate


class DownstreamAuthRejectedException(McpGatewayException):
    """Raised when a downstream server rejects a freshly issued access token and its OAuth discovery
    is unchanged; starting another login would loop forever."""


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
