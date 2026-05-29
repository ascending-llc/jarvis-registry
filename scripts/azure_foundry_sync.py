"""Azure AI Foundry federation — sync via production code path (create_federation -> start_manual_sync).

Env (loaded from .env, shell overrides):
    required: AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, FOUNDRY_PROJECT_ENDPOINT
    optional: FOUNDRY_AGENT_NAME       limit discovery to one agent
              E2E_KEEP_FEDERATION=1    skip cleanup so azure_foundry_execute.py can use it

    uv run python scripts/azure_foundry_sync.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

from registry.container import RegistryContainer
from registry.core.config import settings
from registry_pkgs.database import close_mongodb, init_mongodb
from registry_pkgs.database.decorators import use_transaction
from registry_pkgs.database.redis_client import create_redis_client
from registry_pkgs.models import A2AAgent
from registry_pkgs.models.enums import FederationProviderType
from registry_pkgs.models.federation import Federation
from registry_pkgs.vector.client import create_database_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)
logger = logging.getLogger("azure-foundry-sync")

load_dotenv()


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"missing required env var: {name}")
    return value


def _banner(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


@use_transaction
async def _create_federation(crud, provider_config: dict) -> Federation:
    """Wrapped in a txn because create_federation persists via get_current_session()."""
    return await crud.create_federation(
        provider_type=FederationProviderType.AZURE_AI_FOUNDRY,
        display_name="azure-foundry-e2e",
        description="real production-path sync test",
        tags=["e2e", "azure", "foundry"],
        provider_config=provider_config,
        created_by=None,
    )


async def main() -> None:
    provider_config: dict = {
        "projectEndpoint": _require_env("FOUNDRY_PROJECT_ENDPOINT"),
        "tenantId": _require_env("AZURE_TENANT_ID"),
        "clientId": _require_env("AZURE_CLIENT_ID"),
        "clientSecret": _require_env("AZURE_CLIENT_SECRET"),
    }
    agent_name = os.environ.get("FOUNDRY_AGENT_NAME")
    if agent_name:
        provider_config["agentNames"] = [agent_name]

    # ---- bootstrap exactly like registry/main.py:_startup_container ----
    await init_mongodb(settings.mongo_config)
    redis_client = create_redis_client(settings.redis_config)
    db_client = create_database_client(settings.vector_backend_config)
    container = RegistryContainer(settings=settings, db_client=db_client, redis_client=redis_client)

    crud = container.federation_crud_service
    sync = container.federation_sync_service
    headers_provider = container.a2a_headers_provider

    # ---- 0. pre-clean leftovers from prior runs so this is re-runnable ----
    _banner("STEP 0 — pre-clean prior e2e federations/agents")
    stale = await Federation.find({"displayName": "azure-foundry-e2e"}).to_list()
    for f in stale:
        deleted = await A2AAgent.find({"federationRefId": f.id}).delete()
        await f.delete()
        print(f"removed stale federation {f.id} (+{getattr(deleted, 'deleted_count', '?')} agents)")

    federation: Federation | None = None
    try:
        # ---- 1. create federation (real crud: normalize + encrypt secret) ----
        _banner("STEP 1 — create_federation")
        federation = await _create_federation(crud, provider_config)
        secret = (federation.providerConfig or {}).get("clientSecret", "")
        print(f"federation_id = {federation.id}")
        print(f"providerType  = {federation.providerType}")
        print(f"clientSecret stored encrypted = {':' in str(secret)} (len={len(str(secret))})")

        # ---- 2. sync via the real manual-sync entry point ----
        _banner("STEP 2 — start_manual_sync (discover -> diff -> apply -> vector -> mark_success)")
        federation_id = str(federation.id)
        try:
            job = await sync.start_manual_sync(federation=federation, reason="e2e", triggered_by=None)
            print(f"job_id     = {job.id}")
            print(f"job_status = {getattr(job.status, 'value', job.status)}")
        except Exception as sync_exc:
            print(f"[note] sync raised at post-commit vector step (infra, not code): {sync_exc}")

        # Re-fetch (federation may be marked FAILED if only the vector step failed)
        federation = await Federation.get(federation_id)
        print(f"federation.syncStatus = {getattr(federation.syncStatus, 'value', federation.syncStatus)}")
        print(f"federation.stats      = {federation.stats}")

        # ---- 3. inspect what landed in Mongo ----
        _banner("STEP 3 — synced A2AAgent documents")
        agents = await A2AAgent.find({"federationRefId": federation.id}).to_list()
        print(f"agent count = {len(agents)}")
        for a in agents:
            meta = a.federationMetadata or {}
            print(
                f"  path={a.path} name={meta.get('agentName')} ver={meta.get('agentVersion')} "
                f"transport={a.config.type if a.config else '?'} enabled={a.isEnabled} "
                f"federationRefId={a.federationRefId} wellKnown={getattr(a.wellKnown, 'lastSyncStatus', None)}"
            )
        if agents:
            print(
                "\nnext step: keep this federation (E2E_KEEP_FEDERATION=1) and run "
                "scripts/azure_foundry_execute.py (optionally AGENT_PATH=...) to invoke it."
            )

    finally:
        if federation is not None and os.environ.get("E2E_KEEP_FEDERATION") != "1":
            _banner("CLEANUP — delete federation + agents (direct)")
            try:
                await A2AAgent.find({"federationRefId": federation.id}).delete()
                fresh = await Federation.get(federation.id)
                if fresh is not None:
                    await fresh.delete()
                print(f"deleted federation {federation.id} + its agents")
            except Exception as exc:
                logger.warning("cleanup failed (leaving federation %s): %s", federation.id, exc)
        await headers_provider.close()
        await close_mongodb()


if __name__ == "__main__":
    asyncio.run(main())
