"""Unit tests for shared redirect_uri validation."""

from urllib.parse import parse_qs, urlsplit

import pytest

from registry_pkgs.core.redirect_uri import (
    VENDOR_BROKER_REDIRECT_URIS,
    build_oauth_error_redirect_url,
    is_loopback_host,
    is_safe_unverified_redirect_target,
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


class TestIsSafeUnverifiedRedirectTarget:
    @pytest.mark.parametrize(
        "uri",
        [
            "http://localhost/callback",
            "http://localhost:54321/callback",
            "http://127.0.0.1/callback",
            "http://127.0.0.1:54321/callback",
            "http://[::1]/callback",
            "http://[::1]:54321/callback",
        ],
    )
    def test_http_loopback_accepted(self, uri: str) -> None:
        assert is_safe_unverified_redirect_target(uri) is True

    @pytest.mark.parametrize(
        "uri",
        [
            "https://localhost/callback",
            "https://127.0.0.1/callback",
            "https://[::1]/callback",
        ],
    )
    def test_https_loopback_rejected(self, uri: str) -> None:
        assert is_safe_unverified_redirect_target(uri) is False

    def test_exact_allowlist_match_accepted(self) -> None:
        assert (
            is_safe_unverified_redirect_target(
                "https://vscode.dev/redirect",
                VENDOR_BROKER_REDIRECT_URIS,
            )
            is True
        )

    @pytest.mark.parametrize(
        "uri",
        [
            "https://vscode.dev/redirect/",
            "https://vscode.dev/redirect/extra",
            "https://vscode.dev/redirect-elsewhere",
            "https://vscode.dev.evil.example/redirect",
        ],
    )
    def test_allowlist_near_matches_rejected(self, uri: str) -> None:
        assert is_safe_unverified_redirect_target(uri, VENDOR_BROKER_REDIRECT_URIS) is False

    @pytest.mark.parametrize(
        ("uri", "trusted"),
        [
            ("http://localhost/callback#fragment", frozenset()),
            (
                "https://trusted.example/callback#fragment",
                frozenset({"https://trusted.example/callback#fragment"}),
            ),
        ],
    )
    def test_fragment_rejected_even_if_otherwise_safe(self, uri: str, trusted: frozenset[str]) -> None:
        assert is_safe_unverified_redirect_target(uri, trusted) is False

    def test_malformed_uri_rejected_without_raising(self) -> None:
        assert is_safe_unverified_redirect_target("http://[::1") is False

    @pytest.mark.parametrize(
        "uri",
        [
            "http://evil.example\\@localhost/callback",
            "http://user@localhost/callback",
            "http://localhost:0/callback",
            "http://localhost:99999/callback",
            "http://local%68ost/callback",
            "http://localhost/call back",
            "http://localhost/callback\n",
        ],
    )
    def test_ambiguous_loopback_uri_rejected(self, uri: str) -> None:
        assert is_safe_unverified_redirect_target(uri) is False


class TestBuildOauthErrorRedirectUrl:
    def test_error_fields_always_present_and_return_is_string(self) -> None:
        result = build_oauth_error_redirect_url(
            "http://localhost:1234/callback",
            "invalid_client",
            "Unknown client_id",
        )

        assert isinstance(result, str)
        assert parse_qs(urlsplit(result).query) == {
            "error": ["invalid_client"],
            "error_description": ["Unknown client_id"],
        }

    def test_state_appended_when_provided(self) -> None:
        result = build_oauth_error_redirect_url(
            "http://localhost/callback",
            "invalid_request",
            "Bad redirect",
            "client-state",
        )

        assert parse_qs(urlsplit(result).query)["state"] == ["client-state"]

    def test_state_omitted_when_none(self) -> None:
        result = build_oauth_error_redirect_url(
            "http://localhost/callback",
            "invalid_client",
            "Unknown client_id",
            None,
        )

        assert "state" not in parse_qs(urlsplit(result).query)

    def test_empty_state_is_preserved(self) -> None:
        result = build_oauth_error_redirect_url(
            "http://localhost/callback",
            "invalid_client",
            "Unknown client_id",
            "",
        )

        assert parse_qs(urlsplit(result).query, keep_blank_values=True)["state"] == [""]

    def test_existing_query_is_preserved_without_duplicate_oauth_fields(self) -> None:
        result = build_oauth_error_redirect_url(
            "http://localhost/callback?existing=1&error=stale&state=stale",
            "invalid_client",
            "Unknown client_id",
            "fresh",
        )

        assert parse_qs(urlsplit(result).query) == {
            "existing": ["1"],
            "error": ["invalid_client"],
            "error_description": ["Unknown client_id"],
            "state": ["fresh"],
        }


class TestValidateRegistrationRedirectUri:
    @pytest.mark.parametrize(
        "uri",
        [
            "https://app.example.com/callback",
            "http://localhost:1234/cb",
            "http://127.0.0.1/cb",
            "http://[::1]:5000/cb",
            "https://example.com:8443/cb",
            # Native-app private-use schemes (RFC 8252 §7.1) — e.g. Cline / VS Code extensions.
            "vscode://saoudrizwan.claude-dev/oauth",
            "cline://oauth/callback",
            "com.example.app:/oauth2redirect",
        ],
    )
    def test_valid_uris_pass(self, uri: str) -> None:
        validate_registration_redirect_uri(uri)  # should not raise

    @pytest.mark.parametrize(
        "uri",
        [
            "https:///cb",  # no host
            "https://example.com/cb#frag",  # fragment
            "vscode://app/cb#frag",  # fragment on a native scheme
            "http://example.com/cb",  # non-loopback http
            "https://10.0.0.5/cb",  # RFC-1918
            "https://172.16.3.4/cb",  # RFC-1918
            "https://192.168.1.1/cb",  # RFC-1918
            "https://169.254.1.1/cb",  # link-local
            "https://127.0.0.1/cb",  # loopback IP over https
            "https://0.0.0.0/cb",  # unspecified
            # Dangerous browser-executing schemes are always rejected.
            "javascript:alert(1)",
            "data:text/html;base64,PHNjcmlwdD4=",
            "file:///etc/passwd",
            # Network / remote-transport schemes would leak the code off-device — rejected.
            "ftp://attacker.example.com/cb",
            "ftps://attacker.example.com/cb",
            "gopher://attacker.example.com/cb",
            "ws://attacker.example.com/cb",
            "wss://attacker.example.com/cb",
            "mailto:attacker@example.com",
            "sms:+15551234567",
            "tel:+15551234567",
            "cb-only",  # no scheme
            "http://evil.example\\@localhost/callback",
            "http://user@localhost/callback",
            "http://localhost:0/callback",
            "http://localhost:99999/callback",
            "https://user@example.com/callback",
            "https://exam%70le.com/callback",
            "https://example.com/call back",
            "https://example.com/callback\r",
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

    def test_loopback_ignores_only_port_when_query_matches(self) -> None:
        assert (
            redirect_uri_matches(
                "http://localhost:54321/cb?tenant=expected",
                "http://localhost:1234/cb?tenant=expected",
            )
            is True
        )

    def test_loopback_query_mismatch_fails(self) -> None:
        assert (
            redirect_uri_matches(
                "http://localhost:54321/cb?tenant=attacker",
                "http://localhost:1234/cb?tenant=expected",
            )
            is False
        )

    def test_loopback_fragment_mismatch_fails(self) -> None:
        assert redirect_uri_matches("http://localhost:54321/cb", "http://localhost:1234/cb#fragment") is False

    def test_loopback_scheme_mismatch_fails(self) -> None:
        assert redirect_uri_matches("https://localhost:1/cb", "http://localhost:2/cb") is False

    def test_loopback_path_mismatch_fails(self) -> None:
        assert redirect_uri_matches("http://localhost:1/x", "http://localhost:2/y") is False

    def test_received_non_loopback_against_registered_loopback_fails(self) -> None:
        assert redirect_uri_matches("http://evil.com:1234/cb", "http://localhost:1234/cb") is False

    @pytest.mark.parametrize(
        "received",
        [
            "http://evil.example\\@localhost/cb",
            "http://user@localhost/cb",
            "http://localhost:0/cb",
            "http://localhost:99999/cb",
        ],
    )
    def test_ambiguous_received_loopback_fails(self, received: str) -> None:
        assert redirect_uri_matches(received, "http://localhost:1234/cb") is False
