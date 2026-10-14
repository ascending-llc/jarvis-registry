"""Exercise the backfill's safety checks and conditional MongoDB operations without I/O."""

import argparse
import logging
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from bson import ObjectId
from pymongo.errors import OperationFailure, PyMongoError

from scripts import backfill_user_openid_issuer as backfill
from tests.unit.scripts.memory_mongo import MemoryDatabase

ISSUER = "https://login.microsoftonline.com/11111111-2222-3333-4444-555555555555/v2.0"
OTHER_ISSUER = "https://login.microsoftonline.com/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/v2.0"
USER_A = ObjectId("111111111111111111111111")
USER_B = ObjectId("222222222222222222222222")


def _cursor(ids: tuple[ObjectId, ...]) -> MagicMock:
    cursor = MagicMock()
    cursor.__aiter__.return_value = [{"_id": user_id} for user_id in ids]
    return cursor


def _database(issuer_ids: tuple[ObjectId, ...] = (), empty_id_ids: tuple[ObjectId, ...] = ()) -> MagicMock:
    db = MagicMock()
    users = db["users"]
    users.distinct = AsyncMock(return_value=[ISSUER])
    users.find.side_effect = lambda query, projection: _cursor(
        empty_id_ids if query == {"openidId": ""} else issuer_ids
    )
    users.update_many = AsyncMock()
    return db


@pytest.mark.parametrize("suffix", ["", "/", "///"])
def test_normalize_issuer_strips_trailing_slashes(suffix: str) -> None:
    assert backfill.normalize_issuer(ISSUER + suffix) == ISSUER


@pytest.mark.parametrize(
    "issuer",
    [
        "https://accounts.google.com",
        ISSUER.replace("https://", "http://"),
        ISSUER.replace("/v2.0", "/v1.0"),
        ISSUER.replace("11111111-2222-3333-4444-555555555555", "common"),
        ISSUER.replace("11111111-2222-3333-4444-555555555555", "-" * 36),
        ISSUER.replace("login.microsoftonline.com", "login.microsoftonline.com.example.com"),
        OTHER_ISSUER.replace("aaaaaaaa", "AAAAAAAA"),
        ISSUER + "?query=true",
        ISSUER + "#fragment",
        " " + ISSUER,
    ],
)
def test_rejects_non_entra_or_noncanonical_tenant(issuer: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        backfill.normalize_issuer(issuer)


@pytest.mark.parametrize("existing", [[OTHER_ISSUER], [ISSUER, OTHER_ISSUER], [None], [""], [ISSUER, None]])
async def test_conflicting_or_null_issuers_abort_before_any_updates(existing: list[str | None]) -> None:
    db = _database((USER_A,), (USER_B,))
    db["users"].distinct.return_value = existing

    with pytest.raises(backfill.BackfillError, match="different or null issuer"):
        await backfill.run_backfill(db, ISSUER, dry_run=False, force=True)

    db["users"].find.assert_not_called()
    db["users"].update_many.assert_not_awaited()


@pytest.mark.parametrize("existing", [[], [ISSUER]])
async def test_collects_ids_using_exact_spec_filters_and_projection(existing: list[str]) -> None:
    db = _database((USER_B, USER_A), (USER_B,))
    db["users"].distinct.return_value = existing

    changes = await backfill.collect_changes(db, ISSUER)

    assert changes == backfill.BackfillChanges((USER_A, USER_B), (USER_B,))
    db["users"].distinct.assert_awaited_once_with("openidIssuer", {"provider": "openid"})
    assert db["users"].find.call_args_list == [
        call(
            {
                "provider": "openid",
                "openidIssuer": {"$exists": False},
                "$or": [{"openidId": {"$nin": ["", None]}}, {"idOnTheSource": {"$exists": True}}],
            },
            {"_id": 1},
        ),
        call({"openidId": ""}, {"_id": 1}),
    ]


async def test_dry_run_only_reads_and_reports_each_step_ids_and_counts(caplog: pytest.LogCaptureFixture) -> None:
    db = _database((USER_A,), (USER_B,))
    with caplog.at_level(logging.INFO):
        assert await backfill.run_backfill(db, ISSUER, dry_run=True, force=False) == (0, 0)

    assert str(USER_A) in caplog.text
    assert str(USER_B) in caplog.text
    assert "openidIssuer: planned=1" in caplog.text
    assert "Empty openidId: planned=1" in caplog.text
    assert {entry[0] for entry in db["users"].method_calls} == {"distinct", "find"}


async def test_backfill_updates_documents_once_and_preserves_unrelated_fields() -> None:
    # Each pair states the expected document fields independently of the script's query.
    cases = [
        (
            {"provider": "openid", "openidId": "subject-a"},
            {"provider": "openid", "openidId": "subject-a", "openidIssuer": ISSUER},
        ),
        (
            {"provider": "openid", "idOnTheSource": "source-b"},
            {"provider": "openid", "idOnTheSource": "source-b", "openidIssuer": ISSUER},
        ),
        (
            {"provider": "openid", "openidId": "", "idOnTheSource": "source-c"},
            {"provider": "openid", "idOnTheSource": "source-c", "openidIssuer": ISSUER},
        ),
        (
            {"provider": "openid", "openidId": "existing-subject", "openidIssuer": ISSUER},
            {"provider": "openid", "openidId": "existing-subject", "openidIssuer": ISSUER},
        ),
        (
            {"provider": "local", "openidId": "local-subject"},
            {"provider": "local", "openidId": "local-subject"},
        ),
        (
            {"provider": "google", "googleId": "google-subject", "openidIssuer": "https://accounts.google.com"},
            {"provider": "google", "googleId": "google-subject", "openidIssuer": "https://accounts.google.com"},
        ),
        ({"provider": "openid"}, {"provider": "openid"}),
        (
            {"provider": "openid", "openidId": None},
            {"provider": "openid", "openidId": None},
        ),
        ({"provider": "openid", "openidId": ""}, {"provider": "openid"}),
        # The spec tests source-field existence, so explicit null/empty values qualify.
        (
            {"provider": "openid", "idOnTheSource": None},
            {"provider": "openid", "idOnTheSource": None, "openidIssuer": ISSUER},
        ),
        (
            {"provider": "openid", "idOnTheSource": ""},
            {"provider": "openid", "idOnTheSource": "", "openidIssuer": ISSUER},
        ),
        ({"provider": "local", "openidId": ""}, {"provider": "local"}),
    ]
    initial = []
    expected = []
    for index, (before, after) in enumerate(cases, start=1):
        unchanged = {
            "_id": ObjectId(f"{index:024x}"),
            "username": f"fixture-{index}",
            "email": f"fixture-{index}@example.invalid",
            "emailVerified": False,
        }
        initial.append(unchanged | before)
        expected.append(unchanged | after)
    db = MemoryDatabase({"users": initial})
    before_dry_run = db.snapshot()

    assert await backfill.run_backfill(db, ISSUER, dry_run=True, force=True) == (0, 0)
    assert db.snapshot() == before_dry_run

    assert await backfill.run_backfill(db, ISSUER, dry_run=False, force=True) == (5, 3)
    assert db.snapshot() == {"users": expected}
    after_first_run = db.snapshot()

    assert await backfill.run_backfill(db, ISSUER, dry_run=False, force=True) == (0, 0)
    assert db.snapshot() == after_first_run


async def test_apply_keeps_preconditions_and_restricts_updates_to_previewed_ids(
    caplog: pytest.LogCaptureFixture,
) -> None:
    db = _database((USER_A, USER_B), (USER_B,))
    db["users"].update_many.side_effect = [MagicMock(modified_count=1), MagicMock(modified_count=1)]
    with caplog.at_level(logging.INFO):
        result = await backfill.apply_changes(db, ISSUER, backfill.BackfillChanges((USER_A, USER_B), (USER_B,)))

    assert result == (1, 1)
    assert db["users"].update_many.await_args_list == [
        call(
            {
                "provider": "openid",
                "openidIssuer": {"$exists": False},
                "$or": [{"openidId": {"$nin": ["", None]}}, {"idOnTheSource": {"$exists": True}}],
                "_id": {"$in": [USER_A, USER_B]},
            },
            {"$set": {"openidIssuer": ISSUER}},
        ),
        call({"openidId": "", "_id": {"$in": [USER_B]}}, {"$unset": {"openidId": ""}}),
    ]
    assert "openidIssuer: planned=2 modified=1" in caplog.text


@pytest.mark.parametrize(
    "current_ids,current_empty_ids",
    [((USER_A, USER_B), ()), ((), ()), ((USER_A,), (USER_B,))],
)
async def test_apply_aborts_when_plan_changed(
    current_ids: tuple[ObjectId, ...], current_empty_ids: tuple[ObjectId, ...]
) -> None:
    db = _database(current_ids, current_empty_ids)

    with pytest.raises(backfill.BackfillError, match="changed after the preview"):
        await backfill.apply_changes(db, ISSUER, backfill.BackfillChanges((USER_A,), ()))

    db["users"].update_many.assert_not_awaited()


async def test_apply_rechecks_single_issuer_before_writing() -> None:
    db = _database((USER_A,), ())
    changes = await backfill.collect_changes(db, ISSUER)
    db["users"].distinct.return_value = [ISSUER, OTHER_ISSUER]

    with pytest.raises(backfill.BackfillError, match="different or null issuer"):
        await backfill.apply_changes(db, ISSUER, changes)

    db["users"].update_many.assert_not_awaited()


async def test_no_remaining_candidates_returns_zero_without_writing_or_prompting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _database()
    prompt = MagicMock()
    monkeypatch.setattr("builtins.input", prompt)

    assert await backfill.run_backfill(db, ISSUER, dry_run=False, force=False) == (0, 0)

    db["users"].update_many.assert_not_awaited()
    prompt.assert_not_called()


@pytest.mark.parametrize("answer", ["no", "", "y"])
async def test_declining_confirmation_makes_no_changes(answer: str, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _database((USER_A,), ())
    monkeypatch.setattr("builtins.input", lambda _: answer)

    assert await backfill.run_backfill(db, ISSUER, dry_run=False, force=False) == (0, 0)

    db["users"].update_many.assert_not_awaited()


async def test_accepting_confirmation_applies_and_rechecks(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _database((USER_A,), ())
    db["users"].update_many.return_value.modified_count = 1
    monkeypatch.setattr("builtins.input", lambda _: "yes")

    assert await backfill.run_backfill(db, ISSUER, dry_run=False, force=False) == (1, 0)

    assert db["users"].distinct.await_count == 2
    db["users"].update_many.assert_awaited_once()


async def test_force_skips_only_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _database((USER_A,), ())
    db["users"].update_many.return_value.modified_count = 1
    prompt = MagicMock()
    monkeypatch.setattr("builtins.input", prompt)

    assert await backfill.run_backfill(db, ISSUER, dry_run=False, force=True) == (1, 0)

    prompt.assert_not_called()
    assert db["users"].distinct.await_count == 2


async def test_missing_confirmation_input_aborts(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _database((USER_A,), ())
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=EOFError))

    with pytest.raises(backfill.BackfillError, match="Confirmation unavailable"):
        await backfill.run_backfill(db, ISSUER, dry_run=False, force=False)

    db["users"].update_many.assert_not_awaited()


async def test_first_write_failure_prevents_second_step() -> None:
    db = _database((USER_A,), (USER_B,))
    db["users"].update_many.side_effect = OperationFailure("write failed")

    with pytest.raises(OperationFailure):
        await backfill.apply_changes(db, ISSUER, backfill.BackfillChanges((USER_A,), (USER_B,)))

    db["users"].update_many.assert_awaited_once()


async def test_second_write_failure_keeps_actual_first_step_count(caplog: pytest.LogCaptureFixture) -> None:
    db = _database((USER_A,), (USER_B,))
    db["users"].update_many.side_effect = [MagicMock(modified_count=1), OperationFailure("write failed")]

    with caplog.at_level(logging.INFO), pytest.raises(OperationFailure):
        await backfill.apply_changes(db, ISSUER, backfill.BackfillChanges((USER_A,), (USER_B,)))

    assert "openidIssuer: planned=1 modified=1" in caplog.text


@pytest.mark.parametrize("fail_ping", [False, True])
async def test_entrypoint_uses_raw_client_and_always_closes(fail_ping: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.admin.command = AsyncMock(side_effect=PyMongoError("failed") if fail_ping else None)
    client.close = AsyncMock()
    client.__getitem__.return_value = _database()
    create_client = MagicMock(return_value=(client, "test_backfill"))
    monkeypatch.setattr(backfill, "create_mongo_client", create_client)
    settings = MagicMock()
    monkeypatch.setattr(backfill, "MongoSettings", MagicMock(return_value=settings))
    args = argparse.Namespace(issuer=ISSUER, dry_run=True, force=False)

    if fail_ping:
        with pytest.raises(PyMongoError):
            await backfill.async_main(args)
    else:
        assert await backfill.async_main(args) == 0

    create_client.assert_called_once_with(settings.mongo_config)
    client.admin.command.assert_awaited_once_with("ping")
    client.close.assert_awaited_once()
    assert {entry[0] for entry in client.method_calls} == {"admin.command", "close"}


@pytest.mark.parametrize("argv", [[], ["--issuer", "not-a-valid-issuer"]])
def test_invalid_arguments_fail_before_connecting(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    create_client = MagicMock()
    monkeypatch.setattr(backfill, "create_mongo_client", create_client)

    with pytest.raises(SystemExit) as exc:
        backfill.main(argv)

    assert exc.value.code == 2
    create_client.assert_not_called()


@pytest.mark.parametrize("error_type", [PyMongoError, ValueError])
def test_cli_redacts_driver_and_settings_errors(
    error_type: type[Exception], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(backfill, "async_main", AsyncMock(side_effect=error_type("sensitive-connection-or-document")))

    assert backfill.main(["--issuer", ISSUER, "--force"]) == 1

    assert "sensitive-connection-or-document" not in caplog.text
    assert error_type.__name__ in caplog.text


def test_cli_normalizes_issuer_and_defaults_to_confirmed_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    entrypoint = AsyncMock(return_value=0)
    monkeypatch.setattr(backfill, "async_main", entrypoint)

    assert backfill.main(["--issuer", ISSUER + "/"]) == 0

    args = entrypoint.await_args.args[0]
    assert args.issuer == ISSUER
    assert args.dry_run is False
    assert args.force is False
