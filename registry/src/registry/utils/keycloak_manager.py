"""
Keycloak group management utilities.

This module provides functions to manage groups in Keycloak via the Admin REST API.
It handles authentication, group CRUD operations, and integrates with the registry.
"""

import logging
from typing import Any

import httpx

from ..core.config import settings

logger = logging.getLogger(__name__)

KEYCLOAK_ADMIN_URL = settings.keycloak_url
KEYCLOAK_REALM = settings.keycloak_realm
KEYCLOAK_ADMIN = settings.keycloak_admin
KEYCLOAK_ADMIN_PASSWORD = settings.keycloak_admin_password


class KeycloakAdminError(RuntimeError):
    """Raised when Keycloak admin API operations fail."""


async def _get_keycloak_admin_token() -> str:
    """
    Get admin access token from Keycloak for Admin API calls.

    Returns:
        Admin access token string

    Raises:
        Exception: If authentication fails
    """
    if not KEYCLOAK_ADMIN_PASSWORD:
        raise Exception("KEYCLOAK_ADMIN_PASSWORD environment variable not set")

    token_url = f"{KEYCLOAK_ADMIN_URL}/realms/master/protocol/openid-connect/token"

    data = {
        "username": KEYCLOAK_ADMIN,
        "password": KEYCLOAK_ADMIN_PASSWORD,
        "grant_type": "password",
        "client_id": "admin-cli",
    }

    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(token_url, data=data, headers=headers)
            response.raise_for_status()

            token_data = response.json()
            access_token = token_data.get("access_token")

            if not access_token:
                raise Exception("No access token in Keycloak response")

            logger.info("Successfully obtained Keycloak admin token")
            return access_token

    except httpx.HTTPStatusError as e:
        logger.error(f"Failed to authenticate with Keycloak: HTTP {e.response.status_code}")
        raise Exception(f"Keycloak authentication failed: HTTP {e.response.status_code}") from e
    except Exception as e:
        logger.error(f"Error getting Keycloak admin token: {e}")
        raise Exception(f"Failed to authenticate with Keycloak: {e}") from e


def _auth_headers(token: str, content_type: str | None = "application/json") -> dict[str, str]:
    """Build auth headers for Keycloak admin API."""
    headers = {"Authorization": f"Bearer {token}"}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


async def _get_group_name_map(
    client: httpx.AsyncClient,
    token: str,
) -> dict[str, str]:
    """Return mapping of Keycloak group name to ID."""
    groups_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/groups"
    response = await client.get(groups_url, headers=_auth_headers(token, None))
    response.raise_for_status()
    groups = response.json()
    return {group.get("name"): group.get("id") for group in groups if group.get("id")}


async def _find_client_uuid(
    client: httpx.AsyncClient,
    token: str,
    client_id: str,
) -> str | None:
    """Look up a client UUID by clientId."""
    clients_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/clients"
    response = await client.get(
        clients_url,
        headers=_auth_headers(token, None),
        params={"clientId": client_id},
    )
    response.raise_for_status()
    clients = response.json()
    if clients:
        return clients[0].get("id")
    return None


def _extract_resource_id(location_header: str | None) -> str | None:
    """Extract trailing resource ID from a Location header."""
    if not location_header:
        return None
    return location_header.rstrip("/").split("/")[-1]


async def create_keycloak_group(group_name: str, description: str = "") -> dict[str, Any]:
    """
    Create a group in Keycloak.

    Args:
        group_name: Name of the group to create
        description: Optional description for the group

    Returns:
        Dict containing group information including ID

    Raises:
        Exception: If group creation fails
    """
    logger.info(f"Creating Keycloak group: {group_name}")

    try:
        # Get admin token
        admin_token = await _get_keycloak_admin_token()

        # Prepare group data
        group_data = {"name": group_name, "attributes": {"description": [description] if description else []}}

        # Create group via Admin API
        groups_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/groups"
        headers = {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(groups_url, json=group_data, headers=headers)

            if response.status_code == 201:
                logger.info(f"Successfully created Keycloak group: {group_name}")

                # Get the created group's details
                group_info = await get_keycloak_group(group_name)
                return group_info

            elif response.status_code == 409:
                logger.warning(f"Group already exists in Keycloak: {group_name}")
                raise Exception(f"Group '{group_name}' already exists in Keycloak")

            else:
                logger.error(f"Failed to create group: HTTP {response.status_code} - {response.text}")
                raise Exception(f"Failed to create group in Keycloak: HTTP {response.status_code}")

    except Exception as e:
        logger.error(f"Error creating Keycloak group '{group_name}': {e}")
        raise


async def delete_keycloak_group(group_name: str) -> bool:
    """
    Delete a group from Keycloak.

    Args:
        group_name: Name of the group to delete

    Returns:
        True if successful

    Raises:
        Exception: If group deletion fails
    """
    logger.info(f"Deleting Keycloak group: {group_name}")

    try:
        # Get admin token
        admin_token = await _get_keycloak_admin_token()

        # First, get the group ID
        group_info = await get_keycloak_group(group_name)
        group_id = group_info.get("id")

        if not group_id:
            raise Exception(f"Group '{group_name}' not found in Keycloak")

        # Delete group via Admin API
        delete_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/groups/{group_id}"
        headers = {"Authorization": f"Bearer {admin_token}"}

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.delete(delete_url, headers=headers)

            if response.status_code == 204:
                logger.info(f"Successfully deleted Keycloak group: {group_name}")
                return True

            elif response.status_code == 404:
                logger.warning(f"Group not found in Keycloak: {group_name}")
                raise Exception(f"Group '{group_name}' not found in Keycloak")

            else:
                logger.error(f"Failed to delete group: HTTP {response.status_code} - {response.text}")
                raise Exception(f"Failed to delete group from Keycloak: HTTP {response.status_code}")

    except Exception as e:
        logger.error(f"Error deleting Keycloak group '{group_name}': {e}")
        raise


async def get_keycloak_group(group_name: str) -> dict[str, Any]:
    """
    Get a group's details from Keycloak by name.

    Args:
        group_name: Name of the group to retrieve

    Returns:
        Dict containing group information (id, name, path, attributes, etc.)

    Raises:
        Exception: If group retrieval fails or group not found
    """
    logger.info(f"Getting Keycloak group: {group_name}")

    try:
        # Get admin token
        admin_token = await _get_keycloak_admin_token()

        # List all groups and find the one with matching name
        groups_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/groups"
        headers = {"Authorization": f"Bearer {admin_token}"}

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(groups_url, headers=headers)
            response.raise_for_status()

            groups = response.json()

            # Find group by name
            for group in groups:
                if group.get("name") == group_name:
                    logger.info(f"Found group: {group_name} with ID: {group.get('id')}")
                    return group

            # Group not found
            raise Exception(f"Group '{group_name}' not found in Keycloak")

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error getting group: {e.response.status_code}")
        raise Exception(f"Failed to get group from Keycloak: HTTP {e.response.status_code}") from e
    except Exception as e:
        logger.error(f"Error getting Keycloak group '{group_name}': {e}")
        raise


async def list_keycloak_groups() -> list[dict[str, Any]]:
    """
    List all groups in Keycloak realm.

    Returns:
        List of dicts containing group information

    Raises:
        Exception: If listing groups fails
    """
    logger.info("Listing all Keycloak groups")

    try:
        # Get admin token
        admin_token = await _get_keycloak_admin_token()

        # List all groups
        groups_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/groups"
        headers = {"Authorization": f"Bearer {admin_token}"}

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(groups_url, headers=headers)
            response.raise_for_status()

            groups = response.json()
            logger.info(f"Retrieved {len(groups)} groups from Keycloak")

            return groups

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error listing groups: {e.response.status_code}")
        raise Exception(f"Failed to list groups from Keycloak: HTTP {e.response.status_code}") from e
    except Exception as e:
        logger.error(f"Error listing Keycloak groups: {e}")
        raise


async def group_exists_in_keycloak(group_name: str) -> bool:
    """
    Check if a group exists in Keycloak.

    Args:
        group_name: Name of the group to check

    Returns:
        True if group exists, False otherwise
    """
    try:
        await get_keycloak_group(group_name)
        return True
    except Exception:
        return False


def _normalize_group_list(groups: list[str]) -> list[str]:
    """Clean and validate incoming group list."""
    normalized = [group.strip() for group in groups if group and group.strip()]
    if not normalized:
        raise KeycloakAdminError("At least one group must be provided")
    return normalized


async def _assign_user_to_groups_by_name(
    client: httpx.AsyncClient,
    token: str,
    user_id: str,
    groups: list[str],
) -> None:
    """Assign a Keycloak user/service account to a set of groups."""
    if not groups:
        return

    name_map = await _get_group_name_map(client, token)
    for group_name in groups:
        group_id = name_map.get(group_name)
        if not group_id:
            raise KeycloakAdminError(f"Group '{group_name}' not found in Keycloak")

        assign_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/users/{user_id}/groups/{group_id}"
        response = await client.put(assign_url, headers=_auth_headers(token, None))
        if response.status_code not in (204, 409):
            logger.error("Failed assigning user %s to group %s: %s", user_id, group_name, response.text)
            raise KeycloakAdminError(f"Failed to assign group '{group_name}' (HTTP {response.status_code})")


async def _get_user_groups(
    client: httpx.AsyncClient,
    token: str,
    user_id: str,
) -> list[str]:
    """Fetch group names for a given Keycloak user."""
    groups_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/users/{user_id}/groups"
    response = await client.get(groups_url, headers=_auth_headers(token, None))
    response.raise_for_status()
    groups = response.json()
    return [group.get("name") for group in groups if group.get("name")]


async def _get_user_by_username(
    client: httpx.AsyncClient,
    token: str,
    username: str,
) -> dict[str, Any] | None:
    """Look up a user in Keycloak by username."""
    users_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/users"
    response = await client.get(
        users_url,
        headers=_auth_headers(token, None),
        params={"username": username},
    )
    response.raise_for_status()
    matches = response.json()
    for user in matches:
        if user.get("username") == username:
            return user
    return None


async def _get_user_by_id(
    client: httpx.AsyncClient,
    token: str,
    user_id: str,
) -> dict[str, Any]:
    """Fetch a user document by ID."""
    user_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/users/{user_id}"
    response = await client.get(user_url, headers=_auth_headers(token, None))
    response.raise_for_status()
    return response.json()


async def _ensure_client(
    client: httpx.AsyncClient,
    token: str,
    client_id: str,
    description: str | None,
) -> str:
    """Create the client if it does not yet exist and return UUID."""
    existing_uuid = await _find_client_uuid(client, token, client_id)
    if existing_uuid:
        return existing_uuid

    payload = {
        "clientId": client_id,
        "name": client_id,
        "description": description or f"Service account for {client_id}",
        "enabled": True,
        "clientAuthenticatorType": "client-secret",
        "serviceAccountsEnabled": True,
        "standardFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "publicClient": False,
        "bearerOnly": False,
        "protocol": "openid-connect",
    }

    clients_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/clients"
    response = await client.post(clients_url, headers=_auth_headers(token), json=payload)
    if response.status_code not in (201, 204):
        logger.error("Failed to create client %s: %s", client_id, response.text)
        raise KeycloakAdminError(f"Failed to create service account client '{client_id}' (HTTP {response.status_code})")

    created_id = _extract_resource_id(response.headers.get("Location"))
    if created_id:
        return created_id

    client_uuid = await _find_client_uuid(client, token, client_id)
    if not client_uuid:
        raise KeycloakAdminError(f"Unable to resolve client ID for '{client_id}' after creation")
    return client_uuid


async def _ensure_groups_mapper(
    client: httpx.AsyncClient,
    token: str,
    client_uuid: str,
) -> None:
    """Ensure the standard groups protocol mapper exists for the client."""
    mapper_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/clients/{client_uuid}/protocol-mappers/models"
    response = await client.get(mapper_url, headers=_auth_headers(token, None))
    response.raise_for_status()

    mappers = response.json()
    if any(mapper.get("name") == "groups" for mapper in mappers):
        return

    mapper_payload = {
        "name": "groups",
        "protocol": "openid-connect",
        "protocolMapper": "oidc-group-membership-mapper",
        "consentRequired": False,
        "config": {
            "full.path": "false",
            "id.token.claim": "true",
            "access.token.claim": "true",
            "claim.name": "groups",
            "userinfo.token.claim": "true",
        },
    }

    create_response = await client.post(mapper_url, headers=_auth_headers(token), json=mapper_payload)
    if create_response.status_code not in (201, 409):
        logger.error(
            "Failed to create groups mapper for client %s: %s",
            client_uuid,
            create_response.text,
        )
        raise KeycloakAdminError(f"Failed to create groups mapper (HTTP {create_response.status_code})")


async def _get_service_account_user_id(
    client: httpx.AsyncClient,
    token: str,
    client_uuid: str,
) -> str:
    """Return the user ID of the service account backing a client."""
    sa_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/clients/{client_uuid}/service-account-user"
    response = await client.get(sa_url, headers=_auth_headers(token, None))
    response.raise_for_status()
    data = response.json()
    user_id = data.get("id")
    if not user_id:
        raise KeycloakAdminError("Unable to determine service account user ID")
    return user_id


async def _get_client_secret_value(
    client: httpx.AsyncClient,
    token: str,
    client_uuid: str,
) -> str:
    """Fetch the client secret value for the specified client."""
    secret_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/clients/{client_uuid}/client-secret"
    response = await client.get(secret_url, headers=_auth_headers(token, None))
    response.raise_for_status()
    data = response.json()
    secret_value = data.get("value")
    if not secret_value:
        raise KeycloakAdminError("Keycloak did not return a client secret value")
    return secret_value


async def _set_initial_password(
    client: httpx.AsyncClient,
    token: str,
    user_id: str,
    password: str,
    temporary: bool = False,
) -> None:
    """Set the initial password for a created user."""
    password_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/users/{user_id}/reset-password"
    payload = {
        "type": "password",
        "value": password,
        "temporary": temporary,
    }
    response = await client.put(password_url, headers=_auth_headers(token), json=payload)
    if response.status_code != 204:
        logger.error("Failed to set initial password for user %s: %s", user_id, response.text)
        raise KeycloakAdminError(f"Failed to set password (HTTP {response.status_code})")


async def create_service_account_client(
    client_id: str,
    group_names: list[str],
    description: str | None = None,
) -> dict[str, Any]:
    """
    Create or update a service account client with group assignments.

    Returns:
        Dict with client_id, client_uuid, service_account_user_id, client_secret, and groups.
    """
    normalized_groups = _normalize_group_list(group_names)
    admin_token = await _get_keycloak_admin_token()

    async with httpx.AsyncClient(timeout=10.0) as client:
        client_uuid = await _ensure_client(client, admin_token, client_id, description)
        await _ensure_groups_mapper(client, admin_token, client_uuid)
        service_account_user_id = await _get_service_account_user_id(client, admin_token, client_uuid)
        await _assign_user_to_groups_by_name(client, admin_token, service_account_user_id, normalized_groups)
        client_secret = await _get_client_secret_value(client, admin_token, client_uuid)

    logger.info("Configured service account client '%s' with groups: %s", client_id, normalized_groups)
    return {
        "client_id": client_id,
        "client_uuid": client_uuid,
        "service_account_user_id": service_account_user_id,
        "client_secret": client_secret,
        "groups": normalized_groups,
    }


async def create_human_user_account(
    username: str,
    email: str,
    first_name: str,
    last_name: str,
    groups: list[str],
    password: str | None = None,
) -> dict[str, Any]:
    """
    Create a human Keycloak user and assign groups.
    """
    normalized_groups = _normalize_group_list(groups)
    admin_token = await _get_keycloak_admin_token()

    async with httpx.AsyncClient(timeout=10.0) as client:
        existing = await _get_user_by_username(client, admin_token, username)
        if existing:
            raise KeycloakAdminError(f"User '{username}' already exists")

        user_payload = {
            "username": username,
            "email": email,
            "firstName": first_name,
            "lastName": last_name,
            "enabled": True,
            "emailVerified": False,
        }

        users_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/users"
        response = await client.post(users_url, headers=_auth_headers(admin_token), json=user_payload)
        if response.status_code not in (201, 204):
            logger.error("Failed to create user %s: %s", username, response.text)
            raise KeycloakAdminError(f"Failed to create user '{username}' (HTTP {response.status_code})")

        created_id = _extract_resource_id(response.headers.get("Location"))
        if not created_id:
            new_user = await _get_user_by_username(client, admin_token, username)
            if not new_user:
                raise KeycloakAdminError(f"Unable to resolve new user ID for '{username}'")
            created_id = new_user.get("id")

        if password:
            await _set_initial_password(client, admin_token, created_id, password)

        await _assign_user_to_groups_by_name(client, admin_token, created_id, normalized_groups)
        user_doc = await _get_user_by_id(client, admin_token, created_id)
        user_doc["groups"] = normalized_groups

    logger.info("Created Keycloak user '%s' with groups: %s", username, normalized_groups)
    return user_doc


async def delete_keycloak_user(username: str) -> bool:
    """
    Delete a Keycloak user or M2M service account by username.

    This function handles both:
    - Human users: deleted via the users endpoint
    - M2M service accounts: deleted via the clients endpoint (they are Keycloak clients)
    """
    admin_token = await _get_keycloak_admin_token()

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Try to find as a regular user first
        user = await _get_user_by_username(client, admin_token, username)
        if user:
            # It's a human user - delete via users endpoint
            user_id = user.get("id")
            delete_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/users/{user_id}"
            response = await client.delete(delete_url, headers=_auth_headers(admin_token, None))
            if response.status_code != 204:
                logger.error("Failed to delete user %s: %s", username, response.text)
                raise KeycloakAdminError(f"Failed to delete user '{username}' (HTTP {response.status_code})")
            logger.info("Deleted Keycloak user '%s'", username)
            return True

        # Not found as user - try to find as a client (M2M service account)
        client_uuid = await _find_client_uuid(client, admin_token, username)
        if client_uuid:
            # It's an M2M service account - delete via clients endpoint
            delete_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/clients/{client_uuid}"
            response = await client.delete(delete_url, headers=_auth_headers(admin_token, None))
            if response.status_code != 204:
                logger.error("Failed to delete M2M client %s: %s", username, response.text)
                raise KeycloakAdminError(f"Failed to delete M2M client '{username}' (HTTP {response.status_code})")
            logger.info("Deleted Keycloak M2M service account (client) '%s'", username)
            return True

        # Not found as either user or client
        raise KeycloakAdminError(f"User or M2M account '{username}' not found")


async def list_keycloak_users(
    search: str | None = None,
    max_results: int = 500,
    include_groups: bool = True,
) -> list[dict[str, Any]]:
    """
    List users in the Keycloak realm.

    This includes both:
    - Human users (regular Keycloak users)
    - M2M service accounts (service account clients)

    M2M accounts are returned with their clientId as the username and are marked
    with serviceAccountsEnabled=True for identification.
    """
    admin_token = await _get_keycloak_admin_token()

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Fetch human users
        params: dict[str, Any] = {"max": max_results}
        if search:
            params["search"] = search
        users_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/users"
        response = await client.get(users_url, headers=_auth_headers(admin_token, None), params=params)
        response.raise_for_status()
        users = response.json()

        # Fetch M2M service account clients
        clients_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/clients"
        response = await client.get(clients_url, headers=_auth_headers(admin_token, None))
        response.raise_for_status()
        all_clients = response.json()

        # Filter to only service account clients and convert to user-like format
        service_accounts = []
        for keycloak_client in all_clients:
            if not keycloak_client.get("serviceAccountsEnabled"):
                continue

            client_id = keycloak_client.get("clientId", "")
            # Apply search filter if specified
            if search and search.lower() not in client_id.lower():
                continue

            # Get the service account user to retrieve groups
            service_account_user_id = None
            groups = []
            if include_groups:
                try:
                    sa_url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/clients/{keycloak_client['id']}/service-account-user"
                    sa_response = await client.get(sa_url, headers=_auth_headers(admin_token, None))
                    if sa_response.status_code == 200:
                        sa_user = sa_response.json()
                        service_account_user_id = sa_user.get("id")
                        if service_account_user_id:
                            groups = await _get_user_groups(client, admin_token, service_account_user_id)
                except Exception as e:
                    logger.warning("Failed to get groups for M2M account %s: %s", client_id, e)

            # Format M2M account as a user entry
            service_account_entry = {
                "id": keycloak_client.get("id", ""),
                "username": client_id,
                "enabled": keycloak_client.get("enabled", True),
                "serviceAccountsEnabled": True,  # Mark as M2M account
                "firstName": "M2M",
                "lastName": "Service Account",
                "email": f"{client_id}@service-account.local",
                "groups": groups,
            }
            service_accounts.append(service_account_entry)

        # Add groups to human users if requested
        if include_groups:
            for user in users:
                user_id = user.get("id")
                if not user_id:
                    user["groups"] = []
                    continue
                user["groups"] = await _get_user_groups(client, admin_token, user_id)

        # Combine human users and M2M service accounts
        all_users = users + service_accounts

        # Apply max_results limit to combined list
        return all_users[:max_results]
