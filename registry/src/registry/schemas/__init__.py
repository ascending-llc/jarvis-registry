"""Models for the registry service."""

from .anthropic_schema import (
    ErrorResponse,
    Package,
    PaginationMetadata,
    Repository,
    ServerDetail,
    ServerList,
    ServerResponse,
    SseTransport,
    StdioTransport,
    StreamableHttpTransport,
)
from .errors import (
    APIErrorDetail,
    APIErrorResponse,
    ErrorCode,
    create_error_detail,
)

__all__ = [
    "Repository",
    "StdioTransport",
    "StreamableHttpTransport",
    "SseTransport",
    "Package",
    "ServerDetail",
    "ServerResponse",
    "ServerList",
    "PaginationMetadata",
    "ErrorResponse",
    # Error handling
    "APIErrorDetail",
    "APIErrorResponse",
    "ErrorCode",
    "create_error_detail",
]
