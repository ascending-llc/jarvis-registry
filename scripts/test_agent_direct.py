"""Directly invoke a MongoDB A2A agent by path.

Usage:
    uv run python scripts/test_agent_direct.py --path /deep-intel "your message"
    uv run python scripts/test_agent_direct.py --path /deep-intel --transport http_json "your message"
    uv run python scripts/test_agent_direct.py --list

Environment variables:
    MONGO_URI   MongoDB connection string (default: mongodb://127.0.0.1:27017/jarvis)

Examples:
    uv run python scripts/test_agent_direct.py --list
    uv run python scripts/test_agent_direct.py --path /a2aweatherforfederationtesting "What is the weather in New York?"
    uv run python scripts/test_agent_direct.py --path /a2aweatherforfederationtesting --transport http_json "What is the weather in New York?"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=ResourceWarning)

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from registry import settings
from registry_pkgs.core.config import MongoConfig
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models.a2a_agent import A2AAgent
from registry_pkgs.workflows.a2a_client import agent_base_url, call_a2a, get_agentcore_auth_mode, is_agentcore_runtime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)
logger = logging.getLogger(__name__)


async def _connect() -> None:
    await MongoDB.connect_db(
        config=MongoConfig(
            mongo_uri=os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/jarvis"),
            mongodb_username=os.getenv("MONGODB_USERNAME", ""),
            mongodb_password=os.getenv("MONGODB_PASSWORD", ""),
        ),
    )


async def _list_agents() -> None:
    agents = await A2AAgent.find({"status": "active"}).to_list()
    if not agents:
        print("No active A2A agents found.")
        return

    print(f"\n── Active A2A agents ({len(agents)}) ─────────────────────────────────")
    for a in agents:
        base_url = agent_base_url(a)
        provider = (a.federationMetadata or {}).get("providerType", "—")
        transport = a.config.type if a.config else "—"
        auth_mode = get_agentcore_auth_mode(a) if is_agentcore_runtime(a) else "—"
        print(f"  path={a.path:<35} transport={transport}  provider={provider}  auth={auth_mode}")
        print(f"  url={base_url}\n")


async def _resolve_agent(path: str) -> A2AAgent | None:
    normalized = path if path.startswith("/") else f"/{path}"
    agent = await A2AAgent.find_one(A2AAgent.path == normalized, A2AAgent.status == "active")
    if agent is None:
        print(f"ERROR: No active agent found with path={normalized!r}")
    return agent


async def main(path: str, message: str, *, list_agents: bool = False, transport: str | None = None) -> int:
    await _connect()

    try:
        if list_agents:
            await _list_agents()
            return 0

        agent = await _resolve_agent(path)
        if agent is None:
            return 1

        original_transport = agent.config.type if agent.config else "—"
        if transport and agent.config:
            agent.config.type = transport

        effective_transport = agent.config.type if agent.config else "—"
        override_note = f" (overriding {original_transport})" if transport and transport != original_transport else ""

        name = agent.config.title if agent.config else agent.card.name
        print("\n── Invoking agent ────────────────────────────────────────")
        print(f"  name      : {name}")
        print(f"  path      : {agent.path}")
        print(f"  url       : {agent_base_url(agent)}")
        print(f"  transport : {effective_transport}{override_note}")
        print(f"  message   : {message!r}\n")

        chunks_received: list[str] = []

        async def on_chunk(chunk: str) -> None:
            chunks_received.append(chunk)
            print(chunk, end="", flush=True)

        result = await call_a2a(agent, message, jwt_config=settings.jwt_signing_config, on_chunk=on_chunk)

        if chunks_received:
            print()

        print("\n── Result ────────────────────────────────────────────────")
        print(f"  success : {result.success}")
        if result.success:
            if not chunks_received:
                print(f"  response: {result.text}")
            if result.artifacts:
                print(f"  artifacts ({len(result.artifacts)}):")
                for i, part in enumerate(result.artifacts):
                    print(f"    [{i}] {part}")
        else:
            print(f"  error   : {result.error}")

        return 0 if result.success else 1

    finally:
        await asyncio.sleep(0.1)
        try:
            await MongoDB.close_db()
        except Exception as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            logger.warning("MongoDB.close_db failed: %s: %s", type(exc).__name__, exc)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Directly invoke a MongoDB A2A agent by path.")
    parser.add_argument("--path", help="Agent path (e.g. /deep-intel)")
    parser.add_argument("--list", action="store_true", help="List all active agents and exit")
    parser.add_argument(
        "--transport",
        choices=["jsonrpc", "http_json"],
        help="Override the agent's configured transport protocol",
    )
    parser.add_argument("message", nargs=argparse.REMAINDER, help="Message to send to the agent")
    parsed = parser.parse_args()

    if parsed.list:
        sys.exit(asyncio.run(main("", "", list_agents=True)))

    if not parsed.path:
        parser.print_help()
        sys.exit(1)

    msg = " ".join(parsed.message) if parsed.message else "Hello, please introduce yourself."
    sys.exit(asyncio.run(main(parsed.path, msg, transport=parsed.transport)))
