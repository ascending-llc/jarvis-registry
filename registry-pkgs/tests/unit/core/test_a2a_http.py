from a2a.client import card_resolver
from a2a.client.transports import jsonrpc

from registry_pkgs.core import a2a_http


def test_sdk_exception_types_match_boundary() -> None:
    """A failure signals that the SDK dependency changed; review retiring the boundary."""
    assert card_resolver.httpx.HTTPStatusError is a2a_http.HTTPStatusError
    assert jsonrpc.httpx.HTTPStatusError is a2a_http.HTTPStatusError


def test_decoder_registry_is_accessible() -> None:
    assert a2a_http.DECODABLE_CONTENT_ENCODINGS
