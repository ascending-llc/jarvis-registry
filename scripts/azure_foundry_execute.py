"""Azure AI Foundry federation — invoke an already-synced A2AAgent via call_a2a.

Run `azure_foundry_sync.py` with E2E_KEEP_FEDERATION=1 first.

Env (loaded from .env, shell overrides):
    AGENT_PATH           pick a specific agent; default: first Foundry agent in Mongo
    FOUNDRY_TEST_PROMPT  prompt to send; default: greeting

    uv run python scripts/azure_foundry_execute.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from registry.container import RegistryContainer
from registry.core.config import settings
from registry_pkgs.database import close_mongodb, init_mongodb
from registry_pkgs.database.redis_client import create_redis_client
from registry_pkgs.models import A2AAgent
from registry_pkgs.models.enums import FederationProviderType
from registry_pkgs.vector.client import create_database_client
from registry_pkgs.workflows.a2a_client import call_a2a

logging.basicConfig(level=logging.INFO, format="%(asctime)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s")

load_dotenv()


async def main() -> None:
    prompt = os.environ.get("FOUNDRY_TEST_PROMPT", "Hello, what can you do?")
    target_path = os.environ.get("AGENT_PATH")

    await init_mongodb(settings.mongo_config)
    redis_client = create_redis_client(settings.redis_config)
    db_client = create_database_client(settings.vector_backend_config)
    container = RegistryContainer(settings=settings, db_client=db_client, redis_client=redis_client)
    headers_provider = container.a2a_headers_provider

    try:
        if target_path:
            agent = await A2AAgent.find_one(A2AAgent.path == target_path)
        else:
            agent = await A2AAgent.find_one(
                {"federationMetadata.providerType": FederationProviderType.AZURE_AI_FOUNDRY.value}
            )

        if agent is None:
            print("no persisted Azure A2A agent found — run a sync that keeps it (E2E_KEEP_FEDERATION=1) first")
            sys.exit(1)

        print("=" * 70)
        print("READ agent from Mongo")
        print("=" * 70)
        print(f"path            = {agent.path}")
        print(f"name            = {agent.card.name}")
        print(f"transport       = {agent.config.type if agent.config else '?'}")
        print(f"url             = {agent.card.url}")
        print(f"federationRefId = {agent.federationRefId}")
        print(f"providerType    = {(agent.federationMetadata or {}).get('providerType')}")

        print("=" * 70)
        print(f"EXECUTE via A2aHeadersProvider + call_a2a  prompt={prompt!r}")
        print("=" * 70)
        result = await call_a2a(
            agent,
            prompt,
            jwt_config=settings.jwt_signing_config,
            headers_provider=headers_provider,
        )
        print(f"success    = {result.success}")
        print(f"error      = {result.error}")
        print(f"task_state = {getattr(result.task_state, 'value', result.task_state)}")
        print("response text:\n" + (result.render_text() or "<empty>"))
    finally:
        await close_mongodb()


if __name__ == "__main__":
    asyncio.run(main())
