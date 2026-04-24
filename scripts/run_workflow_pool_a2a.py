"""
End-to-end smoke test: 1 fixed MCP step + 1 pool A2A step (3 candidates).

Usage:
    uv run python scripts/run_workflow_pool_a2a.py \
        --mcp-key <mcp-server-name> \
        --a2a-pool <agent-1> <agent-2> <agent-3> \
        --prompt "Summarise the latest AI news"

The script creates a throw-away WorkflowDefinition, runs it, then prints
the NodeRun results including the selected_a2a_key to verify persistence.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from agno.models.aws import AwsBedrock
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from registry import settings
from registry_pkgs.core.config import MongoConfig
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models.workflow import NodeRun, WorkflowDefinition, WorkflowNode, WorkflowRun
from registry_pkgs.workflows.runner import WorkflowRunner


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pool A2A workflow smoke test.")
    parser.add_argument("--mcp-key", required=True, help="MCP server name (executor_key).")
    parser.add_argument(
        "--a2a-pool",
        nargs="+",
        required=True,
        metavar="AGENT_KEY",
        help="2–5 A2A agent keys (path without leading slash) for the pool step.",
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
        "--selector-model",
        default="us.amazon.nova-micro-v1:0",
        help="Bedrock model ID used for pool selection (cheap/fast recommended).",
    )
    return parser.parse_args()


def _build_definition(mcp_key: str, a2a_pool: list[str]) -> WorkflowDefinition:
    return WorkflowDefinition(
        name=f"pool-smoke-{mcp_key}",
        description="Smoke test: fixed MCP step + pool A2A step.",
        nodes=[
            WorkflowNode(
                name="mcp-step",
                executor_key=mcp_key,
            ),
            WorkflowNode(
                name="pool-a2a-step",
                a2a_pool=a2a_pool,
            ),
        ],
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


async def main() -> int:
    args = _parse_args()

    if len(args.a2a_pool) < 2:
        raise SystemExit("--a2a-pool requires at least 2 agent keys.")
    if len(args.a2a_pool) > 5:
        raise SystemExit("--a2a-pool accepts at most 5 agent keys.")

    registry_token = os.getenv("REGISTRY_TOKEN", "")

    await MongoDB.connect_db(
        config=MongoConfig(
            mongo_uri=os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/jarvis"),
            mongodb_username=os.getenv("MONGODB_USERNAME", ""),
            mongodb_password=os.getenv("MONGODB_PASSWORD", ""),
        ),
    )

    try:
        # Primary model: used by MCP-server executors.
        llm = AwsBedrock(
            aws_region=settings.aws_region,
            aws_session_token=settings.aws_session_token,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )
        # Selector model: lighter model dedicated to A2A pool selection.
        selector_llm = AwsBedrock(
            id=args.selector_model,
            aws_region=settings.aws_region,
            aws_session_token=settings.aws_session_token,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )

        definition = _build_definition(args.mcp_key, args.a2a_pool)
        await definition.insert()
        print(f"WorkflowDefinition created: id={definition.id}")
        print(f"  MCP step  : {args.mcp_key}")
        print(f"  Pool step : {args.a2a_pool}")

        # WorkflowRunner is a long-lived service object — create once, reuse.
        # registry_token is per-request and passed to run(), not the constructor.
        workflow_runner = WorkflowRunner(
            llm=llm,
            selector_llm=selector_llm,
            registry_url=args.registry_url,
            db_client=MongoDB.get_client(),
            db_name=MongoDB.database_name,
        )

        print(f"\nRunning workflow with prompt: {args.prompt!r}\n")
        run, node_runs = await workflow_runner.run(
            str(definition.id),
            args.prompt,
            registry_token=registry_token,
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
