"""Smoke test for the invoke_agent MCP tool.

Examples
--------
    # List agents:
    uv run python scripts/test_invoke_agent.py --list

    # Weather agent (JWT auth):
    uv run python scripts/test_invoke_agent.py \
        --agent /a2aweatherforfederationtesting \
        --skill get_weather \
        --message "What is the weather in Beijing?" \
        --token "$A2A_TOKEN
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from registry.mcpgw.tools.agent_invoke import invoke_agent_impl
from registry.services.a2a_agent_service import A2AAgentService
from registry_pkgs.core.config import MongoConfig
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models.a2a_agent import A2AAgent


def _print_agent_row(agent: A2AAgent) -> None:
    skills = [s.name for s in (agent.card.skills or [])]
    status = "enabled" if agent.isEnabled else "disabled"
    print(f"  {agent.path}  [{status}]  skills={skills or '(none)'}  id={agent.id}")


async def _list_agents() -> int:
    agents = await A2AAgent.find().sort("path").to_list()
    if not agents:
        print("No agents found.")
        return 0
    for agent in agents:
        _print_agent_row(agent)
    return 0


async def _resolve_agent(agent_arg: str) -> A2AAgent | None:
    path = agent_arg if agent_arg.startswith("/") else f"/{agent_arg}"
    found = await A2AAgent.find_one(A2AAgent.path == path)
    if found:
        return found
    try:
        from beanie import PydanticObjectId

        return await A2AAgent.get(PydanticObjectId(agent_arg))
    except Exception as e:
        print(f"error: {e}")
        return None


async def _pick_first_enabled_agent() -> A2AAgent | None:
    return await A2AAgent.find_one(A2AAgent.isEnabled == True)  # noqa: E712


def _build_test_message(agent: A2AAgent, skill_name: str | None) -> str:
    for skill in agent.card.skills or []:
        if skill.name == skill_name:
            if skill.examples:
                return skill.examples[0]
            if skill.description:
                return f"Please help me with: {skill.description}"
    if skill_name:
        return f"Please demonstrate the '{skill_name}' skill."
    title = (agent.config.title if agent.config else None) or agent.card.name
    return f"Hello, {title}! Please introduce yourself."


def _make_ctx(proxy_client: httpx.AsyncClient) -> SimpleNamespace:
    acl_service = AsyncMock()
    acl_service.check_user_permission = AsyncMock(return_value=None)
    lifespan = SimpleNamespace(
        a2a_agent_service=A2AAgentService(),
        acl_service=acl_service,
        proxy_client=proxy_client,
    )
    request = SimpleNamespace(
        state=SimpleNamespace(user={"user_id": "000000000000000000000000", "username": "smoke-test"})
    )
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context=lifespan, request=request))


async def _run(agent: A2AAgent, message: str, skill_name: str | None, timeout: float, token: str | None) -> int:
    print(f"→ {agent.path}  skill={skill_name or '(none)'}  auth={'jwt' if token else 'none'}")
    print(f"  {message[:120]}\n")

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, read=timeout), follow_redirects=True, headers=headers
    ) as client:
        result = await invoke_agent_impl(_make_ctx(client), str(agent.id), message, skill_name)

    if result.isError:
        print(f"{result.content[0].text if result.content else '(no content)'}")
        return 1

    print(f"{result.content[0].text if result.content else ''}")
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--list", action="store_true", help="List all agents and exit.")
    p.add_argument("--agent", metavar="PATH_OR_ID")
    p.add_argument("--skill", metavar="SKILL_NAME")
    p.add_argument("--message", metavar="TEXT")
    p.add_argument("--timeout", type=float, default=60.0)
    p.add_argument("--token", metavar="BEARER_TOKEN", default=os.getenv("A2A_TOKEN"))
    p.add_argument("--mongo-uri", default=os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/jarvis"))
    return p.parse_args()


async def main() -> int:
    args = _parse_args()

    try:
        await MongoDB.connect_db(
            config=MongoConfig(
                mongo_uri=args.mongo_uri,
                mongodb_username=os.getenv("MONGODB_USERNAME", ""),
                mongodb_password=os.getenv("MONGODB_PASSWORD", ""),
            )
        )
    except Exception as exc:
        print(f"MongoDB: {exc}")
        return 1

    try:
        if args.list:
            return await _list_agents()

        if args.agent:
            agent = await _resolve_agent(args.agent)
            if agent is None:
                print(f"Agent not found: '{args.agent}'")
                return 1
        else:
            agent = await _pick_first_enabled_agent()
            if agent is None:
                print("No enabled agents. Run --list to see all.")
                return 1

        skill_name = args.skill or (agent.card.skills[0].name if agent.card.skills else None)
        message = args.message or _build_test_message(agent, skill_name)
        return await _run(agent, message, skill_name, args.timeout, args.token)

    finally:
        try:
            await MongoDB.close_db()
        except Exception as e:
            print(f"error:{e}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
