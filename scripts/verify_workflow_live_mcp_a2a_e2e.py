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
import json
import logging
import os
import sys
import time
import urllib.request
from pathlib import Path

import httpx
from beanie import PydanticObjectId
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from registry import settings  # noqa: E402
from registry.services.access_control_service import ACLService, load_role_cache  # noqa: E402
from registry.services.group_service import GroupService  # noqa: E402
from registry.services.user_service import UserService  # noqa: E402
from registry_pkgs.core.config import MongoConfig  # noqa: E402
from registry_pkgs.core.jwt_utils import build_jwt_payload, encode_jwt  # noqa: E402
from registry_pkgs.database.mongodb import MongoDB  # noqa: E402
from registry_pkgs.models import PrincipalType  # noqa: E402
from registry_pkgs.models.a2a_agent import A2AAgent  # noqa: E402
from registry_pkgs.models.enums import RoleBits, WorkflowNodeType, WorkflowRunStatus  # noqa: E402
from registry_pkgs.models.extended_access_role import RegistryResourceType  # noqa: E402
from registry_pkgs.models.workflow import (  # noqa: E402
    HumanReviewSpec,
    NodeRun,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowRun,
)

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
    parser.add_argument("--keep-data", action="store_true", help="Keep created records on exit (for debugging).")
    return parser.parse_args()


def _detect_jwt_issuer(registry_url: str, fallback: str) -> str:
    """Resolve the JWT issuer the registry actually validates against."""
    try:
        with urllib.request.urlopen(f"{registry_url.rstrip('/')}/api/auth/config", timeout=3) as resp:  # noqa: S310  # nosec B310
            auth_server_url = json.loads(resp.read()).get("auth_server_url", "").rstrip("/")
        if not auth_server_url:
            return fallback
        with urllib.request.urlopen(f"{auth_server_url}/.well-known/openid-configuration", timeout=3) as resp:  # noqa: S310  # nosec B310
            return json.loads(resp.read()).get("issuer", fallback)
    except Exception as exc:
        logger.warning("issuer detection failed, using fallback %r: %s", fallback, exc)
        return fallback


def _make_token(user_id: str, registry_url: str) -> str:
    """Self-sign a registry token carrying user_id + the scopes the control routes require."""
    if not settings.jwt_private_key:
        raise SystemExit("Set REGISTRY_TOKEN or JWT_PRIVATE_KEY in .env to authenticate against the registry.")
    issuer = _detect_jwt_issuer(registry_url, settings.jwt_issuer)
    scopes = "workflows-control mcp-proxy-ops servers-read agents-read agents-write federations-read"
    payload = build_jwt_payload(
        subject="real-e2e-user",
        issuer=issuer,
        audience=settings.jwt_audience,
        expires_in_seconds=3600,
        extra_claims={"scope": scopes, "user_id": user_id},
    )
    return encode_jwt(payload, settings.jwt_private_key, kid=settings.jwt_self_signed_kid)


def _build_definition(args: argparse.Namespace) -> WorkflowDefinition:
    """Complex node tree: MCP step -> condition branch -> HITL gate -> A2A pool."""
    return WorkflowDefinition(
        name=f"{PREFIX}real-mcp-a2a",
        description="Real MCP + A2A + HITL complex e2e",
        nodes=[
            WorkflowNode(name="mcp-doc", executor_key=args.mcp_key),
            WorkflowNode(
                name="branch",
                node_type=WorkflowNodeType.CONDITION,
                condition_cel="session_state.user_text != ''",
                true_steps=[WorkflowNode(name="a2a-direct", executor_key=args.a2a_direct)],
                false_steps=[WorkflowNode(name="fallback", executor_key="echo")],
            ),
            WorkflowNode(
                name="review-gate",
                executor_key="echo",
                human_review=HumanReviewSpec(requires_confirmation=True),
            ),
            WorkflowNode(name="a2a-pool", a2a_pool=args.a2a_pool),
        ],
    )


async def _grant_owner(user_id: str, workflow_id: PydanticObjectId) -> None:
    acl = ACLService(
        user_service=UserService(),
        group_service=GroupService(),
        role_cache=await load_role_cache(),
    )
    await acl.grant_permission(
        principal_type=PrincipalType.USER,
        principal_id=PydanticObjectId(user_id),
        resource_type=RegistryResourceType.WORKFLOW,
        resource_id=workflow_id,
        perm_bits=RoleBits.OWNER,
    )


async def _grant_agent_access(user_id: str, agent_keys: list[str]) -> None:
    """Grant the ephemeral user VIEW on each A2A agent used by the flow.

    The HTTP-triggered run resolves executors with the JWT's user_id, which
    enforces REMOTE_AGENT ACL (unlike the user_id=None bypass that one-off
    scripts use). Without this grant the run fails at A2A resolution.
    """
    acl = ACLService(
        user_service=UserService(),
        group_service=GroupService(),
        role_cache=await load_role_cache(),
    )
    for key in dict.fromkeys(agent_keys):
        agent = await A2AAgent.find_one({"path": f"/{key.lstrip('/')}"})
        if agent is None:
            print(f"{FAIL} A2A agent not found for key {key!r}; flow will fail at resolution")
            continue
        await acl.grant_permission(
            principal_type=PrincipalType.USER,
            principal_id=PydanticObjectId(user_id),
            resource_type=RegistryResourceType.REMOTE_AGENT,
            resource_id=agent.id,
            perm_bits=RoleBits.VIEWER,
        )


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
    # The ephemeral user owns only the grants we created (workflow + agents).
    await db.get_collection("aclentries").delete_many({"principalId": PydanticObjectId(user_id)})


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
    if resp.status_code != 202:
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
        user_id = str(PydanticObjectId())
        token = os.getenv("REGISTRY_TOKEN") or _make_token(user_id, args.registry_url)
        print(f"{PASS} token ready (user_id={user_id})")

        definition = _build_definition(args)
        await definition.insert()
        await _grant_owner(user_id, definition.id)
        await _grant_agent_access(user_id, [args.a2a_direct, *args.a2a_pool])
        print(f"{PASS} definition inserted + workflow/agent ACL granted (id={definition.id})")

        try:
            headers = {"Authorization": f"Bearer {token}"}
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
