"""End-to-end smoke test: 1 fixed MCP step + 1 pool A2A step (2-5 candidates).

Usage:
    uv run python scripts/run_workflow_pool_a2a.py \
        --mcp-key <mcp-server-name> \
        --a2a-pool <agent-1> <agent-2> <agent-3> \
        --prompt "Summarise the latest AI news"

    # Skip the MCP step when the MCP backend is unavailable:
    uv run python scripts/run_workflow_pool_a2a.py \
        --pool-only \
        --a2a-pool <agent-1> <agent-2> \
        --prompt "Summarise the latest AI news"

Environment variables:
    REGISTRY_TOKEN     CRUD-session token. Auto-generated from JWT_PRIVATE_KEY when absent.
    REGISTRY_URL       Registry base URL (default: http://localhost:7860)
    MONGO_URI          MongoDB connection string (default: mongodb://127.0.0.1:27017/jarvis)
    WORKFLOW_TIMEOUT   Maximum number of seconds to poll before failing (default: 300).
    KEEP_WORKFLOW      Keep a completed/failed temporary workflow when set.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

import httpx
from beanie import PydanticObjectId
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from registry.core.config import settings
from registry.utils.csrf import compute_csrf_token
from registry_pkgs.core.config import MongoConfig
from registry_pkgs.core.jwt_tokens import mint_crud_session_token
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models.a2a_agent import A2AAgent
from registry_pkgs.models.extended_access_role import RegistryResourceType
from registry_pkgs.models.extended_acl_entry import RegistryAclEntry
from registry_pkgs.models.workflow import NodeRun


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pool A2A workflow smoke test.")
    parser.add_argument("--mcp-key", default="", help="MCP server name (executor_key). Required unless --pool-only.")
    parser.add_argument(
        "--a2a-pool",
        nargs="+",
        required=True,
        metavar="AGENT_KEY",
        help="2-5 A2A agent keys (path without leading slash) for the pool step.",
    )
    parser.add_argument(
        "--prompt",
        default="Analyse the current AI landscape and provide a brief summary.",
        help="User input passed as the workflow's initial input.",
    )
    parser.add_argument(
        "--registry-url",
        default=os.getenv("REGISTRY_URL", "http://localhost:7860"),
        help="Registry base URL.",
    )
    parser.add_argument(
        "--pool-only",
        action="store_true",
        help="Skip the MCP step and run only the A2A pool step.",
    )
    return parser.parse_args()


def _build_definition_payload(mcp_key: str, a2a_pool: list[str], pool_only: bool = False) -> dict:
    nodes: list[dict] = []
    if not pool_only:
        nodes.append(
            {
                "name": "mcp-step",
                "nodeType": "step",
                "executorKey": mcp_key,
                "stepObjective": "Use the MCP server to gather facts required by the user prompt.",
            }
        )
    nodes.append(
        {
            "name": "pool-a2a-step",
            "nodeType": "step",
            "a2aPool": a2a_pool,
            "stepObjective": "Select the best A2A agent to synthesize the final response.",
        }
    )
    return {
        "name": f"pool-smoke-{mcp_key or 'pool-only'}",
        "description": "Smoke test: pool A2A step" + ("" if pool_only else " with fixed MCP step"),
        "canvas": {"viewport": {"x": 0, "y": 0, "zoom": 1}},
        "nodes": nodes,
    }


def _print_results(run: dict, selected_agents: dict[str, str]) -> None:
    print(f"\nWorkflowRun  id={run.get('id')}  status={run.get('status')}")
    if run.get("errorSummary"):
        print(f"  error: {run['errorSummary']}")
    print()
    for node in run.get("nodeRuns", []):
        print(f"  NodeRun  name={node.get('nodeName')}  status={node.get('status')}")
        selected_agent = selected_agents.get(node.get("nodeName", ""))
        if selected_agent:
            print(f"    selected_a2a_key = {selected_agent}")
        if node.get("error"):
            print(f"    error = {node['error']}")
        if node.get("outputSnapshot"):
            snippet = str(node["outputSnapshot"].get("content", ""))[:300]
            print(f"    output = {snippet}")
    for requirement in run.get("pendingRequirements", []):
        print(f"  Requirement type={requirement.get('requirementKind') or requirement.get('stepType')}")
        if requirement.get("consentUrl"):
            print(f"    consent_url = {requirement['consentUrl']}")
    print()


async def _resolve_pool_user_id(a2a_pool: list[str]) -> str | None:
    """Return a user_id with VIEW access to every enabled pool agent."""
    paths = list(dict.fromkeys(key.lstrip("/") for key in a2a_pool))
    agents = await A2AAgent.find(
        {
            "path": {"$in": paths},
            "config.enabled": True,
        }
    ).to_list()
    if {agent.path for agent in agents} != set(paths):
        return None

    agent_ids = {agent.id for agent in agents}
    acls = await RegistryAclEntry.find(
        {
            "resourceType": RegistryResourceType.REMOTE_AGENT.value,
            "resourceId": {"$in": list(agent_ids)},
            "principalType": "user",
            "permBits": {"$bitsAllSet": 1},
        }
    ).to_list()
    resources_by_user: dict[object, set[object]] = {}
    for acl in acls:
        resources_by_user.setdefault(acl.principalId, set()).add(acl.resourceId)
    for principal_id, resource_ids in resources_by_user.items():
        if agent_ids <= resource_ids:
            return str(principal_id)
    return None


async def _make_registry_token(a2a_pool: list[str]) -> str:
    """Generate a CRUD-session token from JWT_PRIVATE_KEY."""
    if not settings.jwt_private_key:
        raise SystemExit("Set REGISTRY_TOKEN or JWT_PRIVATE_KEY in .env to authenticate against the registry.")
    user_id = await _resolve_pool_user_id(a2a_pool)
    if not user_id:
        raise SystemExit("Could not find a user with VIEW access to the requested A2A agents; set REGISTRY_TOKEN.")
    scopes = "workflows-read workflows-write workflows-control servers-read agents-read federations-read"
    token = mint_crud_session_token(
        settings.jwt_token_config,
        subject="smoke-test-user",
        token_type="access_token",
        expires_in_seconds=3600,
        extra_claims={
            "scope": scopes,
            "user_id": user_id,
            "username": "workflow-pool-script",
            "groups": ["jarvis-registry-admin"],
        },
    )
    print(f"Generated registry CRUD-session token (sub=smoke-test-user, user_id={user_id})")
    return token


def _session_headers(token: str) -> dict[str, str]:
    return {
        "Cookie": f"{settings.session_cookie_name}={token}",
        settings.csrf_header_name: compute_csrf_token(token),
    }


def _api(registry_url: str, path: str) -> str:
    return f"{registry_url.rstrip('/')}/api/{settings.api_version}{path}"


async def _create_and_run(
    client: httpx.AsyncClient,
    args: argparse.Namespace,
    token: str,
) -> tuple[str, dict]:
    headers = _session_headers(token)
    create = await client.post(
        _api(args.registry_url, "/workflows"),
        headers=headers,
        json=_build_definition_payload(args.mcp_key, args.a2a_pool, pool_only=args.pool_only),
    )
    create.raise_for_status()
    workflow_id = create.json()["id"]
    print(f"WorkflowDefinition created through API: id={workflow_id}")

    enable = await client.put(
        _api(args.registry_url, f"/workflows/{workflow_id}"),
        headers=headers,
        json={"enabled": True},
    )
    enable.raise_for_status()
    print(f"WorkflowDefinition enabled: id={workflow_id}")

    trigger = await client.post(
        _api(args.registry_url, f"/workflows/{workflow_id}/runs"),
        headers=headers,
        json={"initialInput": {"user_text": args.prompt}, "triggerSource": "pool-smoke"},
    )
    trigger.raise_for_status()
    run_id = trigger.json()["runId"]

    timeout = float(os.getenv("WORKFLOW_TIMEOUT", "300"))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = await client.get(
            _api(args.registry_url, f"/workflows/{workflow_id}/runs/{run_id}"),
            headers=headers,
        )
        detail.raise_for_status()
        run = detail.json()
        if run.get("status") in {"completed", "failed", "cancelled", "awaiting_approval"}:
            return workflow_id, run
        await asyncio.sleep(0.5)
    raise TimeoutError(f"Workflow run {run_id} did not finish within {timeout:g}s")


async def _load_selected_agents(run_id: str) -> dict[str, str]:
    node_runs = await NodeRun.find(NodeRun.workflow_run_id == PydanticObjectId(run_id)).to_list()
    return {
        node_run.node_name: node_run.selected_a2a_key for node_run in node_runs if node_run.selected_a2a_key is not None
    }


async def main() -> int:
    args = _parse_args()

    if len(args.a2a_pool) < 2:
        raise SystemExit("--a2a-pool requires at least 2 agent keys.")
    if len(args.a2a_pool) > 5:
        raise SystemExit("--a2a-pool accepts at most 5 agent keys.")
    if not args.pool_only and not args.mcp_key:
        raise SystemExit("--mcp-key is required unless --pool-only is set.")

    await MongoDB.connect_db(
        config=MongoConfig(
            mongo_uri=os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/jarvis"),
            mongodb_username=os.getenv("MONGODB_USERNAME", ""),
            mongodb_password=os.getenv("MONGODB_PASSWORD", ""),
        ),
    )

    try:
        registry_token = os.getenv("REGISTRY_TOKEN") or await _make_registry_token(args.a2a_pool)
        if not args.pool_only:
            print(f"  MCP step  : {args.mcp_key}")
        print(f"  Pool step : {args.a2a_pool}")
        print(f"\nRunning workflow with prompt: {args.prompt!r}\n")
        headers = _session_headers(registry_token)
        async with httpx.AsyncClient(timeout=30) as client:
            workflow_id, run = await _create_and_run(client, args, registry_token)
            selected_agents = await _load_selected_agents(run["id"])
            _print_results(run, selected_agents)
            if run.get("status") == "awaiting_approval":
                print(f"WorkflowDefinition kept for approval: id={workflow_id}")
            elif not os.getenv("KEEP_WORKFLOW"):
                delete = await client.delete(_api(args.registry_url, f"/workflows/{workflow_id}"), headers=headers)
                delete.raise_for_status()
                print(f"WorkflowDefinition deleted: id={workflow_id}")

        if run.get("status") == "awaiting_approval":
            print("Smoke test PAUSED: complete the displayed consent/HITL action and rerun or poll via the API.")
            return 2
        failed = run.get("status") != "completed" or any(
            node.get("status") != "completed" for node in run.get("nodeRuns", [])
        )
        if not selected_agents.get("pool-a2a-step"):
            print("Smoke test FAILED: pool selector result was not persisted.")
            return 1
        if failed:
            print("Smoke test FAILED.")
            return 1
        print("Smoke test PASSED.")
        return 0

    finally:
        try:
            await MongoDB.close_db()
        except Exception as exc:
            print(f"WARNING: MongoDB.close_db failed: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
