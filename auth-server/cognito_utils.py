"""
Cognito utilities for token generation and AWS Cognito operations.
"""

import logging

import httpx

logger = logging.getLogger(__name__)


async def generate_token(
    client_id: str, client_secret: str, user_pool_id: str, region: str, scopes: list[str] = None, domain: str = None
) -> dict:
    """
    Generate a token using the client credentials flow

    Args:
        client_id: Cognito App Client ID
        client_secret: Cognito App Client Secret
        user_pool_id: Cognito User Pool ID
        region: AWS region
        scopes: List of scopes to request (optional)
        domain: Optional custom domain name (e.g., 'mcp-gateway')

    Returns:
        Dict containing access token and metadata
    """
    try:
        # Construct the Cognito domain
        if domain:
            # Use custom domain if provided
            cognito_domain = f"https://{domain}.auth.{region}.amazoncognito.com"
        else:
            # Otherwise use user pool ID without underscores (standard format)
            user_pool_id_wo_underscore = user_pool_id.replace("_", "")
            cognito_domain = f"https://{user_pool_id_wo_underscore}.auth.{region}.amazoncognito.com"

        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        data = {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret}

        if scopes:
            data["scope"] = " ".join(scopes)

        token_url = f"{cognito_domain}/oauth2/token"

        logger.info(f"Requesting token from {token_url}")
        async with httpx.AsyncClient() as client:
            response = await client.post(token_url, headers=headers, data=data, timeout=10)
            response.raise_for_status()
            token_data = response.json()

        logger.info("Successfully obtained client credentials token")
        return token_data

    except Exception as e:
        logger.error(f"Failed to get client credentials token: {e}")
        raise ValueError(f"Cannot obtain token: {e}")
