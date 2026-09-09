import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from registry_pkgs.google.cloud_identity_client import CloudIdentityGroupsClient

pytestmark = pytest.mark.asyncio


def _sa_key_json() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return json.dumps(
        {
            "client_email": "sa@proj.iam.gserviceaccount.com",
            "private_key": pem,
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )


def _resp(json_data: dict, status: int = 200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data
    r.raise_for_status = MagicMock()
    return r


def _client_with_token() -> CloudIdentityGroupsClient:
    client = CloudIdentityGroupsClient(_sa_key_json())
    client._http = MagicMock()
    client._http.post = AsyncMock(return_value=_resp({"access_token": "tok", "expires_in": 3600}))
    return client


async def test_list_groups_paginates_and_maps():
    client = _client_with_token()
    page1 = {
        "memberships": [{"group": "groups/1", "groupKey": {"id": "g1@x.com"}, "displayName": "G1"}],
        "nextPageToken": "abc",
    }
    page2 = {"memberships": [{"group": "groups/2", "groupKey": {"id": "g2@x.com"}, "displayName": "G2"}]}
    client._http.get = AsyncMock(side_effect=[_resp(page1), _resp(page2)])

    groups = await client.list_transitive_groups_for_member("ada@x.com")

    assert [g.email for g in groups] == ["g1@x.com", "g2@x.com"]
    assert groups[0].resource_name == "groups/1"
    assert groups[1].display_name == "G2"
    assert client._http.get.await_count == 2
    calls = client._http.get.await_args_list
    assert calls[0].kwargs["params"]["query"] == (
        "member_key_id == 'ada@x.com' && 'cloudidentity.googleapis.com/groups.discussion_forum' in labels"
    )
    assert calls[1].kwargs["params"]["pageToken"] == "abc"


async def test_list_members_extracts_emails():
    client = _client_with_token()
    page = {
        "memberships": [
            {"preferredMemberKey": [{"id": "a@x.com"}]},
            {"preferredMemberKey": [{"id": "b@x.com"}]},
        ]
    }
    client._http.get = AsyncMock(return_value=_resp(page))

    members = await client.list_transitive_members_of_group("groups/1")

    assert members == ["a@x.com", "b@x.com"]


async def test_token_is_cached_across_calls():
    client = _client_with_token()
    client._http.get = AsyncMock(return_value=_resp({"memberships": []}))

    await client.list_transitive_groups_for_member("a@x.com")
    await client.list_transitive_members_of_group("groups/1")

    assert client._http.post.await_count == 1  # minted once, reused


async def test_token_refreshes_when_expired():
    client = _client_with_token()
    client._http.get = AsyncMock(return_value=_resp({"memberships": []}))

    await client.list_transitive_groups_for_member("a@x.com")
    client._token_expiry = 0.0  # force expiry
    await client.list_transitive_groups_for_member("a@x.com")

    assert client._http.post.await_count == 2


async def test_empty_key_construction_does_not_raise():
    # Entra-only/Cognito deployments construct the client with an empty key at startup.
    client = CloudIdentityGroupsClient("")
    assert client._client_email is None  # not parsed eagerly


async def test_empty_key_raises_on_first_use():
    client = CloudIdentityGroupsClient("")
    with pytest.raises(ValueError):
        await client.list_transitive_groups_for_member("a@x.com")


async def test_token_failure_raises():
    client = CloudIdentityGroupsClient(_sa_key_json())
    client._http = MagicMock()
    client._http.post = AsyncMock(return_value=_resp({"error": "bad"}, status=401))

    with pytest.raises(ValueError):
        await client.list_transitive_groups_for_member("a@x.com")


async def test_client_and_http_are_lazy():
    client = CloudIdentityGroupsClient("")
    assert client._http is None
    assert client._client() is client._http is not None


async def test_aclose_is_noop_when_never_used():
    client = CloudIdentityGroupsClient("")
    await client.aclose()  # must not raise
    assert client._http is None


async def test_aclose_closes_and_resets_http():
    client = CloudIdentityGroupsClient("")
    mock_http = AsyncMock()
    client._http = mock_http
    await client.aclose()
    mock_http.aclose.assert_awaited_once()
    assert client._http is None
