"""IdP group directory clients for syncing group membership into MongoDB."""

import asyncio
import logging
import time
from abc import ABC, abstractmethod

import httpx

logger = logging.getLogger(__name__)


class IdPGroupDirectoryClient(ABC):
    @abstractmethod
    async def get_user_group_ids(self, user_oid: str) -> list[str]:
        """Return transitive group GUIDs the user is a member of."""

    @abstractmethod
    async def get_group_members(self, group_oid: str) -> list[str]:
        """Return OIDs of all transitive members of the given group."""

    @abstractmethod
    async def get_group_details_batch(self, group_ids: list[str]) -> list[dict]:
        """Return name/email/description dicts for the given group GUIDs."""


class CognitoGroupDirectoryClient(IdPGroupDirectoryClient):
    async def get_user_group_ids(self, user_oid: str) -> list[str]:
        logger.warning("IdP group sync is not supported for cognito; group-based ACLs will not reflect IdP membership.")
        return []

    async def get_group_members(self, group_oid: str) -> list[str]:
        logger.warning("IdP group sync is not supported for cognito; group-based ACLs will not reflect IdP membership.")
        return []

    async def get_group_details_batch(self, group_ids: list[str]) -> list[dict]:
        logger.warning("IdP group sync is not supported for cognito; group-based ACLs will not reflect IdP membership.")
        return []


class KeycloakGroupDirectoryClient(IdPGroupDirectoryClient):
    async def get_user_group_ids(self, user_oid: str) -> list[str]:
        logger.warning(
            "IdP group sync is not supported for keycloak; group-based ACLs will not reflect IdP membership."
        )
        return []

    async def get_group_members(self, group_oid: str) -> list[str]:
        logger.warning(
            "IdP group sync is not supported for keycloak; group-based ACLs will not reflect IdP membership."
        )
        return []

    async def get_group_details_batch(self, group_ids: list[str]) -> list[dict]:
        logger.warning(
            "IdP group sync is not supported for keycloak; group-based ACLs will not reflect IdP membership."
        )
        return []


class EntraIdGroupDirectoryClient(IdPGroupDirectoryClient):
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        graph_url: str = "https://graph.microsoft.com",
    ) -> None:
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._graph_url = graph_url.rstrip("/")
        self._access_token: str | None = None
        self._token_expiry: float = 0.0

    async def _get_token(self) -> str:
        if self._access_token and time.monotonic() < self._token_expiry - 60:
            return self._access_token
        url = f"https://login.microsoftonline.com/{self._tenant_id}/oauth2/v2.0/token"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
        if resp.status_code != 200:
            raise ValueError(f"Failed to acquire Graph API token: {resp.status_code} {resp.text}")
        data = resp.json()
        self._access_token = data["access_token"]
        self._token_expiry = time.monotonic() + data.get("expires_in", 3600)
        return self._access_token

    async def get_user_group_ids(self, user_oid: str) -> list[str]:
        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self._graph_url}/v1.0/users/{user_oid}/getMemberGroups",
                json={"securityEnabledOnly": False},
                headers=headers,
            )
        if resp.status_code == 404:
            logger.warning("Graph API: user %s not found, returning empty group list.", user_oid)
            return []
        resp.raise_for_status()
        return resp.json().get("value", [])

    async def get_group_members(self, group_oid: str) -> list[str]:
        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        url: str | None = f"{self._graph_url}/v1.0/groups/{group_oid}/transitiveMembers?$select=id&$top=999"
        members: list[str] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            while url:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                for item in data.get("value", []):
                    if item.get("@odata.type") == "#microsoft.graph.user":
                        members.append(item["id"])
                url = data.get("@odata.nextLink")

        return members

    async def get_group_details_batch(self, group_ids: list[str]) -> list[dict]:
        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        results: list[dict] = []
        chunk_size = 20

        async with httpx.AsyncClient(timeout=30.0) as client:
            for i in range(0, len(group_ids), chunk_size):
                chunk = group_ids[i : i + chunk_size]
                requests_payload = [
                    {
                        "id": gid,
                        "method": "GET",
                        "url": f"/groups/{gid}?$select=id,displayName,mail,description",
                    }
                    for gid in chunk
                ]
                resp = await client.post(
                    f"{self._graph_url}/v1.0/$batch",
                    json={"requests": requests_payload},
                    headers=headers,
                )
                resp.raise_for_status()
                throttled: list[dict] = []
                retry_delay = 1

                for sub in resp.json().get("responses", []):
                    if sub.get("status") == 429:
                        retry_delay = int((sub.get("headers") or {}).get("Retry-After", 1))
                        throttled.append(
                            {
                                "id": sub["id"],
                                "method": "GET",
                                "url": f"/groups/{sub['id']}?$select=id,displayName,mail,description",
                            }
                        )
                    elif sub.get("status") == 200:
                        body = sub["body"]
                        results.append(
                            {
                                "id": body.get("id"),
                                "name": body.get("displayName"),
                                "email": body.get("mail"),
                                "description": body.get("description"),
                            }
                        )

                if throttled:
                    await asyncio.sleep(retry_delay)
                    retry_resp = await client.post(
                        f"{self._graph_url}/v1.0/$batch",
                        json={"requests": throttled},
                        headers=headers,
                    )
                    retry_resp.raise_for_status()
                    for sub in retry_resp.json().get("responses", []):
                        if sub.get("status") == 200:
                            body = sub["body"]
                            results.append(
                                {
                                    "id": body.get("id"),
                                    "name": body.get("displayName"),
                                    "email": body.get("mail"),
                                    "description": body.get("description"),
                                }
                            )

        return results
