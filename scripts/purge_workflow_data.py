"""Purge workflow data from MongoDB.

Deletes WorkflowDefinition documents (and all their associated children) in
the correct cascade order so no orphan documents are left behind:

    NodeRun → agno_workflow_sessions → WorkflowRun → WorkflowVersion → WorkflowDefinition

This is the same order used by ``WorkflowService.delete_workflow`` in
production code — this script is a bulk / pre-deployment variant that
operates outside the API layer.

Usage:
    # Dry-run (default) — prints counts, touches nothing
    uv run python scripts/purge_workflow_data.py

    # Purge all workflow data
    uv run python scripts/purge_workflow_data.py --apply

    # Skip interactive confirmation
    uv run python scripts/purge_workflow_data.py --apply --yes

    # Purge a single workflow by its ObjectId
    uv run python scripts/purge_workflow_data.py --workflow-id 6839e... --apply

Options:
    --workflow-id <id>  Purge only the workflow with this ObjectId (default: all)
    --apply             Actually delete. Default is dry-run.
    --yes               Skip interactive confirmation when used with --apply.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from beanie import PydanticObjectId

from registry_pkgs.core.config import MongoConfig
from registry_pkgs.database.mongodb import MongoDB

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017/jarvis")

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)
logger = logging.getLogger(__name__)

_COL_DEFINITIONS = "workflow_definitions"
_COL_VERSIONS = "workflow_versions"
_COL_RUNS = "workflow_runs"
_COL_NODE_RUNS = "node_runs"
_COL_SESSIONS = "agno_workflow_sessions"


def _col(name: str) -> Any:
    return MongoDB.get_database().get_collection(name)


@dataclass
class PurgePlan:
    # Raw dicts with only {_id, name} projected — no Pydantic deserialization.
    workflow_docs: list[dict[str, Any]] = field(default_factory=list)
    version_count: int = 0
    run_count: int = 0
    node_run_count: int = 0
    session_count: int = 0


@dataclass
class PurgeOutcome:
    node_runs_deleted: int = 0
    sessions_deleted: int = 0
    runs_deleted: int = 0
    versions_deleted: int = 0
    definitions_deleted: int = 0
    errors: list[str] = field(default_factory=list)


async def collect_plan(workflow_id: str | None) -> PurgePlan:
    """Query counts without deleting anything (raw motor, no Pydantic)."""
    plan = PurgePlan()

    if workflow_id:
        try:
            oid = PydanticObjectId(workflow_id)
        except Exception as exc:
            raise ValueError(f"--workflow-id is not a valid ObjectId: {workflow_id}") from exc
        doc = await _col(_COL_DEFINITIONS).find_one({"_id": oid}, projection={"_id": 1, "name": 1})
        if doc is None:
            raise ValueError(f"WorkflowDefinition not found: {workflow_id}")
        plan.workflow_docs = [doc]
    else:
        plan.workflow_docs = (
            await _col(_COL_DEFINITIONS).find({}, projection={"_id": 1, "name": 1}).to_list(length=None)
        )

    if not plan.workflow_docs:
        return plan

    wf_ids = [d["_id"] for d in plan.workflow_docs]

    run_docs = (
        await _col(_COL_RUNS)
        .find({"workflow_definition_id": {"$in": wf_ids}}, projection={"_id": 1})
        .to_list(length=None)
    )
    run_ids = [r["_id"] for r in run_docs]
    plan.run_count = len(run_ids)

    if run_ids:
        plan.node_run_count = await _col(_COL_NODE_RUNS).count_documents({"workflow_run_id": {"$in": run_ids}})
        session_ids = [str(rid) for rid in run_ids]
        plan.session_count = await _col(_COL_SESSIONS).count_documents({"session_id": {"$in": session_ids}})

    plan.version_count = await _col(_COL_VERSIONS).count_documents({"workflow_id": {"$in": wf_ids}})

    return plan


async def apply_purge(plan: PurgePlan) -> PurgeOutcome:
    """Delete everything in the plan using raw motor queries.

    Order mirrors WorkflowService.delete_workflow:
      NodeRun → agno_workflow_sessions → WorkflowRun → WorkflowVersion → WorkflowDefinition
    """
    outcome = PurgeOutcome()

    if not plan.workflow_docs:
        return outcome

    wf_ids = [d["_id"] for d in plan.workflow_docs]

    # Re-fetch run ids at apply time so counts stay accurate even if new runs arrived.
    run_docs = (
        await _col(_COL_RUNS)
        .find({"workflow_definition_id": {"$in": wf_ids}}, projection={"_id": 1})
        .to_list(length=None)
    )
    run_ids = [r["_id"] for r in run_docs]

    if run_ids:
        # 2. NodeRun — leaf documents.
        try:
            result = await _col(_COL_NODE_RUNS).delete_many({"workflow_run_id": {"$in": run_ids}})
            outcome.node_runs_deleted = result.deleted_count
        except Exception as exc:
            outcome.errors.append(f"NodeRun delete: {exc}")
            return outcome

        # 3. agno session state — keyed by str(run_id).
        try:
            session_ids = [str(rid) for rid in run_ids]
            result = await _col(_COL_SESSIONS).delete_many({"session_id": {"$in": session_ids}})
            outcome.sessions_deleted = result.deleted_count
        except Exception as exc:
            outcome.errors.append(f"agno_workflow_sessions delete: {exc}")
            return outcome

        # 4. WorkflowRun documents.
        try:
            result = await _col(_COL_RUNS).delete_many({"_id": {"$in": run_ids}})
            outcome.runs_deleted = result.deleted_count
        except Exception as exc:
            outcome.errors.append(f"WorkflowRun delete: {exc}")
            return outcome

    # 5. WorkflowVersion documents.
    try:
        result = await _col(_COL_VERSIONS).delete_many({"workflow_id": {"$in": wf_ids}})
        outcome.versions_deleted = result.deleted_count
    except Exception as exc:
        outcome.errors.append(f"WorkflowVersion delete: {exc}")
        return outcome

    # 6. WorkflowDefinition documents — last.
    try:
        result = await _col(_COL_DEFINITIONS).delete_many({"_id": {"$in": wf_ids}})
        outcome.definitions_deleted = result.deleted_count
    except Exception as exc:
        outcome.errors.append(f"WorkflowDefinition delete: {exc}")

    return outcome


def _print_plan(plan: PurgePlan, *, workflow_id: str | None) -> None:
    scope = f"workflow {workflow_id}" if workflow_id else "ALL workflows"
    print("\n" + "=" * 70)
    print("WORKFLOW PURGE PLAN")
    print("=" * 70)
    print(f"Scope:                    {scope}")
    print(f"WorkflowDefinitions:      {len(plan.workflow_docs)}")
    print(f"WorkflowVersions:         {plan.version_count}")
    print(f"WorkflowRuns:             {plan.run_count}")
    print(f"NodeRuns:                 {plan.node_run_count}")
    print(f"agno_workflow_sessions:   {plan.session_count}")
    print("=" * 70)
    if plan.workflow_docs:
        print("\n-- WorkflowDefinitions --")
        for w in plan.workflow_docs[:30]:
            print(f"  - {w['_id']}  {w.get('name', '<no-name>')}")
        if len(plan.workflow_docs) > 30:
            print(f"  ... and {len(plan.workflow_docs) - 30} more")
    print("=" * 70)


def _print_outcome(outcome: PurgeOutcome) -> None:
    print("\n" + "=" * 70)
    print("PURGE RESULT")
    print("=" * 70)
    print(f"NodeRuns deleted:               {outcome.node_runs_deleted}")
    print(f"agno_workflow_sessions deleted: {outcome.sessions_deleted}")
    print(f"WorkflowRuns deleted:           {outcome.runs_deleted}")
    print(f"WorkflowVersions deleted:       {outcome.versions_deleted}")
    print(f"WorkflowDefinitions deleted:    {outcome.definitions_deleted}")
    if outcome.errors:
        print(f"\nErrors ({len(outcome.errors)}):")
        for err in outcome.errors:
            print(f"  - {err}")
    print("=" * 70)


@dataclass
class CliOptions:
    workflow_id: str | None = None
    apply: bool = False
    assume_yes: bool = False


def parse_args(argv: list[str]) -> CliOptions:
    opts = CliOptions()
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--workflow-id":
            if i + 1 >= len(argv):
                print("Error: --workflow-id requires a value")
                sys.exit(1)
            opts.workflow_id = argv[i + 1]
            i += 2
            continue
        if arg == "--apply":
            opts.apply = True
            i += 1
            continue
        if arg == "--yes":
            opts.assume_yes = True
            i += 1
            continue
        if arg in {"-h", "--help"}:
            print(__doc__)
            sys.exit(0)
        print(f"Error: unknown argument {arg}")
        sys.exit(1)
    return opts


async def _run(argv: list[str] | None = None) -> int:
    opts = parse_args(argv if argv is not None else sys.argv)

    await MongoDB.connect_db(config=MongoConfig(mongo_uri=MONGO_URI))
    print("MongoDB connected.")

    try:
        plan = await collect_plan(opts.workflow_id)
        _print_plan(plan, workflow_id=opts.workflow_id)

        total = len(plan.workflow_docs) + plan.version_count + plan.run_count + plan.node_run_count + plan.session_count
        if total == 0:
            print("Nothing to purge.")
            return 0

        if not opts.apply:
            print("\nDry-run only. Re-run with --apply to delete.")
            return 0

        if not opts.assume_yes:
            scope = f"workflow {opts.workflow_id}" if opts.workflow_id else f"all {len(plan.workflow_docs)} workflow(s)"
            answer = input(f"\nPermanently delete data for {scope}? Type 'yes' to proceed: ")
            if answer.strip().lower() != "yes":
                print("Aborted.")
                return 1

        outcome = await apply_purge(plan)
        _print_outcome(outcome)
        return 1 if outcome.errors else 0

    except ValueError as exc:
        print(f"Error: {exc}")
        return 1
    except Exception as exc:
        print(f"\nFatal error: {exc}")
        traceback.print_exc()
        return 1
    finally:
        await MongoDB.close_db()


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
