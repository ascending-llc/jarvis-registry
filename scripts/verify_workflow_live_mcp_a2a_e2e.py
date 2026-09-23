"""End-to-end verification of a real MCP + A2A workflow with HTTP-driven HITL approval.

One complex run, executed by the LIVE registry server (no mocks):

    MCP(AscendingMcpDoc) -> condition branch -> A2A direct -> HITL confirm gate -> A2A pool -> COMPLETED

The script is a thin client:
  1. self-signs a registry token (user_id + workflows-control scope),
  2. inserts the WorkflowDefinition + grants OWNER ACL directly in Mongo,
  3. triggers the run and approves the HITL gate over the real REST API,
  4. asserts real behavior by reading WorkflowRun / NodeRun documents.

Usage:
    uv run python scripts/verify_workflow_live_mcp_a2a_e2e.py
    uv run python scripts/verify_workflow_live_mcp_a2a_e2e.py --a2a-direct a2a1forfederationtesting \\
        --a2a-pool a2a1forfederationtesting a2aweatherforfederationtesting --keep-data

Env: REGISTRY_TOKEN (override), REGISTRY_URL, MONGO_URI, AWS_* + JWT_PRIVATE_KEY (via .env).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

import httpx
from beanie import PydanticObjectId
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from registry import settings  # noqa: E402
from registry.services.access_control_service import ACLService, load_role_cache  # noqa: E402
from registry.services.group_service import GroupService  # noqa: E402
from registry.utils.csrf import compute_csrf_token  # noqa: E402
from registry_pkgs.core.config import MongoConfig  # noqa: E402
from registry_pkgs.core.jwt_tokens import mint_crud_session_token  # noqa: E402
from registry_pkgs.database.mongodb import MongoDB  # noqa: E402
from registry_pkgs.models import PrincipalType  # noqa: E402
from registry_pkgs.models.a2a_agent import A2AAgent  # noqa: E402
from registry_pkgs.models.enums import RoleBits, WorkflowNodeType, WorkflowRunStatus  # noqa: E402
from registry_pkgs.models.extended_access_role import RegistryResourceType  # noqa: E402
from registry_pkgs.models.extended_acl_entry import RegistryAclEntry  # noqa: E402
from registry_pkgs.models.workflow import (  # noqa: E402
    HumanReviewSpec,
    NodeRun,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowRun,
)
from registry_pkgs.oauth.user_service import UserService  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("real_mcp_a2a_e2e")

PASS = "✅"
FAIL = "❌"
PREFIX = "__real_e2e__"
DEFAULT_POOL = ["a2a1forfederationtesting", "a2aweatherforfederationtesting"]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mcp-key", default="AscendingMcpDoc", help="MCP server name for the MCP step.")
    parser.add_argument("--a2a-direct", default="a2a1forfederationtesting", help="A2A agent key for the direct step.")
    parser.add_argument("--a2a-pool", nargs="+", default=DEFAULT_POOL, help="2-5 A2A agent keys for the pool step.")
    parser.add_argument("--prompt", default="Look up the registry documentation and summarize it briefly.")
    parser.add_argument("--registry-url", default=os.getenv("REGISTRY_URL", "http://localhost:7860"))
    parser.add_argument(
        "--model-source-id",
        default=None,
        help="Optional chat ModelSource id applied to model-backed nodes; otherwise use the gateway default.",
    )
    parser.add_argument("--keep-data", action="store_true", help="Keep created records on exit (for debugging).")
    return parser.parse_args()


def _make_token(user_id: str) -> str:
    """Mint the CRUD-session token required by state-changing registry routes."""
    if not settings.jwt_private_key:
        raise SystemExit("Set REGISTRY_TOKEN or JWT_PRIVATE_KEY in .env to authenticate against the registry.")
    scopes = (
        "workflows-read workflows-write workflows-control mcp-proxy-ops "
        "servers-read agents-read agents-write federations-read"
    )
    return mint_crud_session_token(
        settings.jwt_token_config,
        subject="real-e2e-user",
        token_type="access_token",
        expires_in_seconds=3600,
        extra_claims={
            "scope": scopes,
            "user_id": user_id,
            "username": "real-workflow-e2e",
            "groups": ["jarvis-registry-admin"],
        },
    )


def _session_headers(token: str) -> dict[str, str]:
    return {
        "Cookie": f"{settings.session_cookie_name}={token}",
        settings.csrf_header_name: compute_csrf_token(token),
    }


def _build_definition(args: argparse.Namespace) -> WorkflowDefinition:
    """Complex node tree: MCP step -> condition branch -> HITL gate -> A2A pool."""
    return WorkflowDefinition(
        name=f"{PREFIX}real-mcp-a2a",
        description="Real MCP + A2A + HITL complex e2e",
        enabled=True,
        nodes=[
            WorkflowNode(
                name="mcp-doc",
                executor_key=args.mcp_key,
                model_source_id=args.model_source_id,
                step_objective="Use the MCP server to inspect registry documentation and summarize the result.",
            ),
            WorkflowNode(
                name="branch",
                node_type=WorkflowNodeType.CONDITION,
                condition_cel="session_state.user_text != ''",
                true_steps=[
                    WorkflowNode(
                        name="a2a-direct",
                        executor_key=args.a2a_direct,
                        step_objective="Send the MCP summary to the direct A2A agent and return its response.",
                    )
                ],
                false_steps=[
                    WorkflowNode(
                        name="fallback",
                        executor_key="echo",
                        step_objective="Echo the workflow input when the direct A2A branch is not selected.",
                    )
                ],
            ),
            WorkflowNode(
                name="review-gate",
                executor_key="echo",
                human_review=HumanReviewSpec(requires_confirmation=True),
                step_objective="Pause for human confirmation before the final A2A pool step.",
            ),
            WorkflowNode(
                name="a2a-pool",
                a2a_pool=args.a2a_pool,
                model_source_id=args.model_source_id,
                step_objective="Select the best A2A agent to produce the final concise answer.",
            ),
        ],
    )


async def _grant_owner(user_id: str, workflow_id: PydanticObjectId) -> None:
    acl = ACLService(
        user_service=UserService(),
        group_service=GroupService(directory_clients={}),
        role_cache=await load_role_cache(),
    )
    await acl.grant_permission(
        principal_type=PrincipalType.USER,
        principal_id=PydanticObjectId(user_id),
        resource_type=RegistryResourceType.WORKFLOW,
        resource_id=workflow_id,
        perm_bits=RoleBits.OWNER,
    )


async def _resolve_test_user_id(agent_keys: list[str]) -> str:
    """Find an existing user with VIEW access to every requested A2A agent."""
    paths = list(dict.fromkeys(key.lstrip("/") for key in agent_keys))
    agents = await A2AAgent.find({"path": {"$in": paths}, "config.enabled": True}).to_list()
    if {agent.path for agent in agents} != set(paths):
        missing = sorted(set(paths) - {agent.path for agent in agents})
        raise SystemExit(f"Enabled A2A agents not found: {', '.join(missing)}")

    agent_ids = {agent.id for agent in agents}
    acls = await RegistryAclEntry.find(
        {
            "resourceType": RegistryResourceType.REMOTE_AGENT.value,
            "resourceId": {"$in": list(agent_ids)},
            "principalType": PrincipalType.USER.value,
            "permBits": {"$bitsAllSet": int(RoleBits.VIEWER)},
        }
    ).to_list()
    resources_by_user: dict[PydanticObjectId, set[PydanticObjectId]] = {}
    for acl in acls:
        resources_by_user.setdefault(acl.principalId, set()).add(acl.resourceId)
    for principal_id, resource_ids in resources_by_user.items():
        if agent_ids <= resource_ids:
            return str(principal_id)
    raise SystemExit("No existing user has VIEW access to every requested A2A agent")


async def _cleanup(workflow_id: PydanticObjectId, user_id: str) -> None:
    db = MongoDB.get_database()
    run_docs = [
        r async for r in db.get_collection("workflow_runs").find({"workflow_definition_id": workflow_id}, {"_id": 1})
    ]
    run_ids = [d["_id"] for d in run_docs]
    if run_ids:
        await db.get_collection("node_runs").delete_many({"workflow_run_id": {"$in": run_ids}})
        await db.get_collection("workflow_runs").delete_many({"_id": {"$in": run_ids}})
        await db.get_collection("agno_workflow_sessions").delete_many(
            {"session_id": {"$in": [str(r) for r in run_ids]}}
        )
    await db.get_collection("workflow_definitions").delete_many({"_id": workflow_id})
    await db.get_collection("aclentries").delete_many(
        {
            "principalId": PydanticObjectId(user_id),
            "resourceType": RegistryResourceType.WORKFLOW.value,
            "resourceId": workflow_id,
        }
    )


async def _poll(predicate, timeout: float, interval: float = 0.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = await predicate()
        if result:
            return result
        await asyncio.sleep(interval)
    return None


async def _wait_status(run_id: str, *targets: WorkflowRunStatus, timeout: float = 240.0) -> WorkflowRun | None:
    async def check():
        run = await WorkflowRun.get(PydanticObjectId(run_id))
        return run if run is not None and run.status in targets else None

    return await _poll(check, timeout=timeout)


async def _completed_nodes(run_id: str) -> dict[str, NodeRun]:
    """Map node_name -> NodeRun for nodes that reached COMPLETED."""
    runs = await NodeRun.find(NodeRun.workflow_run_id == PydanticObjectId(run_id)).to_list()
    return {nr.node_name: nr for nr in runs if str(nr.status) == "completed"}


def _api(registry_url: str, path: str) -> str:
    return f"{registry_url.rstrip('/')}/api/{settings.api_version}{path}"


async def _assert_results(run_id: str) -> int:
    nodes = await _completed_nodes(run_id)
    checks: list[tuple[str, bool, str]] = []

    mcp = nodes.get("mcp-doc")
    mcp_out = (mcp.output_snapshot or {}).get("content", "") if mcp else ""
    checks.append(("real MCP step completed with non-empty output", bool(mcp_out.strip()), f"output={mcp_out[:80]!r}"))

    checks.append(
        (
            "A2A direct ran (true branch), fallback skipped",
            "a2a-direct" in nodes and "fallback" not in nodes,
            f"completed={sorted(nodes)}",
        )
    )

    pool = nodes.get("a2a-pool")
    pool_output = (pool.output_snapshot or {}).get("content", "") if pool else ""
    checks.append(
        (
            "A2A pool completed with non-empty output",
            pool is not None and pool.status == "completed" and bool(pool_output),
            f"status={pool.status if pool else 'n/a'} output_len={len(pool_output)}",
        )
    )

    print()
    all_ok = True
    for name, ok, detail in checks:
        all_ok = all_ok and ok
        print(f"  {PASS if ok else FAIL}  {name} — {detail}")
    if not all_ok:
        print(f"\n{FAIL} assertions failed")
        return 1
    print(f"\n{PASS} all real MCP + A2A + HITL assertions passed")
    return 0


async def _run_lifecycle(client: httpx.AsyncClient, args: argparse.Namespace, headers: dict, wf_id: str) -> int:
    resp = await client.post(
        _api(args.registry_url, f"/workflows/{wf_id}/runs"),
        headers=headers,
        json={"initialInput": {"user_text": args.prompt}, "triggerSource": "real-e2e"},
    )
    if resp.status_code not in {200, 202}:
        print(f"{FAIL} trigger failed: HTTP {resp.status_code} {resp.text}")
        return 1
    run_id = resp.json()["runId"]
    print(f"{PASS} run triggered (run_id={run_id}) — server executing MCP + A2A direct...")

    gate = await _wait_status(
        run_id,
        WorkflowRunStatus.AWAITING_APPROVAL,
        WorkflowRunStatus.FAILED,
        WorkflowRunStatus.COMPLETED,
        timeout=240,
    )
    if gate is None or gate.status != WorkflowRunStatus.AWAITING_APPROVAL:
        print(f"{FAIL} run did not reach the HITL gate: status={gate.status if gate else 'timeout'}")
        if gate is not None and gate.error_summary:
            print(f"     error_summary: {gate.error_summary}")
        return 1
    print(f"{PASS} run paused at HITL gate (AWAITING_APPROVAL)")

    pending = (await WorkflowRun.get(PydanticObjectId(run_id))).pending_requirements
    if not pending:
        print(f"{FAIL} no pending_requirements on the paused run")
        return 1
    step_id = pending[0]["step_id"]
    resp = await client.post(
        _api(args.registry_url, f"/workflows/{wf_id}/runs/{run_id}/approve"),
        headers=headers,
        json={"stepId": step_id, "resolution": "confirm"},
    )
    if resp.status_code != 200:
        print(f"{FAIL} approve failed: HTTP {resp.status_code} {resp.text}")
        return 1
    print(f"{PASS} approved via HTTP (stepId={step_id}) — server resuming...")

    final = await _wait_status(run_id, WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED, timeout=240)
    if final is None or final.status != WorkflowRunStatus.COMPLETED:
        print(f"{FAIL} run did not complete: status={final.status if final else 'timeout'}")
        if final is not None and final.error_summary:
            print(f"     error_summary: {final.error_summary}")
        return 1
    print(f"{PASS} run COMPLETED after real HTTP approval")

    return await _assert_results(run_id)


async def amain(args: argparse.Namespace) -> int:
    await MongoDB.connect_db(
        config=MongoConfig(
            mongo_uri=os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/jarvis"),
            mongodb_username=os.getenv("MONGODB_USERNAME", ""),
            mongodb_password=os.getenv("MONGODB_PASSWORD", ""),
        ),
    )
    try:
        user_id = await _resolve_test_user_id([args.a2a_direct, *args.a2a_pool])
        token = os.getenv("REGISTRY_TOKEN") or _make_token(user_id)
        print(f"{PASS} token ready (user_id={user_id})")

        definition = _build_definition(args)
        await definition.insert()
        await _grant_owner(user_id, definition.id)
        print(f"{PASS} definition inserted + workflow ACL granted (id={definition.id})")

        try:
            headers = _session_headers(token)
            async with httpx.AsyncClient(timeout=30) as client:
                return await _run_lifecycle(client, args, headers, str(definition.id))
        finally:
            if not args.keep_data:
                await _cleanup(definition.id, user_id)
                print(f"{PASS} cleaned up {PREFIX}* records")
    finally:
        await MongoDB.close_db()


def main() -> int:
    return asyncio.run(amain(_parse_args()))


if __name__ == "__main__":
    sys.exit(main())
