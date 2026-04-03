import asyncio
import logging
import os
from typing import Any, cast

from registry.core.config import settings

logger = logging.getLogger(__name__)


class AzureAIFoundryClientProvider:
    """Centralized factory/cache for Azure AI Foundry project clients."""

    def __init__(self):
        self._clients: dict[str, Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get_client(self, project_endpoint: str) -> Any:
        normalized_endpoint = project_endpoint.rstrip("/")
        cached = self._clients.get(normalized_endpoint)
        if cached is not None:
            return cached

        lock = self._locks.setdefault(normalized_endpoint, asyncio.Lock())
        async with lock:
            cached = self._clients.get(normalized_endpoint)
            if cached is not None:
                return cached
            client = await asyncio.to_thread(self._create_client, normalized_endpoint)
            self._clients[normalized_endpoint] = client
            return client

    def _create_client(self, project_endpoint: str) -> Any:
        try:
            from azure.ai.projects import AIProjectClient
            from azure.core.credentials import TokenCredential
            from azure.identity import DefaultAzureCredential
        except ImportError as exc:
            raise RuntimeError(
                "Azure AI Foundry dependencies are not installed. Install azure-ai-projects and azure-identity."
            ) from exc

        self._hydrate_credential_environment_from_settings()
        credential = cast(
            TokenCredential,
            DefaultAzureCredential(
                managed_identity_client_id=settings.azure_client_id or None,
            ),
        )
        logger.info("Initialized Azure AI Foundry client for endpoint %s", project_endpoint)
        return AIProjectClient(endpoint=project_endpoint, credential=credential)

    @staticmethod
    def _hydrate_credential_environment_from_settings() -> None:
        """Bridge pydantic-loaded settings into Azure SDK's env credential chain.

        DefaultAzureCredential uses EnvironmentCredential for service principal auth,
        but that credential only reads process environment variables. When our app
        loads AZURE_* values from `.env` via pydantic-settings, those values may not
        exist in `os.environ`. We populate missing env vars here without overriding
        explicit process-level configuration.
        """

        env_defaults = {
            "AZURE_CLIENT_ID": settings.azure_client_id,
            "AZURE_CLIENT_SECRET": settings.azure_client_secret,
            "AZURE_TENANT_ID": settings.azure_tenant_id,
        }
        for env_key, value in env_defaults.items():
            if value:
                os.environ.setdefault(env_key, value)
