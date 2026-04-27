"""Run a saved WorkflowDefinition by its MongoDB ObjectId.

Usage:
    uv run python scripts/run_workflow_by_id.py <definition_id> [user_text]

    # List all active A2A agents before running:
    uv run python scripts/run_workflow_by_id.py --list-agents

Environment variables:
    REGISTRY_TOKEN   User-scoped Bearer token for the MCP gateway proxy.
                     If not set, a self-signed JWT is generated from JWT_PRIVATE_KEY.
    REGISTRY_URL     Registry base URL (default: http://localhost:8000)
    MONGO_URI        MongoDB connection string (default: mongodb://127.0.0.1:27017/jarvis)

Example:
    uv run python scripts/run_workflow_by_id.py 6650f1a2b3c4d5e6f7890123 "Summarise AI news"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from agno.models.aws import AwsBedrock
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from registry import settings
from registry_pkgs.core.config import MongoConfig
from registry_pkgs.core.jwt_utils import build_jwt_payload, encode_jwt
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models.a2a_agent import A2AAgent
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.models.workflow import NodeRun, WorkflowDefinition, WorkflowRun
from registry_pkgs.workflows.a2a_executor import _get_agentcore_auth_mode, _is_agentcore_runtime, agent_base_url
from registry_pkgs.workflows.runner import WorkflowRunner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)


def _make_registry_token() -> str:
    """Generate a self-signed registry token from JWT_PRIVATE_KEY when REGISTRY_TOKEN is not set."""
    if not settings.jwt_private_key:
        raise SystemExit("Set REGISTRY_TOKEN or JWT_PRIVATE_KEY in .env to authenticate against the registry.")
    scopes = "servers-read agents-read mcp-proxy-ops federations-read"
    payload = build_jwt_payload(
        subject="smoke-test-user",
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        expires_in_seconds=3600,
        extra_claims={"scope": scopes},
    )
    token = encode_jwt(payload, settings.jwt_private_key, kid=settings.jwt_self_signed_kid)
    logging.getLogger(__name__).info("Generated registry token (sub=smoke-test-user, iss=%s)", settings.jwt_issuer)
    return token


def _print_status(run: WorkflowRun, node_runs: list[NodeRun]) -> None:
    icons = {"completed": "✓", "failed": "✗", "running": "→", "pending": "·", "skipped": "○"}
    print("\n── Per-node status ──────────────────────────────────────")
    for nr in node_runs:
        icon = icons.get(str(nr.status), "?")
        error_info = f"  error={nr.error!r}" if nr.error else ""
        print(f"  {icon} {nr.node_name:<25} status={nr.status}  attempt={nr.attempt}{error_info}")
    print("\n── Run result ────────────────────────────────────────────")
    print(f"  status      : {run.status}")
    if run.final_output:
        print(f"  final_output: {run.final_output}")
    if run.error_summary:
        print(f"  error       : {run.error_summary}")
    print()


async def _print_definition_agents(definition_id: str) -> None:
    """Print URL / auth details for every A2A agent referenced by a WorkflowDefinition."""
    definition = await WorkflowDefinition.get(definition_id)
    if definition is None:
        print(f"WARNING: WorkflowDefinition {definition_id!r} not found")
        return

    executor_keys = []
    for node in definition.nodes or []:
        if node.executor_key:
            executor_keys.append(node.executor_key)
        if node.a2a_pool:
            executor_keys.extend(node.a2a_pool)

    if not executor_keys:
        return

    print("── Resolved executors ───────────────────────────────────")
    for key in executor_keys:
        # Try MCP server first
        mcp = await ExtendedMCPServer.find_one(
            ExtendedMCPServer.serverName == key,
            ExtendedMCPServer.status == "active",
        )
        if mcp is not None:
            url = mcp.config.get("url") if mcp.config else None
            print(f"  {key:<30} MCP     → {url or '(no url)'}")
            continue

        # Try A2A agent
        path = f"/{key}" if not key.startswith("/") else key
        agent = await A2AAgent.find_one(
            A2AAgent.path == path,
            A2AAgent.status == "active",
        )
        if agent is not None:
            base_url = agent_base_url(agent)
            provider = (agent.federationMetadata or {}).get("providerType", "—")
            if _is_agentcore_runtime(agent):
                auth_mode = _get_agentcore_auth_mode(agent)
                print(f"  {key:<30} A2A     → {base_url}")
                print(f"                                provider={provider}  auth={auth_mode}")
            else:
                print(f"  {key:<30} A2A     → {base_url}")
        else:
            print(f"  {key:<30} NOT FOUND")
    print()


async def _list_all_agents() -> None:
    """List every active A2A agent (mirrors scripts/get_url.py)."""
    agents = await A2AAgent.find({"status": "active"}).to_list()
    if not agents:
        print("No active A2A agents found.")
        return

    print("── Active A2A agents ────────────────────────────────────")
    for a in agents:
        base_url = agent_base_url(a)
        provider = (a.federationMetadata or {}).get("providerType", "—")
        if _is_agentcore_runtime(a):
            auth_mode = _get_agentcore_auth_mode(a)
            print(f"  {a.path:<35} {a.config.type:<10} → {base_url}")
            print(f"                                      provider={provider}  auth={auth_mode}")
        else:
            print(f"  {a.path:<35} {a.config.type:<10} → {base_url}")
    print()


async def main(definition_id: str, user_text: str, *, list_agents: bool = False) -> int:
    registry_token = os.getenv("REGISTRY_TOKEN") or _make_registry_token()

    await MongoDB.connect_db(
        config=MongoConfig(
            mongo_uri=os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/jarvis"),
            mongodb_username=os.getenv("MONGODB_USERNAME", ""),
            mongodb_password=os.getenv("MONGODB_PASSWORD", ""),
        ),
    )

    try:
        if list_agents:
            await _list_all_agents()
            return 0

        await _print_definition_agents(definition_id)

        llm = AwsBedrock(
            id=os.getenv("BEDROCK_MODEL", "us.amazon.nova-lite-v1:0"),
            aws_region=settings.aws_region,
            aws_session_token=settings.aws_session_token,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )

        runner = WorkflowRunner(
            llm=llm,
            registry_url=os.getenv("REGISTRY_URL", "http://localhost:8000"),
            db_client=MongoDB.get_client(),
            db_name=MongoDB.database_name,
        )

        print(f"Running definition {definition_id!r} with prompt: {user_text!r}\n")
        run, node_runs = await runner.run(
            definition_id,
            user_text,
            registry_token=registry_token,
            trigger_source="script",
        )
        _print_status(run, node_runs)

        return 0 if str(run.status) == "completed" else 1

    finally:
        try:
            await MongoDB.close_db()
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            print(f"WARNING: MongoDB.close_db failed: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a saved WorkflowDefinition by its MongoDB ObjectId.")
    parser.add_argument("definition_id", nargs="?", help="WorkflowDefinition ObjectId")
    parser.add_argument("user_text", nargs=argparse.REMAINDER, help="Prompt text to send to the workflow")
    parser.add_argument("--list-agents", action="store_true", help="List active A2A agents and exit")
    parsed = parser.parse_args()

    if parsed.list_agents:
        sys.exit(asyncio.run(main("", "", list_agents=True)))

    if not parsed.definition_id:
        parser.print_help()
        sys.exit(1)

    text = " ".join(parsed.user_text) if parsed.user_text else "default input"
    sys.exit(asyncio.run(main(parsed.definition_id, text)))
