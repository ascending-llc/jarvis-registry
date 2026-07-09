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
from registry_pkgs.models.workflow import NodeRun, WorkflowDefinition, WorkflowRun, WorkflowVersion

MONGO_URI = "mongodb://127.0.0.1:27017/jarvis"

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class PurgePlan:
    workflows: list[WorkflowDefinition] = field(default_factory=list)
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
    """Query counts without deleting anything."""
    plan = PurgePlan()

    if workflow_id:
        try:
            oid = PydanticObjectId(workflow_id)
        except Exception as exc:
            raise ValueError(f"--workflow-id is not a valid ObjectId: {workflow_id}") from exc
        workflow = await WorkflowDefinition.get(oid)
        if workflow is None:
            raise ValueError(f"WorkflowDefinition not found: {workflow_id}")
        plan.workflows = [workflow]
    else:
        plan.workflows = await WorkflowDefinition.find_all().to_list()

    if not plan.workflows:
        return plan

    wf_ids = [w.id for w in plan.workflows if w.id is not None]

    runs = await WorkflowRun.find({"workflow_definition_id": {"$in": wf_ids}}).to_list()
    run_ids = [r.id for r in runs if r.id is not None]
    plan.run_count = len(runs)

    plan.node_run_count = await NodeRun.find({"workflow_run_id": {"$in": run_ids}}).count() if run_ids else 0

    db = MongoDB.get_database()
    if run_ids:
        session_ids = [str(rid) for rid in run_ids]
        plan.session_count = await db.get_collection("agno_workflow_sessions").count_documents(
            {"session_id": {"$in": session_ids}}
        )

    plan.version_count = await WorkflowVersion.find({"workflow_id": {"$in": wf_ids}}).count()

    return plan


async def apply_purge(plan: PurgePlan) -> PurgeOutcome:
    """Delete everything in the plan. Returns per-stage counters."""
    outcome = PurgeOutcome()

    if not plan.workflows:
        return outcome

    wf_ids = [w.id for w in plan.workflows if w.id is not None]
    db = MongoDB.get_database()

    # 1. Collect run ids (needed for children).
    runs = await WorkflowRun.find({"workflow_definition_id": {"$in": wf_ids}}).to_list()
    run_ids = [r.id for r in runs if r.id is not None]

    # 2. NodeRun first — leaf documents.
    if run_ids:
        try:
            result = await NodeRun.find({"workflow_run_id": {"$in": run_ids}}).delete()
            outcome.node_runs_deleted = _deleted_count(result, fallback=0)
        except Exception as exc:
            outcome.errors.append(f"NodeRun delete: {exc}")
            return outcome

        # 3. agno session state — keyed by str(run_id).
        try:
            session_ids = [str(rid) for rid in run_ids]
            result = await db.get_collection("agno_workflow_sessions").delete_many({"session_id": {"$in": session_ids}})
            outcome.sessions_deleted = result.deleted_count
        except Exception as exc:
            outcome.errors.append(f"agno_workflow_sessions delete: {exc}")
            return outcome

        # 4. WorkflowRun documents.
        try:
            result = await WorkflowRun.find({"_id": {"$in": run_ids}}).delete()
            outcome.runs_deleted = _deleted_count(result, fallback=len(run_ids))
        except Exception as exc:
            outcome.errors.append(f"WorkflowRun delete: {exc}")
            return outcome

    # 5. WorkflowVersion documents.
    try:
        result = await WorkflowVersion.find({"workflow_id": {"$in": wf_ids}}).delete()
        outcome.versions_deleted = _deleted_count(result, fallback=0)
    except Exception as exc:
        outcome.errors.append(f"WorkflowVersion delete: {exc}")
        return outcome

    # 6. WorkflowDefinition documents — last.
    try:
        result = await WorkflowDefinition.find({"_id": {"$in": wf_ids}}).delete()
        outcome.definitions_deleted = _deleted_count(result, fallback=len(wf_ids))
    except Exception as exc:
        outcome.errors.append(f"WorkflowDefinition delete: {exc}")

    return outcome


def _deleted_count(result: Any, *, fallback: int) -> int:
    count = getattr(result, "deleted_count", None)
    if count is not None:
        return int(count)
    if isinstance(result, int):
        return result
    return fallback


def _print_plan(plan: PurgePlan, *, workflow_id: str | None) -> None:
    scope = f"workflow {workflow_id}" if workflow_id else "ALL workflows"
    print("\n" + "=" * 70)
    print("WORKFLOW PURGE PLAN")
    print("=" * 70)
    print(f"Scope:                    {scope}")
    print(f"WorkflowDefinitions:      {len(plan.workflows)}")
    print(f"WorkflowVersions:         {plan.version_count}")
    print(f"WorkflowRuns:             {plan.run_count}")
    print(f"NodeRuns:                 {plan.node_run_count}")
    print(f"agno_workflow_sessions:   {plan.session_count}")
    print("=" * 70)
    if plan.workflows:
        print("\n-- WorkflowDefinitions --")
        for w in plan.workflows[:30]:
            print(f"  - {w.id}  {w.name}")
        if len(plan.workflows) > 30:
            print(f"  ... and {len(plan.workflows) - 30} more")
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

        total = len(plan.workflows) + plan.version_count + plan.run_count + plan.node_run_count + plan.session_count
        if total == 0:
            print("Nothing to purge.")
            return 0

        if not opts.apply:
            print("\nDry-run only. Re-run with --apply to delete.")
            return 0

        if not opts.assume_yes:
            scope = f"workflow {opts.workflow_id}" if opts.workflow_id else f"all {len(plan.workflows)} workflow(s)"
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
