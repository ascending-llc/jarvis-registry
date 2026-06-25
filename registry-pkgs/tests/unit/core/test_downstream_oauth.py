"""Unit tests for shared downstream OAuth helpers."""

from registry_pkgs.core.downstream_oauth import downstream_mcp_code_key, downstream_mcp_issuer, oauth_error_payload


def test_downstream_mcp_issuer_includes_user_and_server_path() -> None:
    assert (
        downstream_mcp_issuer("https://issuer.example.com", "user-1", "github/mcp")
        == "https://issuer.example.com/proxy/server/oauth/user-1/github/mcp"
    )


def test_downstream_mcp_code_key_uses_flow_namespace() -> None:
    assert downstream_mcp_code_key("code-1") == "downstream_mcp_code:code-1"


def test_oauth_error_payload_includes_description_when_present() -> None:
    assert oauth_error_payload("invalid_grant", "code expired") == {
        "error": "invalid_grant",
        "error_description": "code expired",
    }


def test_oauth_error_payload_omits_description_when_none() -> None:
    assert oauth_error_payload("server_error") == {"error": "server_error"}


def test_oauth_error_payload_omits_description_when_empty_string() -> None:
    assert oauth_error_payload("invalid_request", "") == {"error": "invalid_request"}
