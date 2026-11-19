"""Classic httpx boundary: a2a-sdk 0.3.x/1.x only translates classic httpx errors.

Clients passed to the SDK and HTTP exception handlers around SDK calls must
use these exports. When the SDK depends on httpx2, repoint these aliases,
then migrate callers and remove this module and the classic httpx dependency.
Do not import the internal httpx module object from this boundary.
"""

import httpx
from httpx._decoders import SUPPORTED_DECODERS

AsyncClient = httpx.AsyncClient
Auth = httpx.Auth
Headers = httpx.Headers
Limits = httpx.Limits
MockTransport = httpx.MockTransport
Request = httpx.Request
Response = httpx.Response
Timeout = httpx.Timeout
HTTPError = httpx.HTTPError
HTTPStatusError = httpx.HTTPStatusError
TimeoutException = httpx.TimeoutException

# Codings this client's aread()/aiter_bytes() can reverse in this process. See
# registry.api.proxy_routes._pop_content_encoding_if_fully_decoded.
DECODABLE_CONTENT_ENCODINGS: frozenset[str] = frozenset(SUPPORTED_DECODERS.keys())

__all__ = [
    "AsyncClient",
    "Auth",
    "Headers",
    "Limits",
    "MockTransport",
    "Request",
    "Response",
    "Timeout",
    "HTTPError",
    "HTTPStatusError",
    "TimeoutException",
    "DECODABLE_CONTENT_ENCODINGS",
]
