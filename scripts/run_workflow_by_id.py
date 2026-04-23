from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from agno.models.aws import AwsBedrock
from agno.workflow import StepInput, StepOutput
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from registry import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)

from registry_pkgs.core.config import MongoConfig
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models.workflow import NodeRun, WorkflowDefinition, WorkflowRun
from registry_pkgs.workflows.compiler import WorkflowExecutor, flatten_workflow_nodes
from registry_pkgs.workflows.executor_resolver import build_executor_registry
from registry_pkgs.workflows.runner import WorkflowRunner


async def _echo_executor(step_input: StepInput, session_state: dict) -> StepOutput:
    session_state["echo_count"] = int(session_state.get("echo_count", 0)) + 1
    content = step_input.input or step_input.previous_step_content or ""
    return StepOutput(content=f"echo: {content}")


async def _set_value_executor(step_input: StepInput, session_state: dict) -> StepOutput:
    session_state["sample_value"] = "set by local demo executor"
    return StepOutput(content=session_state["sample_value"])


_LOCAL_DEMO_EXECUTORS: dict[str, WorkflowExecutor] = {
    "echo": _echo_executor,
    "set_value": _set_value_executor,
}


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
    await MongoDB.connect_db(
        config=MongoConfig(
            mongo_uri=os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/jarvis"),
            mongodb_username=os.getenv("MONGODB_USERNAME", ""),
            mongodb_password=os.getenv("MONGODB_PASSWORD", ""),
        ),
    )
    db_name = MongoDB.database_name

    try:
        definition = await WorkflowDefinition.get(definition_id)
        if definition is None:
            print(f"ERROR: WorkflowDefinition {definition_id!r} not found")
            sys.exit(1)

        print(f"Loaded: {definition.name!r}  nodes={[n.name for n in definition.nodes]}\n")

        executor_keys = [n.executor_key for n in flatten_workflow_nodes(definition.nodes) if n.executor_key is not None]
        llm = AwsBedrock(
            aws_region=settings.aws_region,
            aws_session_token=settings.aws_session_token,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )
        local_executor_keys = [key for key in executor_keys if key in _LOCAL_DEMO_EXECUTORS]
        remote_executor_keys = [key for key in executor_keys if key not in _LOCAL_DEMO_EXECUTORS]
        executor_registry = {key: _LOCAL_DEMO_EXECUTORS[key] for key in local_executor_keys}
        if remote_executor_keys:
            executor_registry.update(
                await build_executor_registry(
                    remote_executor_keys,
                    llm=llm,
                    registry_url=os.getenv("REGISTRY_URL", "http://localhost:8000"),
                    registry_token=os.getenv("REGISTRY_TOKEN", ""),
                )
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
