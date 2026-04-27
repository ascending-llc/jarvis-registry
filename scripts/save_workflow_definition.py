"""Save a WorkflowDefinition to MongoDB.

Supports three definition shapes:
  1. No args          — saves a local demo workflow (echo + condition).
  2. executor_keys    — sequential workflow backed by registry MCP/A2A executors.
  3. --a2a-pool       — adds an A2A pool step after any executor_key steps.

Usage:
    # List active executor keys:
    uv run python scripts/save_workflow_definition.py --list-executors

    # Sequential MCP/A2A workflow:
    uv run python scripts/save_workflow_definition.py github slack --name my-workflow

    # Pool A2A workflow (pool-only):
    uv run python scripts/save_workflow_definition.py --a2a-pool agent-1 agent-2 --name pool-demo

    # MCP step + pool A2A step:
    uv run python scripts/save_workflow_definition.py github --a2a-pool agent-1 agent-2

    # Then run it:
    uv run python scripts/run_workflow_by_id.py <id> "hello world"
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from registry_pkgs.core.config import MongoConfig
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models.a2a_agent import A2AAgent
from registry_pkgs.models.enums import WorkflowNodeType
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.models.workflow import WorkflowDefinition, WorkflowNode


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Save a WorkflowDefinition to MongoDB.")
    parser.add_argument(
        "executor_keys",
        nargs="*",
        help="Executor keys to run sequentially. Each must match an active MCP serverName or A2A path.",
    )
    parser.add_argument(
        "--a2a-pool",
        nargs="+",
        metavar="AGENT_KEY",
        help="Add an A2A pool step with these agent keys (2-5 keys). Appended after executor_key steps.",
    )
    parser.add_argument("--name", default="", help="WorkflowDefinition name.")
    parser.add_argument("--description", default="", help="WorkflowDefinition description.")
    parser.add_argument(
        "--list-executors",
        action="store_true",
        help="List active MCP/A2A executor keys from MongoDB and exit.",
    )
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="Save without validating executor keys against MongoDB.",
    )
    return parser.parse_args()


def _build_definition(args: argparse.Namespace) -> WorkflowDefinition:
    # No args at all → local demo workflow for testing the runner without real backends.
    if not args.executor_keys and not args.a2a_pool:
        return WorkflowDefinition(
            name=args.name or "sample-echo-pipeline",
            description=args.description or "Demo workflow: echo + condition (uses local executors).",
            nodes=[
                WorkflowNode(name="initial-echo", executor_key="echo"),
                WorkflowNode(
                    name="value-condition",
                    node_type=WorkflowNodeType.CONDITION,
                    condition_cel="session_state.echo_count > 0",
                    children=[
                        WorkflowNode(name="set-value-on-true", executor_key="set_value"),
                        WorkflowNode(name="echo-on-false", executor_key="echo"),
                    ],
                ),
            ],
        )

    nodes: list[WorkflowNode] = []

    # Sequential executor_key steps.
    for index, key in enumerate(args.executor_keys, start=1):
        nodes.append(
            WorkflowNode(
                name=f"step-{index}-{key.strip('/').replace('/', '-')}",
                executor_key=key,
            )
        )

    # Optional pool step appended last.
    if args.a2a_pool:
        nodes.append(WorkflowNode(name="pool-a2a-step", a2a_pool=args.a2a_pool))

    # Default name from content when not specified.
    if not args.name:
        if args.executor_keys and args.a2a_pool:
            name = f"{'-'.join(args.executor_keys)}-pool-workflow"
        elif args.a2a_pool:
            name = "pool-a2a-workflow"
        else:
            name = "-".join(args.executor_keys) + "-workflow"
    else:
        name = args.name

    description = args.description or "Sequential workflow backed by registry MCP/A2A executors."

    return WorkflowDefinition(name=name, description=description, nodes=nodes)


def _a2a_agent_summary(agent: A2AAgent) -> str:
    """Return a one-line summary for an A2A agent: auth mode, provider, discoveryUrl."""
    meta = agent.federationMetadata or {}
    provider = meta.get("providerType", "—")

    if agent.config and agent.config.runtimeAccess:
        mode = str(
            agent.config.runtimeAccess.mode.value
            if hasattr(agent.config.runtimeAccess.mode, "value")
            else agent.config.runtimeAccess.mode
        ).upper()
    else:
        mode = "—"

    discovery_url = "—"
    if (
        agent.config
        and agent.config.runtimeAccess
        and agent.config.runtimeAccess.jwt
        and agent.config.runtimeAccess.jwt.discoveryUrl
    ):
        discovery_url = str(agent.config.runtimeAccess.jwt.discoveryUrl)

    return f"  {mode:<5}  {provider:<15}  {agent.path:<35}  {discovery_url}"


async def _active_executor_keys() -> tuple[list[str], list[str], list[A2AAgent]]:
    mcp_servers = await ExtendedMCPServer.find(ExtendedMCPServer.status == "active").to_list()
    a2a_agents = await A2AAgent.find(A2AAgent.status == "active").to_list()
    mcp_keys = sorted(server.serverName for server in mcp_servers if server.config.get("enabled") is True)
    a2a_keys = sorted(agent.path.lstrip("/") for agent in a2a_agents)
    return mcp_keys, a2a_keys, a2a_agents


async def _print_active_executors() -> None:
    mcp_keys, a2a_keys, a2a_agents = await _active_executor_keys()
    print("Active MCP executor keys:")
    for key in mcp_keys or ["<none>"]:
        print(f"  {key}")

    print("\nActive A2A executor keys (also valid as --a2a-pool members):")
    print(f"  {'AUTH':<5}  {'PROVIDER':<15}  {'PATH':<35}  {'DISCOVERY_URL'}")
    print(f"  {'-' * 5}  {'-' * 15}  {'-' * 35}  {'-' * 40}")
    for agent in sorted(a2a_agents, key=lambda a: a.path):
        print(_a2a_agent_summary(agent))


async def _validate_executor_keys(executor_keys: list[str], a2a_pool: list[str] | None) -> None:
    mcp_keys, a2a_keys, a2a_agents = await _active_executor_keys()
    valid_keys = set(mcp_keys) | set(a2a_keys)
    missing = [key for key in executor_keys if key.strip("/") not in valid_keys]
    pool_missing = [key for key in (a2a_pool or []) if key.strip("/") not in a2a_keys]

    errors: list[str] = []
    if missing:
        errors.append(
            "These executor keys are not active MCP servers or A2A agents:\n" + "\n".join(f"  {k}" for k in missing)
        )
    if pool_missing:
        errors.append("These --a2a-pool keys are not active A2A agents:\n" + "\n".join(f"  {k}" for k in pool_missing))

    if errors:
        print("ERROR: " + "\n".join(errors))
        print("\nUse --list-executors to see valid keys.")
        sys.exit(1)

    if a2a_pool and len(a2a_pool) < 2:
        print("ERROR: --a2a-pool requires at least 2 agent keys.")
        sys.exit(1)
    if a2a_pool and len(a2a_pool) > 5:
        print("ERROR: --a2a-pool accepts at most 5 agent keys.")
        sys.exit(1)


async def _print_node_agent_details(nodes: list[WorkflowNode]) -> None:
    """After saving, print discoveryUrl / auth info for every A2A agent referenced."""
    referenced_paths: set[str] = set()
    for node in nodes:
        if node.executor_key:
            referenced_paths.add(f"/{node.executor_key.strip('/')}")
        if node.a2a_pool:
            for key in node.a2a_pool:
                referenced_paths.add(f"/{key.strip('/')}")

    if not referenced_paths:
        return

    agents = await A2AAgent.find({"path": {"$in": sorted(referenced_paths)}, "status": "active"}).to_list()
    if not agents:
        return

    print("\nAgent endpoints:")
    print(f"  {'PATH':<35}  {'AUTH':<5}  {'PROVIDER':<15}  {'DISCOVERY_URL'}")
    print(f"  {'-' * 35}  {'-' * 5}  {'-' * 15}  {'-' * 40}")
    for agent in sorted(agents, key=lambda a: a.path):
        meta = agent.federationMetadata or {}
        provider = meta.get("providerType", "—")
        if agent.config and agent.config.runtimeAccess:
            mode = str(
                agent.config.runtimeAccess.mode.value
                if hasattr(agent.config.runtimeAccess.mode, "value")
                else agent.config.runtimeAccess.mode
            ).upper()
        else:
            mode = "—"
        discovery_url = "—"
        if (
            agent.config
            and agent.config.runtimeAccess
            and agent.config.runtimeAccess.jwt
            and agent.config.runtimeAccess.jwt.discoveryUrl
        ):
            discovery_url = str(agent.config.runtimeAccess.jwt.discoveryUrl)
        print(f"  {agent.path:<35}  {mode:<5}  {provider:<15}  {discovery_url}")


async def main() -> None:
    args = _parse_args()

    await MongoDB.connect_db(
        config=MongoConfig(
            mongo_uri=os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/jarvis"),
            mongodb_username=os.getenv("MONGODB_USERNAME", ""),
            mongodb_password=os.getenv("MONGODB_PASSWORD", ""),
        ),
    )

    try:
        if args.list_executors:
            await _print_active_executors()
            return

        if not args.no_validate and (args.executor_keys or args.a2a_pool):
            await _validate_executor_keys(args.executor_keys, args.a2a_pool)

        definition = _build_definition(args)
        await definition.insert()

        print(f"Saved WorkflowDefinition id={definition.id}  name={definition.name!r}")
        node_summary = ", ".join(
            (f"pool({n.a2a_pool})" if n.a2a_pool else n.executor_key or n.name) for n in definition.nodes
        )
        print(f"  nodes: {node_summary}")

        await _print_node_agent_details(definition.nodes)

        print("\nRun it:")
        print(f"  uv run python scripts/run_workflow_by_id.py {definition.id} 'your prompt here'")

    finally:
        await MongoDB.close_db()


if __name__ == "__main__":
    asyncio.run(main())
