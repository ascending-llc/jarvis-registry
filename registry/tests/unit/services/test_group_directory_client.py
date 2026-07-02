"""Unit tests for IdPGroupDirectoryClient implementations."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from registry.services.group_directory_client import (
    CognitoGroupDirectoryClient,
    EntraIdGroupDirectoryClient,
    KeycloakGroupDirectoryClient,
)


async def test_cognito_get_user_group_ids_returns_empty():
    client = CognitoGroupDirectoryClient()
    result = await client.get_user_group_ids("some-oid")
    assert result == []


async def test_cognito_get_group_members_returns_empty():
    client = CognitoGroupDirectoryClient()
    result = await client.get_group_members("some-group-oid")
    assert result == []


async def test_cognito_get_group_details_batch_returns_empty():
    client = CognitoGroupDirectoryClient()
    result = await client.get_group_details_batch(["g1", "g2"])
    assert result == []


async def test_keycloak_get_user_group_ids_returns_empty():
    client = KeycloakGroupDirectoryClient()
    result = await client.get_user_group_ids("some-oid")
    assert result == []


async def test_keycloak_get_group_members_returns_empty():
    client = KeycloakGroupDirectoryClient()
    result = await client.get_group_members("some-group-oid")
    assert result == []


async def test_keycloak_get_group_details_batch_returns_empty():
    client = KeycloakGroupDirectoryClient()
    result = await client.get_group_details_batch(["g1"])
    assert result == []


def _make_entra_client() -> EntraIdGroupDirectoryClient:
    return EntraIdGroupDirectoryClient(
        tenant_id="tenant-id",
        client_id="client-id",
        client_secret="client-secret",
        graph_url="https://graph.microsoft.com",
    )


def _make_entra_client_with_token() -> EntraIdGroupDirectoryClient:
    """Return a client with a pre-seeded valid token (skips token acquisition)."""
    client = _make_entra_client()
    client._access_token = "pre-seeded-tok"
    client._token_expiry = time.monotonic() + 3600
    return client


def _mock_http_context(mock_cls, *, post=None, get=None):
    """Wire up a mock httpx.AsyncClient context manager."""
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    if post is not None:
        mock_http.post = post
    if get is not None:
        mock_http.get = get
    mock_cls.return_value = mock_http
    return mock_http


def _json_resp(status: int, payload: dict) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    r.raise_for_status = MagicMock()
    r.text = ""
    return r


async def test_entra_token_is_cached():
    """A valid cached token must not trigger a second /token POST.

    First call:  POST /token (1) + POST getMemberGroups (2) = 2 calls
    Second call: token cached  + POST getMemberGroups (3)   = 1 more call
    Total: 3 POSTs, but only 1 to /token.
    """
    client = _make_entra_client()
    token_resp = _json_resp(200, {"access_token": "tok", "expires_in": 3600})
    member_resp = _json_resp(200, {"value": ["g1"]})

    with patch("registry.services.group_directory_client.httpx.AsyncClient") as mock_cls:
        mock_http = _mock_http_context(
            mock_cls,
            post=AsyncMock(side_effect=[token_resp, member_resp, member_resp]),
        )

        await client.get_user_group_ids("oid-1")
        await client.get_user_group_ids("oid-1")

    assert mock_http.post.call_count == 3
    # Verify only the first call was to the token endpoint
    first_call_url = mock_http.post.call_args_list[0].args[0]
    assert "oauth2" in first_call_url


async def test_entra_token_refreshed_when_expired():
    """An expired token must trigger a fresh /token POST."""
    client = _make_entra_client()
    client._access_token = "old-tok"
    client._token_expiry = time.monotonic() - 10  # expired

    fresh_token_resp = _json_resp(200, {"access_token": "new-tok", "expires_in": 3600})
    member_resp = _json_resp(200, {"value": []})

    with patch("registry.services.group_directory_client.httpx.AsyncClient") as mock_cls:
        _mock_http_context(
            mock_cls,
            post=AsyncMock(side_effect=[fresh_token_resp, member_resp]),
        )
        await client.get_user_group_ids("oid-1")

    assert client._access_token == "new-tok"


async def test_entra_token_acquisition_failure_raises():
    client = _make_entra_client()
    fail_resp = MagicMock()
    fail_resp.status_code = 401
    fail_resp.text = "unauthorized"

    with patch("registry.services.group_directory_client.httpx.AsyncClient") as mock_cls:
        _mock_http_context(mock_cls, post=AsyncMock(return_value=fail_resp))
        with pytest.raises(ValueError, match="Failed to acquire Graph API token"):
            await client.get_user_group_ids("oid-1")


async def test_entra_get_user_group_ids_happy_path():
    client = _make_entra_client_with_token()
    resp = _json_resp(200, {"value": ["g1", "g2"]})

    with patch("registry.services.group_directory_client.httpx.AsyncClient") as mock_cls:
        _mock_http_context(mock_cls, post=AsyncMock(return_value=resp))
        result = await client.get_user_group_ids("oid-user")

    assert result == ["g1", "g2"]


async def test_entra_get_user_group_ids_returns_empty_on_404():
    client = _make_entra_client_with_token()
    resp = MagicMock()
    resp.status_code = 404

    with patch("registry.services.group_directory_client.httpx.AsyncClient") as mock_cls:
        _mock_http_context(mock_cls, post=AsyncMock(return_value=resp))
        result = await client.get_user_group_ids("ghost-oid")

    assert result == []


async def test_entra_get_group_members_happy_path_with_pagination():
    client = _make_entra_client_with_token()

    page1 = _json_resp(
        200,
        {
            "value": [
                {"@odata.type": "#microsoft.graph.user", "id": "u1"},
                {"@odata.type": "#microsoft.graph.group", "id": "nested"},  # filtered out
            ],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/groups/gid/transitiveMembers?$skiptoken=abc",
        },
    )
    page2 = _json_resp(200, {"value": [{"@odata.type": "#microsoft.graph.user", "id": "u2"}]})

    with patch("registry.services.group_directory_client.httpx.AsyncClient") as mock_cls:
        _mock_http_context(mock_cls, get=AsyncMock(side_effect=[page1, page2]))
        result = await client.get_group_members("gid")

    assert result == ["u1", "u2"]


async def test_entra_get_group_details_batch_splits_into_chunks_of_20():
    client = _make_entra_client_with_token()
    group_ids = [f"g{i}" for i in range(25)]

    def _batch_resp(ids: list[str]) -> MagicMock:
        return _json_resp(
            200,
            {
                "responses": [
                    {
                        "id": gid,
                        "status": 200,
                        "body": {"id": gid, "displayName": f"Group {gid}", "mail": None, "description": None},
                    }
                    for gid in ids
                ]
            },
        )

    with patch("registry.services.group_directory_client.httpx.AsyncClient") as mock_cls:
        mock_http = _mock_http_context(
            mock_cls,
            post=AsyncMock(side_effect=[_batch_resp(group_ids[:20]), _batch_resp(group_ids[20:])]),
        )
        result = await client.get_group_details_batch(group_ids)

    assert len(result) == 25
    assert mock_http.post.call_count == 2


async def test_entra_get_group_details_batch_retries_on_429():
    client = _make_entra_client_with_token()

    throttle_resp = _json_resp(
        200,
        {
            "responses": [
                {"id": "g1", "status": 429, "headers": {"Retry-After": "0"}, "body": {}},
            ]
        },
    )
    retry_resp = _json_resp(
        200,
        {
            "responses": [
                {
                    "id": "g1",
                    "status": 200,
                    "body": {"id": "g1", "displayName": "G1", "mail": None, "description": None},
                }
            ]
        },
    )

    with patch("registry.services.group_directory_client.httpx.AsyncClient") as mock_cls:
        _mock_http_context(mock_cls, post=AsyncMock(side_effect=[throttle_resp, retry_resp]))
        with patch("registry.services.group_directory_client.asyncio.sleep", new_callable=AsyncMock):
            result = await client.get_group_details_batch(["g1"])

    assert result == [{"id": "g1", "name": "G1", "email": None, "description": None}]
