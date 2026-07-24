"""Run a saved WorkflowDefinition by its MongoDB ObjectId.

Usage:
    uv run python scripts/run_workflow_by_id.py <definition_id> [user_text]

    # List all active A2A agents before running:
    uv run python scripts/run_workflow_by_id.py --list-agents

Environment variables:
    REGISTRY_TOKEN   User-scoped Bearer token accepted by the Registry API.
                     If omitted, a self-signed token is generated from JWT_PRIVATE_KEY.
    REGISTRY_USER_ID Existing user ObjectId used by the generated token. The user must
                     have VIEW access to the workflow and its MCP/A2A resources.
    REGISTRY_CLIENT_ID
                     Client identity used for downstream MCP server OAuth headers
                     (default: workflow-script).
    REGISTRY_URL     Registry base URL (default: http://localhost:8000)
    MONGO_URI        MongoDB connection string (default: mongodb://127.0.0.1:27017/jarvis)
    WORKFLOW_TIMEOUT Maximum number of seconds to poll before failing (default: 300).

Example:
    uv run python scripts/run_workflow_by_id.py 6650f1a2b3c4d5e6f7890123 "Summarise AI news"
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
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from registry import settings
from registry_pkgs.core.config import MongoConfig
from registry_pkgs.core.jwt_utils import build_jwt_payload, encode_jwt
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models.a2a_agent import A2AAgent
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.models.workflow import WorkflowDefinition
from registry_pkgs.workflows.a2a_client import agent_base_url, get_agentcore_auth_mode, is_agentcore_runtime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)


def _make_registry_token() -> str:
    """Generate a self-signed registry token from JWT_PRIVATE_KEY when REGISTRY_TOKEN is not set."""
    if not settings.jwt_private_key:
        raise SystemExit("Set REGISTRY_TOKEN or JWT_PRIVATE_KEY in .env to authenticate against the registry.")
    user_id = os.getenv("REGISTRY_USER_ID")
    if not user_id:
        raise SystemExit("Set REGISTRY_USER_ID when REGISTRY_TOKEN is not provided.")
    client_id = os.getenv("REGISTRY_CLIENT_ID", "workflow-script")
    scopes = "workflows-control workflows-read servers-read agents-read federations-read"
    payload = build_jwt_payload(
        subject="smoke-test-user",
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        expires_in_seconds=3600,
        extra_claims={"scope": scopes, "user_id": user_id, "client_id": client_id},
    )
    token = encode_jwt(payload, settings.jwt_private_key, kid=settings.jwt_self_signed_kid)
    logging.getLogger(__name__).info(
        "Generated registry token (sub=smoke-test-user, user_id=%s, client_id=%s, iss=%s)",
        user_id,
        client_id,
        settings.jwt_issuer,
    )
    return token


def _print_status(run: dict) -> None:
    icons = {"completed": "✓", "failed": "✗", "running": "→", "pending": "·", "skipped": "○"}
    print("\n── Per-node status ──────────────────────────────────────")
    for node in run.get("nodeRuns", []):
        status = node.get("status", "unknown")
        icon = icons.get(status, "?")
        error_info = f"  error={node.get('error')!r}" if node.get("error") else ""
        print(
            f"  {icon} {node.get('nodeName', '?'):<25} status={status}  attempt={node.get('attempt', '?')}{error_info}"
        )
    print("\n── Run result ────────────────────────────────────────────")
    print(f"  status      : {run.get('status')}")
    if run.get("finalOutput"):
        print(f"  final_output: {run['finalOutput']}")
    if run.get("errorSummary"):
        print(f"  error       : {run['errorSummary']}")
    for requirement in run.get("pendingRequirements", []):
        print(f"  requirement : {requirement.get('requirementKind') or requirement.get('stepType')}")
        if requirement.get("consentUrl"):
            print(f"  consent_url : {requirement['consentUrl']}")
    print()


def _api(registry_url: str, path: str) -> str:
    return f"{registry_url.rstrip('/')}/api/{settings.api_version}{path}"


async def _trigger_and_wait(
    definition_id: str,
    user_text: str,
    *,
    registry_url: str,
    token: str,
) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            _api(registry_url, f"/workflows/{definition_id}/runs"),
            headers=headers,
            json={"initialInput": {"user_text": user_text}, "triggerSource": "script"},
        )
        response.raise_for_status()
        run_id = response.json()["runId"]
        print(f"Run queued: {run_id}")

        timeout = float(os.getenv("WORKFLOW_TIMEOUT", "300"))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            detail = await client.get(
                _api(registry_url, f"/workflows/{definition_id}/runs/{run_id}"),
                headers=headers,
            )
            detail.raise_for_status()
            run = detail.json()
            if run.get("status") in {"completed", "failed", "cancelled", "awaiting_approval"}:
                return run
            await asyncio.sleep(0.5)
    raise TimeoutError(f"Workflow run {run_id} did not finish within {timeout:g}s")


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
            {"config.enabled": True},
        )
        if mcp is not None:
            url = mcp.config.get("url") if mcp.config else None
            print(f"  {key:<30} MCP     → {url or '(no url)'}")
            continue

        # Try A2A agent
        path = f"/{key}" if not key.startswith("/") else key
        agent = await A2AAgent.find_one(
            A2AAgent.path == path,
            {"config.enabled": True},
        )
        if agent is not None:
            base_url = agent_base_url(agent)
            provider = (agent.federationMetadata or {}).get("providerType", "—")
            if is_agentcore_runtime(agent):
                auth_mode = get_agentcore_auth_mode(agent)
                print(f"  {key:<30} A2A     → {base_url}")
                print(f"                                provider={provider}  auth={auth_mode}")
            else:
                print(f"  {key:<30} A2A     → {base_url}")
        else:
            print(f"  {key:<30} NOT FOUND")
    print()


async def _list_all_agents() -> None:
    """List every enabled A2A agent (mirrors scripts/get_url.py)."""
    agents = await A2AAgent.find({"config.enabled": True}).to_list()
    if not agents:
        print("No enabled A2A agents found.")
        return

    print("── Enabled A2A agents ──────────────────────────────────")
    for a in agents:
        base_url = agent_base_url(a)
        provider = (a.federationMetadata or {}).get("providerType", "—")
        if is_agentcore_runtime(a):
            auth_mode = get_agentcore_auth_mode(a)
            print(f"  {a.path:<35} {a.config.type:<10} → {base_url}")
            print(f"                                      provider={provider}  auth={auth_mode}")
        else:
            print(f"  {a.path:<35} {a.config.type:<10} → {base_url}")
    print()


async def main(definition_id: str, user_text: str, *, list_agents: bool = False) -> int:
    registry_token = os.getenv("REGISTRY_TOKEN") or _make_registry_token()
    registry_url = os.getenv("REGISTRY_URL", "http://localhost:8000")

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

        print(f"Running definition {definition_id!r} with prompt: {user_text!r}\n")
        run = await _trigger_and_wait(
            definition_id,
            user_text,
            registry_url=registry_url,
            token=registry_token,
        )
        _print_status(run)

        if run.get("status") == "awaiting_approval":
            print("Run requires approval. Complete the displayed consent/HITL action, then poll it via the API.")
            return 2
        return 0 if run.get("status") == "completed" else 1

    finally:
        try:
            await MongoDB.close_db()
        except Exception as exc:
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
