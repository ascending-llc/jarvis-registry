"""Plan and run migrations: the pure `build_plan`, and the `up` / `wait` / `status` commands.

Each command returns its process exit code.
"""

import asyncio
import logging
import time
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from .discovery import MigrationScript, discover_migrations
from .lock import POLL_INTERVAL_SECONDS, MigrationLock, default_owner
from .records import MIGRATIONS_COLLECTION, MigrationRecord, insert_record, read_records

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILURE = 1


class ChecksumMismatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    script: MigrationScript
    record: MigrationRecord


class MigrationPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    pending: list[MigrationScript]
    out_of_order: list[MigrationScript]
    checksum_mismatches: list[ChecksumMismatch]
    unknown_records: list[MigrationRecord]


def _checksum_mismatch_message(mismatch: ChecksumMismatch) -> str:
    script, record = mismatch.script, mismatch.record
    return (
        f"Checksum mismatch for applied migration {script.version} ({script.name}): "
        f"recorded {record.checksum}, current file {script.checksum}. Applied migrations are immutable. "
        "If the edit was accidental, revert it. If it is deliberate and reviewed, merge it with the "
        "`migration-edit-approved` label, then, after the rollout to the new image has started, delete the "
        "record in every environment where this migration was applied: "
        f'db.{MIGRATIONS_COLLECTION}.deleteOne({{_id: "{script.version}"}}). '
        "See AGENTS.md § Data Migrations."
    )


def _log_checksum_mismatches(plan: MigrationPlan) -> None:
    for mismatch in plan.checksum_mismatches:
        logger.error(_checksum_mismatch_message(mismatch))


def _log_plan_warnings(plan: MigrationPlan) -> None:
    for record in plan.unknown_records:
        logger.warning(
            "Migration %s (%s) is recorded but not in this image (applied %s by build %s); "
            "expected after rolling back to an older image",
            record.id,
            record.name,
            record.applied_at.isoformat(),
            record.build_version,
        )
    for script in plan.out_of_order:
        logger.warning(
            "Migration %s (%s) is older than an already applied migration; running it out of order",
            script.version,
            script.name,
        )


async def _apply(
    db: AsyncDatabase,
    script: MigrationScript,
    lock: MigrationLock,
    build_version: str,
) -> bool:
    """Run one migration and record it. Returns False (after logging why) when the run must stop."""
    if lock.lost:
        logger.error("Migration lock lost; not starting migration %s (%s)", script.version, script.name)
        return False

    logger.info("Applying migration %s (%s)", script.version, script.name)
    started = time.monotonic()
    try:
        await script.up(db)
    except Exception:  # Arbitrary migration code: any failure stops the run, with its traceback.
        logger.exception("Migration %s (%s) failed; it is not recorded", script.version, script.name)
        return False
    duration_ms = int((time.monotonic() - started) * 1000)

    if lock.lost:
        logger.error("Migration lock lost; not recording migration %s (%s)", script.version, script.name)
        return False

    record = MigrationRecord(
        id=script.version,
        name=script.name,
        checksum=script.checksum,
        applied_at=datetime.now(UTC),
        duration_ms=duration_ms,
        build_version=build_version,
    )
    try:
        await insert_record(db, record)
    except DuplicateKeyError:
        logger.error(
            "Migration %s (%s) was recorded by another runner; this runner lost the migration lock",
            script.version,
            script.name,
        )
        return False
    logger.info("Applied migration %s (%s) in %d ms", script.version, script.name, duration_ms)
    return True


def build_plan(scripts: list[MigrationScript], records: list[MigrationRecord]) -> MigrationPlan:
    """Compare the scripts in code with the applied records. Pure: no I/O."""
    records_by_version = {record.id: record for record in records}
    script_versions = {script.version for script in scripts}
    ordered = sorted(scripts, key=lambda script: script.version)

    pending = [script for script in ordered if script.version not in records_by_version]
    applied_known = [version for version in records_by_version if version in script_versions]
    highest_applied = max(applied_known, default=None)
    out_of_order = [script for script in pending if highest_applied is not None and script.version < highest_applied]
    checksum_mismatches = [
        ChecksumMismatch(script=script, record=records_by_version[script.version])
        for script in ordered
        if script.version in records_by_version and records_by_version[script.version].checksum != script.checksum
    ]
    unknown_records = sorted(
        (record for record in records if record.id not in script_versions), key=lambda record: record.id
    )
    return MigrationPlan(
        pending=pending,
        out_of_order=out_of_order,
        checksum_mismatches=checksum_mismatches,
        unknown_records=unknown_records,
    )


async def up(db: AsyncDatabase, build_version: str) -> int:
    """Apply every pending migration under the lock."""
    scripts = discover_migrations()
    async with MigrationLock(db, default_owner()) as lock:
        # Read only after acquiring: the previous holder may have just applied migrations.
        plan = build_plan(scripts, await read_records(db))
        if plan.checksum_mismatches:
            _log_checksum_mismatches(plan)
            return EXIT_FAILURE
        _log_plan_warnings(plan)
        if not plan.pending:
            logger.info("No pending migrations")
            return EXIT_OK
        for script in plan.pending:
            if not await _apply(db, script, lock, build_version):
                return EXIT_FAILURE
    logger.info("Applied %d migration(s)", len(plan.pending))
    return EXIT_OK


async def wait(db: AsyncDatabase) -> int:
    """Block, without taking the lock, until no migration is pending."""
    while True:
        plan = build_plan(discover_migrations(), await read_records(db))
        if plan.checksum_mismatches:
            _log_checksum_mismatches(plan)
            return EXIT_FAILURE
        if not plan.pending:
            logger.info("No pending migrations")
            return EXIT_OK
        logger.info(
            "Waiting for pending migrations %s; checking again in %ss",
            [script.version for script in plan.pending],
            POLL_INTERVAL_SECONDS,
        )
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def status(db: AsyncDatabase) -> int:
    """Print each script's state and every unknown record. Fails only on a checksum mismatch."""
    scripts = discover_migrations()
    records = await read_records(db)
    plan = build_plan(scripts, records)
    records_by_version = {record.id: record for record in records}
    mismatched = {mismatch.script.version for mismatch in plan.checksum_mismatches}

    for script in sorted(scripts, key=lambda script: script.version):
        record = records_by_version.get(script.version)
        if record is None:
            state = "pending"
        elif script.version in mismatched:
            state = f"CHECKSUM MISMATCH (recorded {record.checksum}, current {script.checksum})"
        else:
            state = f"applied {record.applied_at.isoformat()} {record.build_version}"
        print(f"{script.version}  {script.name}  {state}")
    for record in plan.unknown_records:
        print(f"{record.id}  {record.name}  UNKNOWN (recorded, not in code) applied {record.applied_at.isoformat()}")

    if plan.checksum_mismatches:
        _log_checksum_mismatches(plan)
        return EXIT_FAILURE
    return EXIT_OK
