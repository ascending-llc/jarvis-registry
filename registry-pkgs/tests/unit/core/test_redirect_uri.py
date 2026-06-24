"""Unit tests for shared redirect_uri validation."""

import pytest

from registry_pkgs.core.redirect_uri import (
    is_loopback_host,
    redirect_uri_matches,
    validate_registration_redirect_uri,
)


class TestIsLoopbackHost:
    @pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1", "LOCALHOST"])
    def test_loopback_hosts(self, host: str) -> None:
        assert is_loopback_host(host) is True

    @pytest.mark.parametrize("host", ["example.com", "10.0.0.1", "", None])
    def test_non_loopback_hosts(self, host: str | None) -> None:
        assert is_loopback_host(host) is False


class TestValidateRegistrationRedirectUri:
    @pytest.mark.parametrize(
        "uri",
        [
            "https://app.example.com/callback",
            "http://localhost:1234/cb",
            "http://127.0.0.1/cb",
            "http://[::1]:5000/cb",
            "https://example.com:8443/cb",
        ],
    )
    def test_valid_uris_pass(self, uri: str) -> None:
        validate_registration_redirect_uri(uri)  # should not raise

    @pytest.mark.parametrize(
        "uri",
        [
            "ftp://example.com/cb",  # bad scheme
            "https:///cb",  # no host
            "https://example.com/cb#frag",  # fragment
            "http://example.com/cb",  # non-loopback http
            "https://10.0.0.5/cb",  # RFC-1918
            "https://172.16.3.4/cb",  # RFC-1918
            "https://192.168.1.1/cb",  # RFC-1918
            "https://169.254.1.1/cb",  # link-local
            "https://127.0.0.1/cb",  # loopback IP over https
            "https://0.0.0.0/cb",  # unspecified
        ],
    )
    def test_invalid_uris_raise(self, uri: str) -> None:
        with pytest.raises(ValueError):
            validate_registration_redirect_uri(uri)


class TestRedirectUriMatches:
    def test_non_loopback_exact_match(self) -> None:
        uri = "https://app.example.com/callback"
        assert redirect_uri_matches(uri, uri) is True

    def test_non_loopback_port_mismatch_fails(self) -> None:
        assert redirect_uri_matches("https://app.example.com:9000/cb", "https://app.example.com:8000/cb") is False

    def test_non_loopback_path_mismatch_fails(self) -> None:
        assert redirect_uri_matches("https://app.example.com/other", "https://app.example.com/cb") is False

    def test_loopback_ignores_port(self) -> None:
        assert redirect_uri_matches("http://127.0.0.1:54321/cb", "http://127.0.0.1:1234/cb") is True

    def test_loopback_scheme_mismatch_fails(self) -> None:
        assert redirect_uri_matches("https://localhost:1/cb", "http://localhost:2/cb") is False

    def test_loopback_path_mismatch_fails(self) -> None:
        assert redirect_uri_matches("http://localhost:1/x", "http://localhost:2/y") is False

    def test_received_non_loopback_against_registered_loopback_fails(self) -> None:
        assert redirect_uri_matches("http://evil.com:1234/cb", "http://localhost:1234/cb") is False
