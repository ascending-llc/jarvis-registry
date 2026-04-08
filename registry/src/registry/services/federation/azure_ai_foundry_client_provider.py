import asyncio
import logging
from typing import Any, cast

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

        credential = cast(TokenCredential, DefaultAzureCredential())
        logger.info("Initialized Azure AI Foundry client for endpoint %s", project_endpoint)
        return AIProjectClient(endpoint=project_endpoint, credential=credential)
