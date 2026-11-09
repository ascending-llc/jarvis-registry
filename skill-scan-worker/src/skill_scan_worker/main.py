"""Worker entrypoint: init → N job runners + orphan sweep → graceful shutdown (change 3)."""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from registry_pkgs.core.structured_logging import configure_structured_logging
from registry_pkgs.database.leased_job import Lease, LeasedJobRunner
from registry_pkgs.database.mongodb import close_mongodb, init_mongodb
from registry_pkgs.database.skill_scan_job_repository import (
    SKILL_SCAN_JOB_MAX_ATTEMPTS,
    claimable_skill_scan_job_filter,
    skill_scan_job_repository,
)
from registry_pkgs.models.enums import SkillScanJobStatus
from registry_pkgs.models.skill_scan_job import SkillScanJob
from registry_pkgs.models.skill_scan_result import MAX_ERROR_MESSAGE_CHARS
from registry_pkgs.telemetry import setup_metrics, setup_tracing, shutdown_telemetry
from registry_pkgs.telemetry.skill_scan_metrics import initialize_skill_scan_metrics, record_job_finished
from skill_scan_worker.config import settings
from skill_scan_worker.job import execute_job
from skill_scan_worker.state import settle_or_requeue
from skill_scan_worker.sweeper import OrphanSweeper, fail_exhausted_jobs
from skill_scan_worker.toolset import publish_toolset

logger = logging.getLogger("skill_scan_worker.main")

_SERVICE_NAME = "skill-scan-worker"


def _initialize_telemetry() -> None:
    """Best-effort telemetry setup that should not block the worker from starting."""
    try:
        settings.configure_logging("skill_scan_worker")
    except Exception:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
        )
        logger.warning("Failed to configure baseline logging", exc_info=True)
    try:
        configure_structured_logging(
            "skill_scan_worker",
            "registry_pkgs",
            service_name=_SERVICE_NAME,
            service_version=settings.telemetry_config.build_version,
        )
    except Exception:
        logger.warning("Failed to configure structured logging", exc_info=True)
    try:
        setup_metrics(_SERVICE_NAME, settings.telemetry_config)
        initialize_skill_scan_metrics(settings.telemetry_config)
    except Exception:
        logger.warning("Failed to initialize metrics", exc_info=True)
    try:
        setup_tracing(_SERVICE_NAME, settings.telemetry_config)
    except Exception:
        logger.warning("Failed to initialize tracing", exc_info=True)


def _shutdown_telemetry_safe() -> None:
    try:
        shutdown_telemetry()
    except Exception:
        logger.warning("Failed to shutdown telemetry", exc_info=True)


async def _claim(owner: str) -> tuple[SkillScanJob, Lease] | None:
    now = datetime.now(UTC)
    # Resetting rerequested at claim is correct: this scan reads the latest content, so earlier requests are covered.
    return await skill_scan_job_repository.claim(
        owner=owner,
        claimable_filter=claimable_skill_scan_job_filter(now),
        set_fields={"status": SkillScanJobStatus.SCANNING.value, "startedAt": now, "rerequested": False},
        inc={"attemptCount": 1},
        sort=[("requestedAt", 1)],
    )


async def _on_execution_error(job: SkillScanJob, lease: Lease, exc: Exception) -> None:
    """Record internal_error and release the lease so the job retries, fails, or (if re-requested) requeues."""
    now = datetime.now(UTC)
    error = str(exc)[:MAX_ERROR_MESSAGE_CHARS]

    if job.attemptCount < SKILL_SCAN_JOB_MAX_ATTEMPTS:
        # Below the cap: just retry. The next claim resets rerequested, so no request is dropped.
        ok = await skill_scan_job_repository.transition(
            lease,
            {"status": SkillScanJobStatus.PENDING.value, "errorCode": "internal_error", "error": error},
            release=True,
        )
        record_job_finished("retried" if ok else "lease_lost")
        return

    # At the attempt cap: fail, unless a request arrived during this attempt — then requeue with fresh attempts.
    result = await settle_or_requeue(
        lease,
        terminal_fields={
            "status": SkillScanJobStatus.FAILED.value,
            "errorCode": "internal_error",
            "error": error,
            "finishedAt": now,
        },
        requeue_fields={"status": SkillScanJobStatus.PENDING.value, "attemptCount": 0, "rerequested": False},
        requeue_unset=["errorCode", "error"],
    )
    record_job_finished({"terminal": "failed", "requeued": "requeued", "lease_lost": "lease_lost"}[result])
    if result == "lease_lost":
        logger.warning("Lost lease finalizing errored skill scan job %s", lease.doc_id)


async def main() -> None:
    """Worker entrypoint: init shared resources → run N job runners + orphan sweep → graceful shutdown."""
    _initialize_telemetry()
    logger.info("Starting skill-scan worker")

    await init_mongodb(settings.mongo_config)
    owner = f"skill-scan-worker-{uuid4()}"

    # Validates ENABLED_ADAPTERS (raises → non-zero exit) before any job is claimed.
    await publish_toolset(owner)

    runners = [
        LeasedJobRunner(
            name=f"skill-scan-runner-{i}",
            claim=_claim,
            execute=execute_job,
            on_execution_error=_on_execution_error,
            before_poll=fail_exhausted_jobs if i == 0 else None,
            repository=skill_scan_job_repository,
            poll_interval=timedelta(seconds=settings.poll_interval_seconds),
            owner=owner,
        )
        for i in range(settings.concurrency)
    ]
    orphan_sweeper = OrphanSweeper(owner, settings.orphan_sweep_interval_seconds)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        for runner in runners:
            await runner.start()
        await orphan_sweeper.start()
        await stop_event.wait()
    finally:
        # Cancels in-flight executions; the cancellation path in job.py kills tool processes and requeues.
        await asyncio.gather(
            *(runner.shutdown() for runner in runners), orphan_sweeper.shutdown(), return_exceptions=True
        )
        await close_mongodb()
        _shutdown_telemetry_safe()


if __name__ == "__main__":
    asyncio.run(main())
