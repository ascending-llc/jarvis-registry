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
    REGISTRY_TOKEN     User-scoped Bearer token. Auto-generated from JWT_PRIVATE_KEY when absent.
    REGISTRY_CLIENT_ID Client identity used for per-client MCP consent.
    REGISTRY_URL       Registry base URL (default: http://localhost:7860)
    MONGO_URI          MongoDB connection string (default: mongodb://127.0.0.1:27017/jarvis)
    WORKFLOW_TIMEOUT   Maximum number of seconds to poll before failing (default: 300).
    KEEP_WORKFLOW      Keep a completed/failed temporary workflow when set.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from registry import settings
from registry_pkgs.core.config import MongoConfig
from registry_pkgs.core.jwt_utils import build_jwt_payload, encode_jwt
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models.a2a_agent import A2AAgent
from registry_pkgs.models.extended_access_role import RegistryResourceType
from registry_pkgs.models.extended_acl_entry import RegistryAclEntry


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
        nodes.append({"name": "mcp-step", "nodeType": "step", "executorKey": mcp_key})
    nodes.append({"name": "pool-a2a-step", "nodeType": "step", "a2aPool": a2a_pool})
    return {
        "name": f"pool-smoke-{mcp_key or 'pool-only'}",
        "description": "Smoke test: pool A2A step" + ("" if pool_only else " with fixed MCP step"),
        "canvas": {"viewport": {"x": 0, "y": 0, "zoom": 1}},
        "nodes": nodes,
    }


def _print_results(run: dict) -> None:
    print(f"\nWorkflowRun  id={run.get('id')}  status={run.get('status')}")
    if run.get("errorSummary"):
        print(f"  error: {run['errorSummary']}")
    print()
    for node in run.get("nodeRuns", []):
        print(f"  NodeRun  name={node.get('nodeName')}  status={node.get('status')}")
        if node.get("selectedA2aKey"):
            print(f"    selected_a2a_key = {node['selectedA2aKey']}")
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
    """Return the first user_id that has VIEW access to the first pool agent."""
    for key in a2a_pool:
        agent = await A2AAgent.find_one({"path": f"/{key}"})
        if agent is None:
            continue
        acls = await RegistryAclEntry.find(
            {
                "resourceType": RegistryResourceType.REMOTE_AGENT.value,
                "resourceId": agent.id,
                "principalType": "user",
            }
        ).to_list()
        for acl in acls:
            if int(acl.permBits) & 1:  # VIEW bit
                return str(acl.principalId)
    return None


def _detect_jwt_issuer(registry_url: str, fallback: str) -> str:
    """Query the registry's auth config to find the actual JWT issuer in use."""
    try:
        auth_config_url = f"{registry_url.rstrip('/')}/api/auth/config"
        with urllib.request.urlopen(auth_config_url, timeout=3) as resp:  # noqa: S310  # nosec B310
            config = json.loads(resp.read())
        auth_server_url = config.get("auth_server_url", "").rstrip("/")
        if not auth_server_url:
            return fallback
        oidc_url = f"{auth_server_url}/.well-known/openid-configuration"
        with urllib.request.urlopen(oidc_url, timeout=3) as resp:  # noqa: S310  # nosec B310
            oidc = json.loads(resp.read())
        return oidc.get("issuer", fallback)
    except Exception as e:
        print(e)
        return fallback


async def _make_registry_token(a2a_pool: list[str], registry_url: str) -> str:
    """Generate a self-signed registry token from JWT_PRIVATE_KEY."""
    if not settings.jwt_private_key:
        raise SystemExit("Set REGISTRY_TOKEN or JWT_PRIVATE_KEY in .env to authenticate against the registry.")
    issuer = _detect_jwt_issuer(registry_url, settings.jwt_issuer)
    user_id = await _resolve_pool_user_id(a2a_pool)
    if not user_id:
        raise SystemExit("Could not find a user with VIEW access to the requested A2A agents; set REGISTRY_TOKEN.")
    scopes = "workflows-read workflows-write workflows-control servers-read agents-read federations-read"
    extra: dict = {
        "scope": scopes,
        "user_id": user_id,
        "client_id": os.getenv("REGISTRY_CLIENT_ID", "workflow-pool-script"),
    }
    payload = build_jwt_payload(
        subject="smoke-test-user",
        issuer=issuer,
        audience=settings.jwt_audience,
        expires_in_seconds=3600,
        extra_claims=extra,
    )
    token = encode_jwt(payload, settings.jwt_private_key, kid=settings.jwt_self_signed_kid)
    print(f"Generated registry token (sub=smoke-test-user, user_id={user_id}, iss={issuer})")
    return token


def _api(registry_url: str, path: str) -> str:
    return f"{registry_url.rstrip('/')}/api/{settings.api_version}{path}"


async def _create_and_run(
    client: httpx.AsyncClient,
    args: argparse.Namespace,
    token: str,
) -> tuple[str, dict]:
    headers = {"Authorization": f"Bearer {token}"}
    create = await client.post(
        _api(args.registry_url, "/workflows"),
        headers=headers,
        json=_build_definition_payload(args.mcp_key, args.a2a_pool, pool_only=args.pool_only),
    )
    create.raise_for_status()
    workflow_id = create.json()["id"]
    print(f"WorkflowDefinition created through API: id={workflow_id}")

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
        registry_token = os.getenv("REGISTRY_TOKEN") or await _make_registry_token(args.a2a_pool, args.registry_url)
        if not args.pool_only:
            print(f"  MCP step  : {args.mcp_key}")
        print(f"  Pool step : {args.a2a_pool}")
        print(f"\nRunning workflow with prompt: {args.prompt!r}\n")
        headers = {"Authorization": f"Bearer {registry_token}"}
        async with httpx.AsyncClient(timeout=30) as client:
            workflow_id, run = await _create_and_run(client, args, registry_token)
            _print_results(run)
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
