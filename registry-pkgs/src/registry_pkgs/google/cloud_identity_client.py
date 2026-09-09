import json
import logging
import time

import httpx
from httpx import AsyncClient
from pydantic import BaseModel

from registry_pkgs.core.jwt_utils import build_jwt_payload, encode_jwt

logger = logging.getLogger(__name__)

_CLOUD_IDENTITY_BASE_URL = "https://cloudidentity.googleapis.com/v1"
_GROUPS_DISCUSSION_FORUM_LABEL = "cloudidentity.googleapis.com/groups.discussion_forum"
_GROUPS_READONLY_SCOPE = "https://www.googleapis.com/auth/cloud-identity.groups.readonly"
_JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
_ASSERTION_TTL_SECONDS = 3600
_TOKEN_REFRESH_SKEW_SECONDS = 60


class GoogleWorkspaceGroupInfo(BaseModel):
    email: str  # groupKey.id
    display_name: str
    resource_name: str  # "groups/{id}"


class CloudIdentityGroupsClient:
    def __init__(self, service_account_key_json: str) -> None:
        self._key_json = service_account_key_json
        self._client_email: str | None = None
        self._private_key: str | None = None
        self._token_uri: str = "https://oauth2.googleapis.com/token"
        self._access_token: str | None = None
        self._token_expiry: float = 0.0
        self._http: httpx.AsyncClient | None = None

    def _client(self) -> AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _load_key(self) -> None:
        if self._client_email is not None:
            return
        if not self._key_json:
            raise ValueError("google_service_account_key_json is not configured")
        key = json.loads(self._key_json)
        self._client_email = key["client_email"]
        self._private_key = key["private_key"]
        self._token_uri = key.get("token_uri", self._token_uri)

    async def _get_token(self) -> str:
        if self._access_token and time.monotonic() < self._token_expiry - _TOKEN_REFRESH_SKEW_SECONDS:
            return self._access_token
        self._load_key()

        payload = build_jwt_payload(
            subject=self._client_email,
            issuer=self._client_email,
            audience=self._token_uri,
            expires_in_seconds=_ASSERTION_TTL_SECONDS,
            extra_claims={"scope": _GROUPS_READONLY_SCOPE},
        )
        assertion = encode_jwt(payload, self._private_key)

        resp = await self._client().post(
            self._token_uri,
            data={"grant_type": _JWT_BEARER_GRANT, "assertion": assertion},
        )
        if resp.status_code != 200:
            raise ValueError(f"Failed to acquire Cloud Identity token: {resp.status_code} {resp.text}")
        data = resp.json()
        self._access_token = data["access_token"]
        self._token_expiry = time.monotonic() + data.get("expires_in", _ASSERTION_TTL_SECONDS)
        return self._access_token

    async def _search_memberships(self, url: str, params: dict) -> list[dict]:
        """Loop `nextPageToken` until exhausted, returning all raw membership dicts."""
        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        memberships: list[dict] = []
        page_token: str | None = None

        while True:
            page_params = dict(params)
            if page_token:
                page_params["pageToken"] = page_token
            resp = await self._client().get(url, params=page_params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            memberships.extend(data.get("memberships", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                return memberships

    async def list_transitive_groups_for_member(self, member_email: str) -> list[GoogleWorkspaceGroupInfo]:
        """Paginates groups.memberships.searchTransitiveGroups via nextPageToken."""
        url = f"{_CLOUD_IDENTITY_BASE_URL}/groups/-/memberships:searchTransitiveGroups"
        escaped_email = member_email.replace("\\", "\\\\").replace("'", "\\'")
        params = {"query": f"member_key_id == '{escaped_email}' && '{_GROUPS_DISCUSSION_FORUM_LABEL}' in labels"}
        memberships = await self._search_memberships(url, params)
        return [
            GoogleWorkspaceGroupInfo(
                email=m["groupKey"]["id"],
                display_name=m.get("displayName", ""),
                resource_name=m["group"],
            )
            for m in memberships
        ]

    async def get_group(self, group_resource_name: str) -> dict:
        """groups.get — GET /v1/{name=groups/*}. Returns the raw group resource dict.

        Unlike the transitive-membership responses, this exposes the group's `description`.
        """
        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        resp = await self._client().get(f"{_CLOUD_IDENTITY_BASE_URL}/{group_resource_name}", headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def list_transitive_members_of_group(self, group_resource_name: str) -> list[str]:
        """Paginates groups.memberships.searchTransitiveMemberships via nextPageToken."""
        url = f"{_CLOUD_IDENTITY_BASE_URL}/{group_resource_name}/memberships:searchTransitiveMemberships"
        memberships = await self._search_memberships(url, params={})
        emails: list[str] = []
        for m in memberships:
            for key in m.get("preferredMemberKey", []):
                member_id = key.get("id")
                if member_id:
                    emails.append(member_id)
        return emails
