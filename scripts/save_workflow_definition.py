"""Save a sample WorkflowDefinition to MongoDB.

Exercises step + condition (with nested step children) so that serialization
round-trips can be verified end-to-end.

Usage:
    python scripts/save_workflow_definition.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "registry-pkgs" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "registry" / "src"))

from dotenv import load_dotenv

load_dotenv()
import os

from registry_pkgs.core.config import MongoConfig
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models.enums import WorkflowNodeType
from registry_pkgs.models.workflow import WorkflowDefinition, WorkflowNode


async def main() -> None:
    await MongoDB.connect_db(
        config=MongoConfig(
            mongo_uri=os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/jarvis"),
            mongodb_username=os.getenv("MONGODB_USERNAME", ""),
            mongodb_password=os.getenv("MONGODB_PASSWORD", ""),
        ),
    )

    try:
        definition = WorkflowDefinition(
            name="sample-echo-pipeline",
            description="Echo input, then conditionally set a value or echo again.",
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
        await definition.insert()
        print(f"Saved WorkflowDefinition id={definition.id}")
        print(f"  python scripts/run_workflow_by_id.py {definition.id} 'hello world'")
    finally:
        await MongoDB.close_db()


if __name__ == "__main__":
    asyncio.run(main())
