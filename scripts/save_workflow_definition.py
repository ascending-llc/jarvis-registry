"""Save a WorkflowDefinition to MongoDB.

Without executor keys, saves a local demo workflow. With executor keys, saves a
real registry-backed sequential workflow whose keys must resolve to active MCP
servers or A2A agents when run.

Usage:
    python scripts/save_workflow_definition.py
    python scripts/save_workflow_definition.py github slack --name real-workflow
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
        help="Real executor keys to run sequentially. Each key must match an active MCP serverName or A2A path.",
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
    if args.executor_keys:
        return WorkflowDefinition(
            name=args.name or "real-sequential-workflow",
            description=args.description or "Sequential workflow backed by registry MCP/A2A executors.",
            nodes=[
                WorkflowNode(
                    name=f"step-{index}-{key.strip('/').replace('/', '-')}",
                    executor_key=key,
                )
                for index, key in enumerate(args.executor_keys, start=1)
            ],
        )

    return WorkflowDefinition(
        name=args.name or "sample-echo-pipeline",
        description=args.description or "Echo input, then conditionally set a value or echo again.",
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


async def _active_executor_keys() -> tuple[list[str], list[str]]:
    mcp_servers = await ExtendedMCPServer.find(ExtendedMCPServer.status == "active").to_list()
    a2a_agents = await A2AAgent.find(A2AAgent.status == "active").to_list()
    mcp_keys = sorted(server.serverName for server in mcp_servers if server.config.get("enabled") is True)
    a2a_keys = sorted(agent.path.lstrip("/") for agent in a2a_agents)
    return mcp_keys, a2a_keys


async def _print_active_executors() -> None:
    mcp_keys, a2a_keys = await _active_executor_keys()
    print("Active MCP executor keys:")
    for key in mcp_keys or ["<none>"]:
        print(f"  {key}")
    print("\nActive A2A executor keys:")
    for key in a2a_keys or ["<none>"]:
        print(f"  {key}")


async def _validate_executor_keys(executor_keys: list[str]) -> None:
    mcp_keys, a2a_keys = await _active_executor_keys()
    valid_keys = set(mcp_keys) | set(a2a_keys)
    missing = [key for key in executor_keys if key.strip("/") not in valid_keys]
    if not missing:
        return

    print("ERROR: These executor keys are not active MCP servers or A2A agents:")
    for key in missing:
        print(f"  {key}")
    print("\nUse --list-executors to see valid keys.")
    print("Use --name to set the workflow name, for example:")
    print("  uv run python scripts/save_workflow_definition.py github --name github-workflow")
    sys.exit(1)


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

        if args.executor_keys and not args.no_validate:
            await _validate_executor_keys(args.executor_keys)

        definition = _build_definition(args)
        await definition.insert()
        print(f"Saved WorkflowDefinition id={definition.id}")
        print(f"  python scripts/run_workflow_by_id.py {definition.id} 'hello world'")
    finally:
        await MongoDB.close_db()


if __name__ == "__main__":
    asyncio.run(main())
