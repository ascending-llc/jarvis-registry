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
    REGISTRY_URL       Registry base URL (default: http://localhost:7860)
    MONGO_URI          MongoDB connection string (default: mongodb://127.0.0.1:27017/jarvis)
    BEDROCK_MODEL      Bedrock model ID for MCP steps (default: us.amazon.nova-lite-v1:0)
    SELECTOR_MODEL     Bedrock model ID for A2A pool selection (default: us.amazon.nova-micro-v1:0)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path

from agno.models.aws import AwsBedrock
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from registry import settings
from registry_pkgs.core.config import MongoConfig
from registry_pkgs.core.jwt_utils import build_jwt_payload, encode_jwt
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models.a2a_agent import A2AAgent
from registry_pkgs.models.extended_acl_entry import ExtendedAclEntry
from registry_pkgs.models.workflow import NodeRun, WorkflowDefinition, WorkflowNode, WorkflowRun
from registry_pkgs.workflows.runner import WorkflowRunner


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


def _build_definition(mcp_key: str, a2a_pool: list[str], pool_only: bool = False) -> WorkflowDefinition:
    nodes = []
    if not pool_only:
        nodes.append(WorkflowNode(name="mcp-step", executor_key=mcp_key))
    nodes.append(WorkflowNode(name="pool-a2a-step", a2a_pool=a2a_pool))
    return WorkflowDefinition(
        name=f"pool-smoke-{mcp_key or 'pool-only'}",
        description="Smoke test: pool A2A step" + ("" if pool_only else " with fixed MCP step"),
        nodes=nodes,
    )


def _print_results(run: WorkflowRun, node_runs: list[NodeRun]) -> None:
    print(f"\nWorkflowRun  id={run.id}  status={run.status}")
    if run.error_summary:
        print(f"  error: {run.error_summary}")
    print()
    for nr in node_runs:
        print(f"  NodeRun  name={nr.node_name}  status={nr.status}")
        if nr.selected_a2a_key:
            print(f"    selected_a2a_key = {nr.selected_a2a_key}")
        if nr.error:
            print(f"    error = {nr.error}")
        if nr.output_snapshot:
            snippet = str(nr.output_snapshot.get("content", ""))[:300]
            print(f"    output = {snippet}")
    print()


async def _resolve_pool_user_id(a2a_pool: list[str]) -> str | None:
    """Return the first user_id that has VIEW access to the first pool agent."""
    for key in a2a_pool:
        agent = await A2AAgent.find_one({"path": f"/{key}"})
        if agent is None:
            continue
        acls = await ExtendedAclEntry.find({"resourceId": agent.id, "principalType": "user"}).to_list()
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
    scopes = "servers-read agents-read agents-write server-write mcp-proxy-ops federations-read"
    extra: dict = {"scope": scopes}
    if user_id:
        extra["user_id"] = user_id
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
        llm = AwsBedrock(
            id=os.getenv("BEDROCK_MODEL", "us.amazon.nova-lite-v1:0"),
            aws_region=settings.aws_region,
            aws_session_token=settings.aws_session_token,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )
        selector_llm = AwsBedrock(
            id=os.getenv("SELECTOR_MODEL", "us.amazon.nova-micro-v1:0"),
            aws_region=settings.aws_region,
            aws_session_token=settings.aws_session_token,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )

        definition = _build_definition(args.mcp_key, args.a2a_pool, pool_only=args.pool_only)
        await definition.insert()
        print(f"WorkflowDefinition created: id={definition.id}")
        if not args.pool_only:
            print(f"  MCP step  : {args.mcp_key}")
        print(f"  Pool step : {args.a2a_pool}")

        runner = WorkflowRunner(
            llm=llm,
            selector_llm=selector_llm,
            registry_url=args.registry_url,
            db_client=MongoDB.get_client(),
            db_name=MongoDB.database_name,
            jwt_config=settings.jwt_signing_config,
        )

        print(f"\nRunning workflow with prompt: {args.prompt!r}\n")
        run, node_runs = await runner.run(
            str(definition.id),
            args.prompt,
            registry_token=registry_token,
            accessible_agent_ids=None,  # script context: bypass ACL filtering
            trigger_source="pool-smoke",
        )
        _print_results(run, node_runs)

        failed = str(run.status) != "completed" or any(str(nr.status) != "completed" for nr in node_runs)
        if failed:
            print("Smoke test FAILED.")
            return 1
        print("Smoke test PASSED.")
        return 0

    finally:
        try:
            await MongoDB.close_db()
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            print(f"WARNING: MongoDB.close_db failed: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
