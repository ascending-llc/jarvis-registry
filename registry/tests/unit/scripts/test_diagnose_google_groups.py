"""Tests for scripts/diagnose_google_groups.py without calling Google."""

import json
import sys
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from registry_pkgs.google.cloud_identity_client import GoogleWorkspaceGroupInfo
from scripts import diagnose_google_groups as diag

_KEY_JSON = json.dumps({"client_email": "sa@proj.iam.gserviceaccount.com", "private_key": "unused"})
_SEARCH_URL = "https://cloudidentity.googleapis.com/v1/groups/-/memberships:searchTransitiveGroups"


@pytest.fixture
def fake_client(monkeypatch) -> MagicMock:
    """Replace CloudIdentityGroupsClient in the script; the returned mock is the class."""
    instance = MagicMock()
    instance.list_transitive_groups_for_member = AsyncMock(return_value=[])
    instance.get_group = AsyncMock(return_value={})
    instance.aclose = AsyncMock()
    client_cls = MagicMock(return_value=instance)
    monkeypatch.setattr(diag, "CloudIdentityGroupsClient", client_cls)
    return client_cls


def _run_main(monkeypatch, *argv: str, customer_id_setting: str | None = None) -> int:
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_KEY_JSON", _KEY_JSON)
    monkeypatch.setenv("GOOGLE_ALLOWED_HD", "example.com")
    if customer_id_setting is None:
        monkeypatch.delenv("GOOGLE_CUSTOMER_ID", raising=False)
    else:
        monkeypatch.setenv("GOOGLE_CUSTOMER_ID", customer_id_setting)
    monkeypatch.setattr(sys, "argv", ["diagnose_google_groups.py", *argv])
    with pytest.raises(SystemExit) as exit_info:
        diag.main()
    return exit_info.value.code


@pytest.mark.unit
def test_customer_id_defaults_to_setting(monkeypatch, fake_client):
    assert _run_main(monkeypatch, "ada@example.com", customer_id_setting=" C046psxkn ") == 0
    assert fake_client.call_args.kwargs["customer_id"] == "C046psxkn"


@pytest.mark.unit
def test_customer_id_flag_overrides_setting(monkeypatch, fake_client):
    exit_code = _run_main(monkeypatch, "--customer-id", "C01l9pmcs", "ada@example.com", customer_id_setting="C046psxkn")

    assert exit_code == 0
    assert fake_client.call_args.kwargs["customer_id"] == "C01l9pmcs"


@pytest.mark.unit
def test_invalid_customer_id_flag_exits_2(monkeypatch, fake_client, capsys):
    assert _run_main(monkeypatch, "--customer-id", "c046psxkn", "ada@example.com") == 2
    assert "--customer-id must start with 'C'" in capsys.readouterr().err
    fake_client.assert_not_called()


@pytest.mark.unit
def test_malformed_customer_id_setting_exits_2(monkeypatch, fake_client, capsys):
    assert _run_main(monkeypatch, "ada@example.com", customer_id_setting="C1' || true") == 2
    assert "GOOGLE_CUSTOMER_ID must start with 'C'" in capsys.readouterr().err
    fake_client.assert_not_called()


@pytest.mark.unit
def test_http_error_prints_google_body(monkeypatch, fake_client, capsys):
    body = {"error": {"code": 403, "message": "Error(4013): Insufficient permissions", "status": "PERMISSION_DENIED"}}
    request = httpx.Request("GET", _SEARCH_URL)
    response = httpx.Response(403, json=body, request=request)
    fake_client.return_value.list_transitive_groups_for_member.side_effect = httpx.HTTPStatusError(
        "forbidden", request=request, response=response
    )

    assert _run_main(monkeypatch, "ada@example.com") == 1
    out = capsys.readouterr().out
    assert "FAILED: HTTP 403 Forbidden" in out
    assert "Error(4013): Insufficient permissions" in out
    assert "PERMISSION_DENIED" in out


@pytest.mark.unit
def test_token_failure_is_reported_per_member(monkeypatch, fake_client, capsys):
    fake_client.return_value.list_transitive_groups_for_member.side_effect = ValueError("bad key")

    assert _run_main(monkeypatch, "ada@example.com") == 1
    assert "FAILED to acquire a Cloud Identity access token: bad key" in capsys.readouterr().out


@pytest.mark.unit
def test_successful_lookup_prints_scope_and_owner(monkeypatch, fake_client, capsys):
    instance = fake_client.return_value
    instance.list_transitive_groups_for_member.return_value = [
        GoogleWorkspaceGroupInfo(
            email="jarvis-registry-admin@example.com", display_name="Admins", resource_name="groups/abc"
        )
    ]
    instance.get_group.return_value = {"parent": "customers/C046psxkn"}

    assert _run_main(monkeypatch, "Ada@Example.com") == 0
    out = capsys.readouterr().out
    assert "grants scope group 'jarvis-registry-admin'" in out
    assert "owner: customers/C046psxkn" in out
    instance.list_transitive_groups_for_member.assert_awaited_once_with("ada@example.com")
    instance.get_group.assert_awaited_once_with("groups/abc")
    instance.aclose.assert_awaited_once()
