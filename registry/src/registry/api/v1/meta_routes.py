import logging

import httpx
from fastapi import APIRouter

from ...core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


async def get_oauth2_providers():
    """Fetch available OAuth2 providers from auth server"""
    try:
        async with httpx.AsyncClient() as client:
            logger.info(f"Fetching OAuth2 providers from {settings.auth_server_url}/oauth2/providers")
            response = await client.get(f"{settings.auth_server_url}/oauth2/providers", timeout=5.0)
            logger.info(f"OAuth2 providers response: status={response.status_code}")
            if response.status_code == 200:
                data = response.json()
                providers = data.get("providers", [])
                logger.info(f"Successfully fetched {len(providers)} OAuth2 providers: {providers}")
                return providers
            else:
                logger.warning(f"Auth server returned non-200 status: {response.status_code}, body: {response.text}")
    except Exception as e:
        logger.warning(f"Failed to fetch OAuth2 providers from auth server: {e}", exc_info=True)
    return []


@router.get("/providers")
async def get_providers_api():
    """API endpoint to get available OAuth2 providers for React frontend"""
    providers = await get_oauth2_providers()
    return {"providers": providers}


@router.get("/config")
async def get_auth_config():
    """API endpoint to get auth configuration for React frontend"""
    return {"auth_server_url": settings.auth_server_external_url}
