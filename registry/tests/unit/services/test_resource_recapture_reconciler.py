"""Unit tests for the leader-leased resource-recapture reconciler (pass logic only)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from beanie import PydanticObjectId

from registry.services import resource_recapture_reconciler as rrr
from registry.services.resource_recapture_reconciler import (
    RESOURCE_RECAPTURE_ATTEMPTS,
    RESOURCE_RECAPTURE_PENDING_AT,
    ResourceRecaptureReconciler,
    needs_resource_recapture,
)
from registry_pkgs.models import ExtendedMCPServer
from registry_pkgs.models.mcp_resource_capture import RESOURCE_CAPTURE_KEY, create_resource_capture

pytestmark = pytest.mark.asyncio

_SHARED = {"url": "https://example.test/mcp", "apiKey": {"key": "shared"}, "resources": [{"uri": "files://a"}]}


def _signed(config: dict) -> dict:
    config = dict(config)
    config[RESOURCE_CAPTURE_KEY] = create_resource_capture(config, config["resources"])
    return config


def _reconciler(*, reindex_active: bool = False, refresh: AsyncMock | None = None) -> ResourceRecaptureReconciler:
    watcher = Mock()
    watcher.is_active.return_value = reindex_active
    service = Mock(refresh_server_capabilities=refresh or AsyncMock())
    return ResourceRecaptureReconciler(
        server_service=service, embedding_maintenance_watcher=watcher, lease_owner="owner-1"
    )


def _doc(config: dict, attempts: int | None = None) -> dict:
    doc = {"_id": PydanticObjectId(), "config": config, RESOURCE_RECAPTURE_PENDING_AT: datetime(2026, 1, 1, tzinfo=UTC)}
    if attempts is not None:
        doc[RESOURCE_RECAPTURE_ATTEMPTS] = attempts
    return doc


def _collection(docs: list[dict]) -> Mock:
    cursor = Mock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=docs)
    return Mock(find=Mock(return_value=cursor), update_one=AsyncMock())


def _patch(collection: Mock):
    return patch.object(ExtendedMCPServer, "get_pymongo_collection", return_value=collection)


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (_SHARED, True),
        (_signed(_SHARED), False),
        ({**_SHARED, "resources": []}, False),
        ({**_SHARED, "requiresOAuth": True}, False),
        ({**_SHARED, "oauth": {}, "runtimeAccess": {"roleArn": "x"}}, True),
        (None, False),
    ],
)
async def test_needs_resource_recapture(config: dict | None, expected: bool) -> None:
    assert needs_resource_recapture(config) is expected


async def test_pass_skips_during_reindex() -> None:
    collection = _collection([_doc(_SHARED)])
    rec = _reconciler(reindex_active=True)
    with _patch(collection):
        assert await rec._run_pass() is False
    collection.find.assert_not_called()


async def test_successful_recapture_clears_marker_with_compare_and_clear() -> None:
    doc = _doc(_SHARED)
    refresh = AsyncMock(return_value={"status": "success", "server": SimpleNamespace(config=_signed(_SHARED))})
    collection = _collection([doc])
    rec = _reconciler(refresh=refresh)
    with _patch(collection):
        assert await rec._run_pass() is False

    refresh.assert_awaited_once_with(str(doc["_id"]))
    query, update = collection.update_one.await_args.args
    assert query == {"_id": doc["_id"], RESOURCE_RECAPTURE_PENDING_AT: doc[RESOURCE_RECAPTURE_PENDING_AT]}
    assert update == {"$unset": {RESOURCE_RECAPTURE_PENDING_AT: "", RESOURCE_RECAPTURE_ATTEMPTS: ""}}


async def test_already_signed_server_is_cleared_without_refresh() -> None:
    doc = _doc(_signed(_SHARED))
    refresh = AsyncMock()
    collection = _collection([doc])
    with _patch(collection):
        await _reconciler(refresh=refresh)._run_pass()

    refresh.assert_not_awaited()
    assert "$unset" in collection.update_one.await_args.args[1]


@pytest.mark.parametrize(
    "refresh",
    [
        AsyncMock(return_value={"status": "failed", "server": SimpleNamespace(config=_SHARED)}),
        # Tools refreshed but the resource listing failed: the old list keeps no proof.
        AsyncMock(return_value={"status": "success", "server": SimpleNamespace(config=_SHARED)}),
        AsyncMock(side_effect=RuntimeError("downstream unreachable")),
    ],
    ids=["failed", "unsigned", "raised"],
)
async def test_failed_recapture_requeues_with_backoff(refresh: AsyncMock) -> None:
    doc = _doc(_SHARED, attempts=2)
    collection = _collection([doc])
    with _patch(collection), patch.object(rrr, "datetime", wraps=datetime) as clock:
        now = datetime(2026, 6, 1, tzinfo=UTC)
        clock.now.return_value = now
        await _reconciler(refresh=refresh)._run_pass()

    query, update = collection.update_one.await_args.args
    assert query == {"_id": doc["_id"], RESOURCE_RECAPTURE_PENDING_AT: doc[RESOURCE_RECAPTURE_PENDING_AT]}
    # Third attempt: 5 min * 2**2.
    assert update == {
        "$set": {RESOURCE_RECAPTURE_PENDING_AT: now + timedelta(minutes=20), RESOURCE_RECAPTURE_ATTEMPTS: 3}
    }


async def test_gives_up_after_max_attempts() -> None:
    doc = _doc(_SHARED, attempts=rrr._MAX_ATTEMPTS - 1)
    collection = _collection([doc])
    with _patch(collection):
        await _reconciler(refresh=AsyncMock(side_effect=RuntimeError("down")))._run_pass()

    assert "$unset" in collection.update_one.await_args.args[1]


async def test_full_batch_resumes_immediately() -> None:
    docs = [_doc(_signed(_SHARED)) for _ in range(rrr._BATCH)]
    with _patch(_collection(docs)):
        assert await _reconciler()._run_pass() is True
