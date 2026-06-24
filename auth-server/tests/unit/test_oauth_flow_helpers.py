"""Unit tests for oauth_flow redirect_uri helpers."""

from auth_server.routes.oauth_flow import _is_registered_redirect_uri


class TestIsRegisteredRedirectUri:
    def test_no_registered_uris_allows_any(self) -> None:
        assert _is_registered_redirect_uri({}, "https://anything.example.com/cb") is True

    def test_exact_match_non_loopback(self) -> None:
        meta = {"redirect_uris": ["https://app.example.com/cb"]}
        assert _is_registered_redirect_uri(meta, "https://app.example.com/cb") is True

    def test_non_loopback_port_mismatch_rejected(self) -> None:
        meta = {"redirect_uris": ["https://app.example.com:8000/cb"]}
        assert _is_registered_redirect_uri(meta, "https://app.example.com:9000/cb") is False

    def test_loopback_port_exemption(self) -> None:
        meta = {"redirect_uris": ["http://127.0.0.1:1234/cb"]}
        assert _is_registered_redirect_uri(meta, "http://127.0.0.1:55555/cb") is True

    def test_matches_any_registered_uri(self) -> None:
        meta = {"redirect_uris": ["https://a.example.com/cb", "https://b.example.com/cb"]}
        assert _is_registered_redirect_uri(meta, "https://b.example.com/cb") is True
