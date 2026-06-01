"""
Federation Resource Reset Script

Usage:
    uv run python scripts/reset_federation_resources.py --federation-id <id>

Required:
    --federation-id <id>   Hex ObjectId of the federation to reset.

Options:
    --apply         Actually delete. Default is dry-run.
    --yes           Skip interactive confirmation when used with --apply.
    --skip-acl      Do not touch ACL entries (leave orphan ACL behind).
    --skip-vector   Do not touch Weaviate (leave orphan vector docs behind).

Examples:
    uv run python scripts/reset_federation_resources.py --federation-id 6a0bf...
    uv run python scripts/reset_federation_resources.py --federation-id 6a0bf... --apply
"""

from __future__ import annotations

import asyncio
import logging
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from beanie import PydanticObjectId

from registry.container import RegistryContainer
from registry.core.config import Settings
from registry_pkgs.database import init_mongodb
from registry_pkgs.database.redis_client import close_redis_client, create_redis_client
from registry_pkgs.models import A2AAgent, ExtendedAclEntry, ExtendedMCPServer
from registry_pkgs.models.extended_acl_entry import ExtendedResourceType
from registry_pkgs.models.federation import Federation
from registry_pkgs.vector.client import create_database_client

logger = logging.getLogger(__name__)


@dataclass
class ResetPlan:
    """Everything the script will touch when it runs in --apply mode."""

    federation: Any
    mcp_servers: list[ExtendedMCPServer] = field(default_factory=list)
    a2a_agents: list[A2AAgent] = field(default_factory=list)
    acl_entries: list[ExtendedAclEntry] = field(default_factory=list)

    @property
    def total_resources(self) -> int:
        return len(self.mcp_servers) + len(self.a2a_agents)


async def _load_federation(federation_id: str) -> Any:
    try:
        oid = PydanticObjectId(federation_id)
    except Exception as exc:
        raise ValueError(f"--federation-id is not a valid ObjectId: {federation_id}") from exc
    fed = await Federation.get(oid)
    if fed is None:
        raise ValueError(f"federation not found: {federation_id}")
    return fed


async def collect_reset_plan(federation_id: str) -> ResetPlan:
    """Find every row tied to this federation. Pure read; no mutation."""
    federation = await _load_federation(federation_id)
    fed_id = federation.id

    mcp_servers = await ExtendedMCPServer.find({"federationRefId": fed_id}).to_list()
    a2a_agents = await A2AAgent.find({"federationRefId": fed_id}).to_list()

    mcp_ids = [s.id for s in mcp_servers if s.id is not None]
    a2a_ids = [a.id for a in a2a_agents if a.id is not None]

    acl_entries: list[ExtendedAclEntry] = []
    if mcp_ids or a2a_ids:
        acl_filters: list[dict[str, Any]] = []
        if mcp_ids:
            acl_filters.append(
                {
                    "resourceType": ExtendedResourceType.MCPSERVER.value,
                    "resourceId": {"$in": mcp_ids},
                }
            )
        if a2a_ids:
            acl_filters.append(
                {
                    "resourceType": ExtendedResourceType.REMOTE_AGENT.value,
                    "resourceId": {"$in": a2a_ids},
                }
            )
        # `$or` with a single clause works fine in Mongo, but unwrap for clarity.
        query = acl_filters[0] if len(acl_filters) == 1 else {"$or": acl_filters}
        acl_entries = await ExtendedAclEntry.find(query).to_list()

    return ResetPlan(
        federation=federation,
        mcp_servers=mcp_servers,
        a2a_agents=a2a_agents,
        acl_entries=acl_entries,
    )


@dataclass
class ApplyOutcome:
    vector_mcp_deleted: int = 0
    vector_a2a_deleted: int = 0
    acl_deleted: int = 0
    mcp_deleted: int = 0
    a2a_deleted: int = 0
    errors: list[str] = field(default_factory=list)


async def apply_reset(
    plan: ResetPlan,
    *,
    mcp_server_repo: Any,
    a2a_agent_repo: Any,
    skip_acl: bool = False,
    skip_vector: bool = False,
) -> ApplyOutcome:
    """Execute the plan. Returns a per-stage counter so callers can audit."""
    outcome = ApplyOutcome()

    # 1. Weaviate first — if Mongo delete later fails, we can still re-run.
    if not skip_vector:
        for server in plan.mcp_servers:
            sid = str(server.id) if server.id is not None else None
            if not sid:
                continue
            try:
                outcome.vector_mcp_deleted += await mcp_server_repo.delete_by_server_id(sid, server.serverName)
            except Exception as exc:
                outcome.errors.append(f"vector mcp {sid}: {exc}")

        for agent in plan.a2a_agents:
            aid = str(agent.id) if agent.id is not None else None
            if not aid:
                continue
            name = getattr(getattr(agent, "card", None), "name", None)
            try:
                outcome.vector_a2a_deleted += await a2a_agent_repo.delete_by_agent_id(aid, name)
            except Exception as exc:
                outcome.errors.append(f"vector a2a {aid}: {exc}")

    # 2. ACL referencing the doomed resources.
    if not skip_acl and plan.acl_entries:
        acl_ids = [entry.id for entry in plan.acl_entries if entry.id is not None]
        if acl_ids:
            try:
                result = await ExtendedAclEntry.find({"_id": {"$in": acl_ids}}).delete()
                outcome.acl_deleted = _coerce_deleted_count(result, fallback=len(acl_ids))
            except Exception as exc:
                outcome.errors.append(f"acl bulk delete: {exc}")

    # 3. Mongo resource docs last.
    for server in plan.mcp_servers:
        try:
            await server.delete()
            outcome.mcp_deleted += 1
        except Exception as exc:
            outcome.errors.append(f"mongo mcp {server.id}: {exc}")

    for agent in plan.a2a_agents:
        try:
            await agent.delete()
            outcome.a2a_deleted += 1
        except Exception as exc:
            outcome.errors.append(f"mongo a2a {agent.id}: {exc}")

    return outcome


def _coerce_deleted_count(result: Any, *, fallback: int) -> int:
    count = getattr(result, "deleted_count", None)
    if count is not None:
        return int(count)
    if isinstance(result, int):
        return result
    return fallback


def _print_plan(plan: ResetPlan, *, skip_acl: bool, skip_vector: bool) -> None:
    fed = plan.federation
    fed_name = getattr(fed, "displayName", None) or getattr(fed, "name", None) or "<no-name>"
    provider = getattr(fed, "providerType", None)
    provider = getattr(provider, "value", provider)
    print("\n" + "=" * 80)
    print("FEDERATION RESET PLAN")
    print("=" * 80)
    print(f"Federation: {fed.id}   ({fed_name})   provider={provider}")
    print(f"MCP servers to delete:   {len(plan.mcp_servers)}")
    print(f"A2A agents to delete:    {len(plan.a2a_agents)}")
    print(f"ACL entries to delete:   {len(plan.acl_entries)}{'  (SKIPPED)' if skip_acl else ''}")
    print(f"Weaviate cleanup:        {'SKIPPED' if skip_vector else 'enabled'}")
    print("=" * 80)

    if plan.mcp_servers:
        print("\n-- MCP servers --")
        for s in plan.mcp_servers:
            print(f"  - {s.id}  {s.serverName}  path={s.path}  author={s.author}")
    if plan.a2a_agents:
        print("\n-- A2A agents --")
        for a in plan.a2a_agents:
            name = getattr(getattr(a, "card", None), "name", None)
            print(f"  - {a.id}  {name}  path={a.path}  author={a.author}")
    if plan.acl_entries and not skip_acl:
        print("\n-- ACL entries --")
        for e in plan.acl_entries[:20]:
            print(
                f"  - {e.id}  resourceType={e.resourceType.value}  resourceId={e.resourceId}  "
                f"principal={e.principalType.value}/{e.principalId}  permBits={e.permBits}"
            )
        if len(plan.acl_entries) > 20:
            print(f"  ... and {len(plan.acl_entries) - 20} more")
    print("=" * 80)


def _print_outcome(outcome: ApplyOutcome) -> None:
    print("\n" + "=" * 80)
    print("APPLY RESULT")
    print("=" * 80)
    print(f"Weaviate MCP docs deleted: {outcome.vector_mcp_deleted}")
    print(f"Weaviate A2A docs deleted: {outcome.vector_a2a_deleted}")
    print(f"ACL entries deleted:        {outcome.acl_deleted}")
    print(f"Mongo MCP servers deleted: {outcome.mcp_deleted}")
    print(f"Mongo A2A agents deleted:  {outcome.a2a_deleted}")
    if outcome.errors:
        print(f"\nErrors ({len(outcome.errors)}):")
        for err in outcome.errors:
            print(f"  - {err}")
    print("=" * 80)


@dataclass
class CliOptions:
    federation_id: str = ""
    apply: bool = False
    assume_yes: bool = False
    skip_acl: bool = False
    skip_vector: bool = False


def parse_args(argv: list[str]) -> CliOptions:
    opts = CliOptions()
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--federation-id":
            if i + 1 >= len(argv):
                print("Error: --federation-id requires a value")
                sys.exit(1)
            opts.federation_id = argv[i + 1]
            i += 2
            continue
        if arg == "--apply":
            opts.apply = True
            i += 1
            continue
        if arg == "--yes":
            opts.assume_yes = True
            i += 1
            continue
        if arg == "--skip-acl":
            opts.skip_acl = True
            i += 1
            continue
        if arg == "--skip-vector":
            opts.skip_vector = True
            i += 1
            continue
        if arg in {"-h", "--help"}:
            print(__doc__)
            sys.exit(0)
        print(f"Error: unknown argument {arg}")
        sys.exit(1)
    if not opts.federation_id:
        print("Error: --federation-id is required")
        sys.exit(1)
    return opts


async def run(argv: list[str] | None = None) -> int:
    opts = parse_args(argv if argv is not None else sys.argv)

    env_path = Path(".env")
    settings = Settings(_env_file=str(env_path)) if env_path.exists() else Settings()

    db_client = None
    redis_client = None
    container: RegistryContainer | None = None

    try:
        await init_mongodb(settings.mongo_config)
        print("MongoDB connected.")

        if not opts.skip_vector:
            redis_client = create_redis_client(settings.redis_config)
            db_client = create_database_client(settings.vector_backend_config)
            container = RegistryContainer(
                settings=settings,
                db_client=db_client,
                redis_client=redis_client,
            )
            print(f"Vector DB connected (store={settings.vector_store_type}, provider={settings.embedding_provider}).")

        plan = await collect_reset_plan(opts.federation_id)
        _print_plan(plan, skip_acl=opts.skip_acl, skip_vector=opts.skip_vector)

        if plan.total_resources == 0 and not plan.acl_entries:
            print("Nothing to reset. Federation is already clean.")
            return 0

        if not opts.apply:
            print("\nDry-run only. Re-run with --apply to delete.")
            return 0

        if not opts.assume_yes:
            answer = input(
                f"\nDelete {plan.total_resources} resources + {len(plan.acl_entries)} ACL entries "
                f"under federation {plan.federation.id}? Type 'yes' to proceed: "
            )
            if answer.strip().lower() != "yes":
                print("Aborted.")
                return 1

        outcome = await apply_reset(
            plan,
            mcp_server_repo=container.mcp_server_repo if container else _NoopRepo(),
            a2a_agent_repo=container.a2a_agent_repo if container else _NoopRepo(),
            skip_acl=opts.skip_acl,
            skip_vector=opts.skip_vector,
        )
        _print_outcome(outcome)
        if outcome.errors:
            return 1
        return 0
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1
    except Exception as exc:
        print(f"\nFatal error: {exc}")
        traceback.print_exc()
        return 1
    finally:
        try:
            await container.shutdown()
            close_redis_client(redis_client)
            db_client.close()
        except Exception as exc:
            print(f"\nFatal error: {exc}")
            traceback.print_exc()
        print("Connection closed.")


class _NoopRepo:
    """Stand-in when --skip-vector is set: every delete is a no-op returning 0."""

    async def delete_by_server_id(self, *_a, **_kw) -> int:
        return 0

    async def delete_by_agent_id(self, *_a, **_kw) -> int:
        return 0


def cli() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    cli()
