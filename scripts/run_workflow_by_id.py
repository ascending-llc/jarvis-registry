from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from agno.models.anthropic import Claude

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "registry-pkgs" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "registry" / "src"))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)

from registry_pkgs.core.config import MongoConfig
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models.workflow import NodeRun, WorkflowDefinition, WorkflowRun
from registry_pkgs.workflows.compiler import flatten_workflow_nodes
from registry_pkgs.workflows.executor_resolver import build_executor_registry
from registry_pkgs.workflows.runner import WorkflowRunner


def _print_status(run: WorkflowRun, node_runs: list[NodeRun]) -> None:
    icons = {"completed": "✓", "failed": "✗", "running": "→", "pending": "·", "skipped": "○"}
    print("\n── Per-node status ──────────────────────────────────────")
    for nr in node_runs:
        icon = icons.get(nr.status, "?")
        error_info = f"  error={nr.error!r}" if nr.error else ""
        print(f"  {icon} {nr.node_name:<25} status={nr.status}  attempt={nr.attempt}{error_info}")
    print("\n── Run result ────────────────────────────────────────────")
    print(f"  status      : {run.status}")
    if run.final_output:
        print(f"  final_output: {run.final_output}")
    if run.error_summary:
        print(f"  error       : {run.error_summary}")
    print()


async def main(definition_id: str, user_text: str) -> None:
    mongo_uri = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/jarvis")
    db_name = mongo_uri.split("://")[-1].rsplit("/", 1)[-1].split("?")[0] or "jarvis"

    await MongoDB.connect_db(
        config=MongoConfig(
            mongo_uri=mongo_uri,
            mongodb_username=os.getenv("MONGODB_USERNAME", ""),
            mongodb_password=os.getenv("MONGODB_PASSWORD", ""),
        ),
        db_name=db_name,
    )

    try:
        definition = await WorkflowDefinition.get(definition_id)
        if definition is None:
            print(f"ERROR: WorkflowDefinition {definition_id!r} not found")
            sys.exit(1)

        print(f"Loaded: {definition.name!r}  nodes={[n.name for n in definition.nodes]}\n")

        executor_keys = [n.executor_key for n in flatten_workflow_nodes(definition.nodes) if n.executor_key is not None]
        llm = Claude(name=os.getenv("LLM_NAME", ""))
        executor_registry = await build_executor_registry(
            executor_keys, llm=llm, registry_url=os.getenv("REGISTRY_URL", "http://localhost:8000"), registry_token=""
        )
        runner = WorkflowRunner(
            executor_registry=executor_registry,
            db_client=MongoDB.get_client(),
            db_name=db_name,
        )
        run, node_runs = await runner.run(definition_id, user_text)
        _print_status(run, node_runs)

    finally:
        await MongoDB.close_db()


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Usage: python scripts/run_workflow_by_id.py <definition_id> [user_text]")
        sys.exit(1)

    asyncio.run(main(args[0], " ".join(args[1:]) if len(args) > 1 else "default input"))
