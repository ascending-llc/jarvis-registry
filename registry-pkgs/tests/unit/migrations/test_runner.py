"""Tests for migration planning and the `up` / `wait` / `status` commands."""

import asyncio
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymongo.errors import DuplicateKeyError

from registry_pkgs.migrations import lock as lock_module
from registry_pkgs.migrations import runner
from registry_pkgs.migrations.discovery import MigrationScript
from registry_pkgs.migrations.records import MigrationRecord
from registry_pkgs.migrations.runner import EXIT_FAILURE, EXIT_OK, build_plan

_APPLIED_AT = datetime(2026, 10, 1, tzinfo=UTC)


def _script(version: str, checksum: str | None = None, up: AsyncMock | None = None) -> MigrationScript:
    return MigrationScript(
        version=version,
        name=f"name_{version}",
        path=Path(f"m{version}_name_{version}.py"),
        checksum=checksum or f"sum-{version}",
        up=up or AsyncMock(),
    )


def _record(version: str, checksum: str | None = None) -> MigrationRecord:
    return MigrationRecord(
        id=version,
        name=f"name_{version}",
        checksum=checksum or f"sum-{version}",
        applied_at=_APPLIED_AT,
        duration_ms=3,
        build_version="old-build",
    )


def _versions(scripts: list[MigrationScript]) -> list[str]:
    return [script.version for script in scripts]


class _FakeLock:
    """Stands in for MigrationLock; `events` records enter/exit so ordering can be asserted."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.lost = False

    async def __aenter__(self) -> "_FakeLock":
        self.events.append("lock-acquired")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.events.append("lock-released")


class TestBuildPlan:
    def test_nothing_applied(self) -> None:
        plan = build_plan([_script("0002"), _script("0001")], [])

        assert _versions(plan.pending) == ["0001", "0002"]
        assert plan.out_of_order == []
        assert plan.checksum_mismatches == []
        assert plan.unknown_records == []

    def test_all_applied(self) -> None:
        plan = build_plan([_script("0001"), _script("0002")], [_record("0001"), _record("0002")])

        assert plan.pending == []
        assert plan.out_of_order == []
        assert plan.checksum_mismatches == []
        assert plan.unknown_records == []

    def test_gap_is_pending_and_out_of_order(self) -> None:
        plan = build_plan(
            [_script("0001"), _script("0002"), _script("0003"), _script("0004")],
            [_record("0003"), _record("0001")],
        )

        assert _versions(plan.pending) == ["0002", "0004"]
        assert _versions(plan.out_of_order) == ["0002"]

    def test_checksum_mismatch(self) -> None:
        plan = build_plan([_script("0001", "new"), _script("0002")], [_record("0001", "old"), _record("0002")])

        assert len(plan.checksum_mismatches) == 1
        mismatch = plan.checksum_mismatches[0]
        assert (mismatch.script.version, mismatch.script.checksum, mismatch.record.checksum) == ("0001", "new", "old")
        assert plan.pending == []

    def test_unknown_record(self) -> None:
        plan = build_plan([_script("0001"), _script("0002")], [_record("0009"), _record("0001")])

        assert [record.id for record in plan.unknown_records] == ["0009"]
        assert _versions(plan.pending) == ["0002"]
        # 0009 is not known in code, so it does not make 0002 out of order.
        assert plan.out_of_order == []


@pytest.fixture
def events() -> list[str]:
    return []


@pytest.fixture
def fake_lock(events: list[str]) -> Iterator[_FakeLock]:
    lock = _FakeLock(events)
    with patch.object(runner, "MigrationLock", return_value=lock):
        yield lock


def _patch_io(
    events: list[str],
    scripts: list[MigrationScript],
    records: list[MigrationRecord],
    insert_side_effect: object = None,
) -> tuple[MagicMock, AsyncMock, AsyncMock]:
    async def read(_db: object) -> list[MigrationRecord]:
        events.append("read-records")
        return records

    async def insert(_db: object, record: MigrationRecord) -> None:
        events.append(f"record-{record.id}")
        if isinstance(insert_side_effect, BaseException):
            raise insert_side_effect

    return (
        patch.object(runner, "discover_migrations", return_value=scripts),
        patch.object(runner, "read_records", side_effect=read),
        patch.object(runner, "insert_record", side_effect=insert),
    )


def _tracking_up(events: list[str], version: str, error: BaseException | None = None) -> AsyncMock:
    async def run(_db: object) -> None:
        events.append(f"up-{version}")
        if error is not None:
            raise error

    return AsyncMock(side_effect=run)


class TestUp:
    @pytest.mark.asyncio
    async def test_runs_pending_in_order_recording_each_after_its_up(
        self, events: list[str], fake_lock: _FakeLock
    ) -> None:
        scripts = [_script(v, up=_tracking_up(events, v)) for v in ("0003", "0001", "0002")]
        discover, read, insert = _patch_io(events, sorted(scripts, key=lambda s: s.version), [_record("0001")])

        with discover, read, insert as insert_mock:
            code = await runner.up(MagicMock(), "build-42")

        assert code == EXIT_OK
        assert events == [
            "lock-acquired",
            "read-records",
            "up-0002",
            "record-0002",
            "up-0003",
            "record-0003",
            "lock-released",
        ]
        recorded = insert_mock.await_args_list[0].args[1]
        assert (recorded.id, recorded.name, recorded.checksum) == ("0002", "name_0002", "sum-0002")
        assert recorded.build_version == "build-42"
        assert recorded.applied_at.tzinfo is UTC
        assert recorded.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_nothing_pending(self, events: list[str], fake_lock: _FakeLock) -> None:
        discover, read, insert = _patch_io(events, [_script("0001")], [_record("0001")])

        with discover, read, insert as insert_mock:
            assert await runner.up(MagicMock(), "b") == EXIT_OK

        insert_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reads_records_only_after_acquiring_lock(self, events: list[str], fake_lock: _FakeLock) -> None:
        discover, read, insert = _patch_io(events, [_script("0001")], [])

        with discover, read, insert:
            await runner.up(MagicMock(), "b")

        assert events.index("lock-acquired") < events.index("read-records")

    @pytest.mark.asyncio
    async def test_checksum_mismatch_runs_nothing_and_names_remediations(
        self, events: list[str], fake_lock: _FakeLock, caplog: pytest.LogCaptureFixture
    ) -> None:
        first = _script("0001", "edited", up=_tracking_up(events, "0001"))
        second = _script("0002", up=_tracking_up(events, "0002"))
        discover, read, insert = _patch_io(events, [first, second], [_record("0001", "original")])

        with discover, read, insert as insert_mock:
            code = await runner.up(MagicMock(), "b")

        assert code == EXIT_FAILURE
        assert "up-0002" not in events
        insert_mock.assert_not_awaited()
        errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert "0001" in errors[0] and "original" in errors[0] and "edited" in errors[0]
        assert "revert" in errors[0]
        assert "`migration-edit-approved` label" in errors[0]
        assert 'db.registry_migrations.deleteOne({_id: "0001"})' in errors[0]
        assert events[-1] == "lock-released"

    @pytest.mark.asyncio
    async def test_migration_exception_records_nothing_for_it_and_stops(
        self, events: list[str], fake_lock: _FakeLock, caplog: pytest.LogCaptureFixture
    ) -> None:
        scripts = [
            _script("0001", up=_tracking_up(events, "0001")),
            _script("0002", up=_tracking_up(events, "0002", RuntimeError("bad data"))),
            _script("0003", up=_tracking_up(events, "0003")),
        ]
        discover, read, insert = _patch_io(events, scripts, [])

        with discover, read, insert:
            code = await runner.up(MagicMock(), "b")

        assert code == EXIT_FAILURE
        assert events == ["lock-acquired", "read-records", "up-0001", "record-0001", "up-0002", "lock-released"]
        failure = next(r for r in caplog.records if "0002" in r.getMessage() and r.levelno == logging.ERROR)
        assert failure.exc_info is not None  # logged with traceback

    @pytest.mark.asyncio
    async def test_duplicate_key_on_record_insert_is_a_lost_lock(
        self, events: list[str], fake_lock: _FakeLock, caplog: pytest.LogCaptureFixture
    ) -> None:
        scripts = [_script("0001", up=_tracking_up(events, "0001")), _script("0002", up=_tracking_up(events, "0002"))]
        discover, read, insert = _patch_io(events, scripts, [], insert_side_effect=DuplicateKeyError("dup"))

        with discover, read, insert:
            code = await runner.up(MagicMock(), "b")

        assert code == EXIT_FAILURE
        assert "up-0002" not in events
        assert "lost the migration lock" in caplog.text

    @pytest.mark.asyncio
    async def test_lost_lock_stops_before_next_migration(self, events: list[str], fake_lock: _FakeLock) -> None:
        async def first_up(_db: object) -> None:
            events.append("up-0001")

        async def insert(_db: object, record: MigrationRecord) -> None:
            events.append(f"record-{record.id}")
            fake_lock.lost = True  # the heartbeat noticed right after the first record

        scripts = [
            _script("0001", up=AsyncMock(side_effect=first_up)),
            _script("0002", up=_tracking_up(events, "0002")),
        ]
        discover, read, _ = _patch_io(events, scripts, [])

        with discover, read, patch.object(runner, "insert_record", side_effect=insert):
            code = await runner.up(MagicMock(), "b")

        assert code == EXIT_FAILURE
        assert events == ["lock-acquired", "read-records", "up-0001", "record-0001", "lock-released"]

    @pytest.mark.asyncio
    async def test_lost_lock_during_migration_skips_its_record(self, events: list[str], fake_lock: _FakeLock) -> None:
        async def losing_up(_db: object) -> None:
            events.append("up-0001")
            fake_lock.lost = True

        discover, read, insert = _patch_io(events, [_script("0001", up=AsyncMock(side_effect=losing_up))], [])

        with discover, read, insert as insert_mock:
            code = await runner.up(MagicMock(), "b")

        assert code == EXIT_FAILURE
        insert_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_heartbeat_losing_real_lock_stops_the_run(self, events: list[str]) -> None:
        """End to end with the real MigrationLock: a heartbeat with matched_count == 0 stops the runner."""
        lock_collection = MagicMock(
            find_one_and_update=AsyncMock(return_value=None),
            update_one=AsyncMock(return_value=MagicMock(matched_count=0)),
            delete_one=AsyncMock(),
        )
        db = MagicMock()
        db.__getitem__ = MagicMock(return_value=lock_collection)

        async def slow_up(_db: object) -> None:
            events.append("up-0001")
            for _ in range(5):
                await asyncio.sleep(0)  # let the heartbeat tick

        scripts = [_script("0001", up=AsyncMock(side_effect=slow_up)), _script("0002", up=_tracking_up(events, "0002"))]
        discover, read, insert = _patch_io(events, scripts, [])

        with discover, read, insert as insert_mock, patch.object(lock_module, "LOCK_HEARTBEAT_SECONDS", 0):
            code = await runner.up(db, "b")

        assert code == EXIT_FAILURE
        assert "up-0002" not in events
        insert_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_warns_about_unknown_and_out_of_order_then_continues(
        self, events: list[str], fake_lock: _FakeLock, caplog: pytest.LogCaptureFixture
    ) -> None:
        scripts = [_script("0001", up=_tracking_up(events, "0001")), _script("0002")]
        discover, read, insert = _patch_io(events, scripts, [_record("0002"), _record("0009")])

        with discover, read, insert:
            code = await runner.up(MagicMock(), "b")

        assert code == EXIT_OK
        assert "up-0001" in events
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("0009" in w and "not in this image" in w for w in warnings)
        assert any("0001" in w and "out of order" in w for w in warnings)


class TestWait:
    @pytest.mark.asyncio
    async def test_exits_when_nothing_pending(self) -> None:
        with (
            patch.object(runner, "discover_migrations", return_value=[_script("0001")]),
            patch.object(runner, "read_records", new_callable=AsyncMock, return_value=[_record("0001")]),
            patch.object(runner.asyncio, "sleep", new_callable=AsyncMock) as sleep,
        ):
            assert await runner.wait(MagicMock()) == EXIT_OK

        sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_polls_while_pending(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger=runner.__name__)
        with (
            patch.object(runner, "discover_migrations", return_value=[_script("0001"), _script("0002")]),
            patch.object(
                runner,
                "read_records",
                new_callable=AsyncMock,
                side_effect=[[], [_record("0001")], [_record("0001"), _record("0002")]],
            ),
            patch.object(runner.asyncio, "sleep", new_callable=AsyncMock) as sleep,
        ):
            assert await runner.wait(MagicMock()) == EXIT_OK

        assert [call.args[0] for call in sleep.await_args_list] == [lock_module.POLL_INTERVAL_SECONDS] * 2
        assert "['0001', '0002']" in caplog.text
        assert "['0002']" in caplog.text

    @pytest.mark.asyncio
    async def test_fails_on_checksum_mismatch_with_remediation(self, caplog: pytest.LogCaptureFixture) -> None:
        with (
            patch.object(runner, "discover_migrations", return_value=[_script("0001", "edited"), _script("0002")]),
            patch.object(runner, "read_records", new_callable=AsyncMock, return_value=[_record("0001", "original")]),
            patch.object(runner.asyncio, "sleep", new_callable=AsyncMock) as sleep,
        ):
            assert await runner.wait(MagicMock()) == EXIT_FAILURE

        sleep.assert_not_awaited()
        assert "`migration-edit-approved` label" in caplog.text
        assert 'db.registry_migrations.deleteOne({_id: "0001"})' in caplog.text


class TestStatus:
    @pytest.mark.asyncio
    async def test_prints_every_state_and_unknown_records(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch.object(runner, "discover_migrations", return_value=[_script("0001"), _script("0002")]),
            patch.object(
                runner, "read_records", new_callable=AsyncMock, return_value=[_record("0001"), _record("0009")]
            ),
        ):
            code = await runner.status(MagicMock())

        lines = capsys.readouterr().out.splitlines()
        assert code == EXIT_OK
        assert lines == [
            f"0001  name_0001  applied {_APPLIED_AT.isoformat()} old-build",
            "0002  name_0002  pending",
            f"0009  name_0009  UNKNOWN (recorded, not in code) applied {_APPLIED_AT.isoformat()}",
        ]

    @pytest.mark.asyncio
    async def test_exits_1_only_on_mismatch(
        self, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
    ) -> None:
        with (
            patch.object(runner, "discover_migrations", return_value=[_script("0001", "edited")]),
            patch.object(runner, "read_records", new_callable=AsyncMock, return_value=[_record("0001", "original")]),
        ):
            code = await runner.status(MagicMock())

        assert code == EXIT_FAILURE
        assert "0001  name_0001  CHECKSUM MISMATCH (recorded original, current edited)" in capsys.readouterr().out
        assert "migration-edit-approved" in caplog.text

    @pytest.mark.asyncio
    async def test_pending_only_is_success(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch.object(runner, "discover_migrations", return_value=[_script("0001")]),
            patch.object(runner, "read_records", new_callable=AsyncMock, return_value=[]),
        ):
            assert await runner.status(MagicMock()) == EXIT_OK
