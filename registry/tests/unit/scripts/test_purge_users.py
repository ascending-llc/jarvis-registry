"""Safety, orchestration and stateful data tests without external I/O."""

import argparse
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from bson import ObjectId
from pymongo.errors import OperationFailure

from scripts import purge_users as purge
from tests.unit.scripts.memory_mongo import MemoryDatabase

USER_A = ObjectId("111111111111111111111111")
USER_B = ObjectId("222222222222222222222222")
SERVER_ID = ObjectId("333333333333333333333333")
AGENT_ID = ObjectId("444444444444444444444444")
ACL_ID = ObjectId("555555555555555555555555")
RECORD_ID = ObjectId("666666666666666666666666")
GENERATION = "777777777777777777777777"


def _cursor(documents: list[dict[str, Any]]) -> MagicMock:
    cursor = MagicMock()
    cursor.__aiter__.return_value = documents
    cursor.limit.return_value = cursor
    return cursor


def _database(scan_collections: tuple[str, ...] = ()) -> MagicMock:
    collections: dict[str, MagicMock] = {}

    def collection(name: str) -> MagicMock:
        if name not in collections:
            current = MagicMock()
            current.find.side_effect = lambda *_: _cursor([])
            current.find_one = AsyncMock(return_value=None)
            current.delete_many = AsyncMock(side_effect=lambda query: MagicMock(deleted_count=len(query["_id"]["$in"])))
            collections[name] = current
        return collections[name]

    db = MagicMock()
    db.__getitem__.side_effect = collection
    db.list_collection_names = AsyncMock(return_value=list(scan_collections))
    return db


def _plan(*, with_server: bool = False, **deletions: frozenset[ObjectId | str]) -> purge.PurgePlan:
    plan = purge.PurgePlan(
        targets=(USER_A,),
        deletions={name: frozenset() for name in purge.DELETE_ORDER},
        resources={"agents": {}, "mcpservers": {}},
    )
    plan.deletions.update(deletions)
    if with_server:
        plan.deletions["mcpservers"] = frozenset({SERVER_ID})
        plan.resources["mcpservers"][SERVER_ID] = {"_id": SERVER_ID, "author": USER_A, "serverName": "test-server"}
    return plan


def _assert_no_deletes(db: MagicMock) -> None:
    for name in purge.DELETE_ORDER:
        db[name].delete_many.assert_not_awaited()


@pytest.fixture
def purge_documents() -> tuple[MemoryDatabase, dict[str, list[dict[str, Any]]]]:
    """Declare deleted and retained records independently of the production cascade map."""
    other_user = ObjectId("888888888888888888888888")
    retained_user = {"_id": other_user, "provider": "google", "username": "keep-user", "emailVerified": True}
    documents = {
        "users": [
            {"_id": USER_A, "provider": "local"},
            {"_id": str(USER_B), "provider": "openid"},
            retained_user,
        ],
        "aclentries": [],
    }
    retained = {"users": [retained_user], "aclentries": []}
    for collection, field in (
        ("transactions", "user"),
        ("conversations", "user"),
        ("messages", "user"),
        ("files", "user"),
        ("sharedlinks", "user"),
        ("keys", "userId"),
        ("tokens", "userId"),
    ):
        keeper = {"_id": ObjectId(), field: other_user, "value": "keep"}
        documents[collection] = [
            {"_id": ObjectId(), field: USER_A, "value": "delete-object-reference"},
            {"_id": str(ObjectId()), field: str(USER_B), "value": "delete-string-reference"},
            keeper,
        ]
        retained[collection] = [keeper]

    for collection, resource_type in (("agents", "agent"), ("mcpservers", "mcpServer")):
        keeper = {"_id": ObjectId(), "author": other_user, "serverName": "keep-resource"}
        documents[collection] = [keeper]
        retained[collection] = [keeper]
        for index, author in enumerate((USER_A, str(USER_B))):
            resource_id = ObjectId()
            documents[collection].append(
                {
                    "_id": resource_id if index == 0 else str(resource_id),
                    "author": author,
                    "serverName": f"remove-{collection}-{index}",
                }
            )
            # Resource references use the opposite representation from their document _id.
            documents["aclentries"].append(
                {
                    "_id": ObjectId(),
                    "principalType": "user",
                    "principalId": str(USER_A) if index == 0 else USER_B,
                    "resourceType": "remoteAgent" if collection == "agents" and index else resource_type,
                    "resourceId": str(resource_id) if index == 0 else resource_id,
                }
            )
        audit_acl = {
            "_id": ObjectId(),
            "principalType": "user",
            "principalId": other_user,
            "resourceType": resource_type,
            "resourceId": keeper["_id"],
            "grantedBy": USER_A if collection == "agents" else str(USER_B),
        }
        documents["aclentries"].append(audit_acl)
        retained["aclentries"].append(audit_acl)

    # Direct principal grants also go away, even on resources outside the authored set.
    for principal_id in (USER_A, str(USER_B)):
        documents["aclentries"].append(
            {
                "_id": ObjectId(),
                "principalType": "user",
                "principalId": principal_id,
                "resourceType": "workflow",
                "resourceId": ObjectId(),
            }
        )
    return MemoryDatabase(documents), deepcopy(retained)


def _stateful_purge_args(*extra: str) -> argparse.Namespace:
    return purge.parse_args(["--user-id", str(USER_A), "--user-id", str(USER_B), "--skip-vector", "--force", *extra])


async def test_purge_cascades_actual_documents_and_preserves_unrelated_records(
    purge_documents: tuple[MemoryDatabase, dict[str, list[dict[str, Any]]]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db, retained = purge_documents
    original = db.snapshot()
    vectors = AsyncMock()
    monkeypatch.setattr(purge, "delete_vectors", vectors)

    await purge.run_purge(db, _stateful_purge_args())
    assert db.snapshot() == original
    assert "TOTAL: planned=26" in capsys.readouterr().out

    await purge.run_purge(db, _stateful_purge_args("--apply"))
    assert db.snapshot() == retained
    assert "users: deleted=2 planned=2" in capsys.readouterr().out

    await purge.run_purge(db, _stateful_purge_args("--apply"))
    assert db.snapshot() == retained
    assert "No matching records remain" in capsys.readouterr().out
    vectors.assert_not_awaited()


@pytest.mark.parametrize(
    ("failed_collection", "completed_collections"),
    [
        ("messages", ("transactions", "conversations")),
        (
            "mcpservers",
            (
                "transactions",
                "conversations",
                "messages",
                "files",
                "sharedlinks",
                "keys",
                "tokens",
                "aclentries",
                "agents",
            ),
        ),
    ],
)
async def test_purge_retry_rebuilds_plan_from_remaining_documents(
    purge_documents: tuple[MemoryDatabase, dict[str, list[dict[str, Any]]]],
    failed_collection: str,
    completed_collections: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db, retained = purge_documents
    partially_cleaned = db.snapshot()
    for collection in completed_collections:
        partially_cleaned[collection] = retained[collection]
    vectors = AsyncMock()
    monkeypatch.setattr(purge, "delete_vectors", vectors)

    # Only the failed write is replaced. Planning, filtering and all other writes operate on data.
    with monkeypatch.context() as failing_write:
        failing_write.setattr(
            db[failed_collection], "delete_many", AsyncMock(side_effect=OperationFailure("injected write failure"))
        )
        with pytest.raises(OperationFailure, match="injected write failure"):
            await purge.run_purge(db, _stateful_purge_args("--apply"))
    assert db.snapshot() == partially_cleaned
    capsys.readouterr()

    await purge.run_purge(db, _stateful_purge_args("--apply"))
    assert db.snapshot() == retained
    output = capsys.readouterr().out
    for collection in completed_collections:
        assert f"{collection}: deleted=0 planned=0" in output
    assert "users: deleted=2 planned=2" in output

    await purge.run_purge(db, _stateful_purge_args("--apply"))
    assert db.snapshot() == retained
    assert "No matching records remain" in capsys.readouterr().out
    vectors.assert_not_awaited()


def test_user_ids_normalize_sort_and_deduplicate() -> None:
    alphabetic_id = ObjectId("abcdefabcdefabcdefabcdef")
    assert purge.normalize_user_ids([str(USER_B), str(USER_A), str(USER_A)]) == (USER_A, USER_B)
    assert purge.normalize_user_ids([str(alphabetic_id), str(alphabetic_id).upper()]) == (alphabetic_id,)


@pytest.mark.parametrize("value", ["", "bad", "1" * 23, "g" * 24])
def test_invalid_user_ids_are_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="24-character"):
        purge.normalize_user_ids([str(USER_A), value])


def test_reference_forms_preserve_both_bson_and_string_ids() -> None:
    assert set(purge.id_forms([USER_A, str(USER_A)])) == {USER_A, str(USER_A)}
    assert set(purge.id_forms(["legacy-id"])) == {"legacy-id"}


@pytest.mark.parametrize("extra", [[], ["--dry-run"], ["--force"], ["--force", "--allow-unmapped"]])
def test_cli_defaults_to_no_apply(extra: list[str]) -> None:
    args = purge.parse_args(["--user-id", str(USER_A), *extra])
    assert args.apply is False
    assert args.user_ids == (USER_A,)


@pytest.mark.parametrize(
    "argv",
    [[], ["--user-id", "bad"], ["--user-id", str(USER_A), "--apply", "--dry-run"], ["--provider", "local"]],
)
def test_invalid_cli_arguments_abort(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        purge.parse_args(argv)
    assert exc.value.code == 2


async def test_every_cascade_queries_both_reference_forms_and_user_acl_type() -> None:
    db = _database()
    expected = {
        "transactions": "user",
        "conversations": "user",
        "messages": "user",
        "files": "user",
        "sharedlinks": "user",
        "keys": "userId",
        "tokens": "userId",
        "agents": "author",
        "mcpservers": "author",
        "aclentries": "principalId",
        "users": "_id",
    }

    plan = await purge.build_plan(db, (USER_A, USER_B))

    assert set(plan.deletions) == set(expected)
    for collection, field in expected.items():
        query, projection = db[collection].find.call_args_list[0].args
        assert set(query[field]["$in"]) == {USER_A, str(USER_A), USER_B, str(USER_B)}
        assert projection == {"_id": 1}
        assert set(query) == ({field, "principalType"} if collection == "aclentries" else {field})
        if collection == "aclentries":
            assert query["principalType"] == "user"
    assert "a2a_agents" not in plan.deletions
    _assert_no_deletes(db)


@pytest.mark.parametrize("stored_id", [RECORD_ID, str(RECORD_ID)])
async def test_plan_preserves_actual_document_id_types(stored_id: ObjectId | str) -> None:
    db = _database()
    db["messages"].find.side_effect = lambda *_: _cursor([{"_id": stored_id}])
    plan = await purge.build_plan(db, (USER_A,))
    assert plan.deletions["messages"] == frozenset({stored_id})


async def test_absent_user_still_collects_orphaned_references() -> None:
    db = _database()
    db["messages"].find.side_effect = lambda *_: _cursor([{"_id": RECORD_ID}])
    plan = await purge.build_plan(db, (USER_A,))
    assert plan.deletions["users"] == frozenset()
    assert plan.deletions["messages"] == frozenset({RECORD_ID})
    assert plan.total == 1


def _resource_database(acl: dict[str, Any], *, collection: str = "mcpservers") -> MagicMock:
    db = _database()
    resource_id = SERVER_ID if collection == "mcpservers" else AGENT_ID
    resource = {"_id": resource_id, "author": USER_A, "serverName": "test-server"}
    db[collection].find.side_effect = lambda *_: _cursor([resource])
    db["aclentries"].find.side_effect = lambda query, _: _cursor([acl] if "resourceId" in query else [{"_id": ACL_ID}])
    return db


@pytest.mark.parametrize("principal_id", [USER_A, str(USER_A)])
async def test_resource_and_principal_acl_matches_are_deduplicated(principal_id: ObjectId | str) -> None:
    db = _resource_database(
        {"_id": ACL_ID, "principalType": "user", "principalId": principal_id, "resourceType": "mcpServer"}
    )
    plan = await purge.build_plan(db, (USER_A,))
    assert plan.deletions["aclentries"] == frozenset({ACL_ID})
    assert not plan.blocked_acls
    query = db["aclentries"].find.call_args_list[1].args[0]
    assert set(query["resourceId"]["$in"]) == {SERVER_ID, str(SERVER_ID)}


@pytest.mark.parametrize("principal_type", ["group", "public", "role", None, "unknown"])
@pytest.mark.parametrize("principal_id", [USER_A, USER_B, None])
async def test_foreign_principal_types_block_even_if_id_matches(
    principal_type: str | None, principal_id: ObjectId | None
) -> None:
    db = _resource_database(
        {"_id": ACL_ID, "principalType": principal_type, "principalId": principal_id, "resourceType": "mcpServer"}
    )
    plan = await purge.build_plan(db, (USER_A,))
    assert plan.blocked_acls == {ACL_ID}
    with pytest.raises(purge.SafetyError, match="foreign"):
        purge.check_plan(plan, allow_unmapped=True)
    _assert_no_deletes(db)


@pytest.mark.parametrize("resource_type", ["agent", "remoteAgent", "promptGroup", None])
async def test_unexpected_mcp_resource_type_blocks_own_user_acl(resource_type: str | None) -> None:
    db = _resource_database(
        {"_id": ACL_ID, "principalType": "user", "principalId": USER_A, "resourceType": resource_type}
    )
    plan = await purge.build_plan(db, (USER_A,))
    assert plan.blocked_acls == {ACL_ID}


@pytest.mark.parametrize("resource_type", ["agent", "remoteAgent"])
async def test_chat_agents_accept_their_known_historical_resource_types(resource_type: str) -> None:
    db = _resource_database(
        {"_id": ACL_ID, "principalType": "user", "principalId": USER_A, "resourceType": resource_type},
        collection="agents",
    )
    plan = await purge.build_plan(db, (USER_A,))
    assert not plan.blocked_acls


async def test_foreign_user_on_resource_blocks() -> None:
    db = _resource_database(
        {"_id": ACL_ID, "principalType": "user", "principalId": USER_B, "resourceType": "mcpServer"}
    )
    plan = await purge.build_plan(db, (USER_A,))
    assert plan.blocked_acls == {ACL_ID}


async def test_unknown_scan_discovers_fields_then_queries_whole_collection() -> None:
    db = _database(("new_collection",))
    sample = _cursor([{"owner": "unrelated"} for _ in range(500)])
    full_query = _cursor([{"_id": RECORD_ID}])
    db["new_collection"].find.side_effect = lambda query, *_: full_query if query else sample

    plan = await purge.build_plan(db, (USER_A,))

    sample.limit.assert_called_once_with(500)
    full_query.limit.assert_not_called()
    assert db["new_collection"].find.call_args_list == [
        call({}),
        call({"owner": {"$in": purge.id_forms((USER_A,))}}, {"_id": 1}),
    ]
    assert plan.unmapped == {("new_collection", "owner"): frozenset({RECORD_ID})}
    with pytest.raises(purge.SafetyError, match="Unmapped"):
        purge.check_plan(plan, allow_unmapped=False)
    purge.check_plan(plan, allow_unmapped=True)
    assert "new_collection" not in plan.deletions


async def test_unknown_scan_uses_union_of_sample_fields_and_checks_known_collections() -> None:
    db = _database(("messages",))
    sample = _cursor([{"user": USER_A, "reviewer": USER_B}, {"sharedWith": USER_B}])

    def find_messages(query: dict[str, Any], *_: Any) -> MagicMock:
        if not query:
            return sample
        return _cursor([{"_id": RECORD_ID}] if "sharedWith" in query else [])

    db["messages"].find.side_effect = find_messages
    plan = await purge.build_plan(db, (USER_A,))

    queries = [entry.args[0] for entry in db["messages"].find.call_args_list]
    assert sum("user" in query for query in queries) == 1
    assert {"reviewer": {"$in": purge.id_forms((USER_A,))}} in queries
    assert {"sharedWith": {"$in": purge.id_forms((USER_A,))}} in queries
    assert plan.unmapped == {("messages", "sharedWith"): frozenset({RECORD_ID})}


async def test_acl_audit_is_kept_and_non_user_principal_reference_is_unmapped() -> None:
    db = _database(("aclentries",))
    audit_sample = _cursor([{"_id": ACL_ID, "grantedBy": USER_A, "principalId": USER_A}])

    def acl_find(query: dict[str, Any], *_: Any) -> MagicMock:
        if not query:
            return audit_sample
        if query.get("principalType") == {"$ne": "user"}:
            return _cursor([{"_id": ACL_ID}])
        return _cursor([])

    db["aclentries"].find.side_effect = acl_find
    plan = await purge.build_plan(db, (USER_A,))

    assert plan.deletions["aclentries"] == frozenset()
    assert plan.unmapped == {("aclentries", "principalId"): frozenset({ACL_ID})}
    queries = [entry.args[0] for entry in db["aclentries"].find.call_args_list]
    assert not any("grantedBy" in query for query in queries)
    assert {"principalId": {"$in": purge.id_forms((USER_A,))}, "principalType": {"$ne": "user"}} in queries


async def test_unknown_scan_failure_propagates_before_any_write() -> None:
    db = _database()
    db.list_collection_names.side_effect = OperationFailure("read failed")
    with pytest.raises(OperationFailure):
        await purge.build_plan(db, (USER_A,))
    _assert_no_deletes(db)


@pytest.mark.parametrize("extra", [[], ["--force"], ["--dry-run", "--force"]])
async def test_default_dry_run_never_applies_or_connects_vectors(
    extra: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _database()
    monkeypatch.setattr(purge, "build_plan", AsyncMock(return_value=_plan(with_server=True)))
    apply = AsyncMock()
    vectors = AsyncMock()
    monkeypatch.setattr(purge, "apply_plan", apply)
    monkeypatch.setattr(purge, "delete_vectors", vectors)
    args = purge.parse_args(["--user-id", str(USER_A), *extra])

    await purge.run_purge(db, args)

    apply.assert_not_awaited()
    vectors.assert_not_awaited()
    _assert_no_deletes(db)
    assert "DRY RUN" in capsys.readouterr().out


async def test_force_and_allow_unmapped_cannot_bypass_foreign_acl(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _database()
    plan = _plan(with_server=True)
    plan.blocked_acls.add(ACL_ID)
    monkeypatch.setattr(purge, "build_plan", AsyncMock(return_value=plan))
    apply = AsyncMock()
    monkeypatch.setattr(purge, "apply_plan", apply)
    args = purge.parse_args(["--user-id", str(USER_A), "--apply", "--force", "--allow-unmapped"])

    with pytest.raises(purge.SafetyError, match="foreign"):
        await purge.run_purge(db, args)

    apply.assert_not_awaited()


async def test_apply_cancellation_is_zero_write(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _database()
    monkeypatch.setattr(purge, "build_plan", AsyncMock(return_value=_plan(users=frozenset({USER_A}))))
    monkeypatch.setattr("builtins.input", lambda _: "no")
    apply = AsyncMock()
    monkeypatch.setattr(purge, "apply_plan", apply)

    with pytest.raises(purge.SafetyError, match="Cancelled"):
        await purge.run_purge(db, purge.parse_args(["--user-id", str(USER_A), "--apply"]))

    apply.assert_not_awaited()
    _assert_no_deletes(db)


@pytest.mark.parametrize("force", [False, True])
async def test_apply_requires_confirmation_unless_forced(force: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _database()
    plan = _plan(users=frozenset({USER_A}))
    monkeypatch.setattr(purge, "build_plan", AsyncMock(return_value=plan))
    prompt = MagicMock(return_value="yes")
    monkeypatch.setattr("builtins.input", prompt)
    apply = AsyncMock()
    monkeypatch.setattr(purge, "apply_plan", apply)
    extra = ["--force"] if force else []

    await purge.run_purge(db, purge.parse_args(["--user-id", str(USER_A), "--apply", "--skip-vector", *extra]))

    assert prompt.call_count == (0 if force else 1)
    apply.assert_awaited_once_with(db, plan, skip_vector=True, allow_unmapped=False)


async def test_fresh_plan_change_aborts_before_vectors_and_mongo(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _database()
    before = _plan(with_server=True)
    after = _plan(with_server=True, messages=frozenset({RECORD_ID}))
    monkeypatch.setattr(purge, "build_plan", AsyncMock(return_value=after))
    vectors = AsyncMock()
    monkeypatch.setattr(purge, "delete_vectors", vectors)

    with pytest.raises(purge.SafetyError, match="plan changed"):
        await purge.apply_plan(db, before, skip_vector=False, allow_unmapped=False)

    vectors.assert_not_awaited()
    _assert_no_deletes(db)


async def test_vector_failure_keeps_all_mongo_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _database()
    plan = _plan(with_server=True, users=frozenset({USER_A}))
    monkeypatch.setattr(purge, "build_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(purge, "delete_vectors", AsyncMock(side_effect=RuntimeError("partial vector failure")))

    with pytest.raises(RuntimeError, match="partial vector"):
        await purge.apply_plan(db, plan, skip_vector=False, allow_unmapped=False)

    _assert_no_deletes(db)


async def test_change_during_vectors_prevents_mongo_deletion(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _database()
    plan = _plan(with_server=True)
    changed = _plan(with_server=True, tokens=frozenset({RECORD_ID}))
    monkeypatch.setattr(purge, "build_plan", AsyncMock(side_effect=[plan, changed]))
    monkeypatch.setattr(purge, "delete_vectors", AsyncMock())

    with pytest.raises(purge.SafetyError, match="during vector cleanup"):
        await purge.apply_plan(db, plan, skip_vector=False, allow_unmapped=False)

    _assert_no_deletes(db)


@pytest.mark.parametrize("skip_vector,with_server", [(True, True), (False, False)])
async def test_skipped_or_absent_servers_never_call_vectors(
    skip_vector: bool, with_server: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _database()
    plan = _plan(with_server=with_server, users=frozenset({USER_A}))
    monkeypatch.setattr(purge, "build_plan", AsyncMock(return_value=plan))
    vectors = AsyncMock()
    monkeypatch.setattr(purge, "delete_vectors", vectors)

    await purge.apply_plan(db, plan, skip_vector=skip_vector, allow_unmapped=False)

    vectors.assert_not_awaited()
    db["users"].delete_many.assert_awaited_once_with({"_id": {"$in": [USER_A]}})


async def test_all_collections_delete_exact_planned_ids_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _database()
    plan = _plan(**{name: frozenset({RECORD_ID, str(RECORD_ID)}) for name in purge.DELETE_ORDER})
    monkeypatch.setattr(purge, "build_plan", AsyncMock(return_value=plan))
    history = MagicMock()
    for name in purge.DELETE_ORDER:
        history.attach_mock(db[name].delete_many, name)

    await purge.apply_plan(db, plan, skip_vector=True, allow_unmapped=False)

    assert [entry[0] for entry in history.mock_calls] == list(purge.DELETE_ORDER)
    assert history.mock_calls[-1][0] == "users"
    for name in purge.DELETE_ORDER:
        args = db[name].delete_many.await_args.args[0]
        assert set(args["_id"]["$in"]) == {RECORD_ID, str(RECORD_ID)}
        assert set(args) == {"_id"}


async def test_partial_mongo_failure_stops_before_resources_and_users(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _database()
    plan = _plan(transactions=frozenset({RECORD_ID}), messages=frozenset({RECORD_ID}), users=frozenset({USER_A}))
    monkeypatch.setattr(purge, "build_plan", AsyncMock(return_value=plan))
    db["messages"].delete_many.side_effect = OperationFailure("write failed")

    with pytest.raises(OperationFailure):
        await purge.apply_plan(db, plan, skip_vector=True, allow_unmapped=False)

    db["transactions"].delete_many.assert_awaited_once()
    db["users"].delete_many.assert_not_awaited()
    assert "transactions: deleted=1 planned=1" in capsys.readouterr().out


async def test_retry_uses_remaining_plan_after_partial_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _database()
    original = _plan(transactions=frozenset({RECORD_ID}), messages=frozenset({RECORD_ID}), users=frozenset({USER_A}))
    remaining = _plan(messages=frozenset({RECORD_ID}), users=frozenset({USER_A}))
    monkeypatch.setattr(purge, "build_plan", AsyncMock(side_effect=[original, remaining]))
    db["messages"].delete_many.side_effect = [OperationFailure("interrupted"), MagicMock(deleted_count=1)]

    with pytest.raises(OperationFailure):
        await purge.apply_plan(db, original, skip_vector=True, allow_unmapped=False)
    db["users"].delete_many.assert_not_awaited()

    await purge.apply_plan(db, remaining, skip_vector=True, allow_unmapped=False)

    db["transactions"].delete_many.assert_awaited_once()
    assert db["messages"].delete_many.await_count == 2
    db["users"].delete_many.assert_awaited_once_with({"_id": {"$in": [USER_A]}})


async def test_multiple_batches_report_committed_progress_before_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _database()
    plan = _plan(messages=frozenset(str(i) for i in range(501)), users=frozenset({USER_A}))
    monkeypatch.setattr(purge, "build_plan", AsyncMock(return_value=plan))
    db["messages"].delete_many.side_effect = [MagicMock(deleted_count=500), OperationFailure("second batch")]

    with pytest.raises(OperationFailure):
        await purge.apply_plan(db, plan, skip_vector=True, allow_unmapped=False)

    assert len(db["messages"].delete_many.await_args_list[0].args[0]["_id"]["$in"]) == 500
    assert len(db["messages"].delete_many.await_args_list[1].args[0]["_id"]["$in"]) == 1
    db["users"].delete_many.assert_not_awaited()
    assert "messages: deleted=500 planned=501" in capsys.readouterr().out


@pytest.mark.parametrize(
    "selection", [None, {}, {"embeddingCollectionGeneration": None}, {"embeddingCollectionGeneration": GENERATION}]
)
async def test_vector_generation_uses_selected_generation_or_legacy(selection: dict[str, Any] | None) -> None:
    db = _database()
    db["model_gateway_selection"].find_one.return_value = selection
    before = datetime.now(UTC)

    result = await purge.vector_generation(db)

    assert result == (selection or {}).get("embeddingCollectionGeneration")
    query = db["embedding_reindex_jobs"].find_one.await_args.args[0]
    assert query["status"] == "running"
    assert before <= query["leaseExpiresAt"]["$gt"] <= datetime.now(UTC)
    assert query["leaseExpiresAt"]["$gt"].tzinfo is UTC


async def test_active_reindex_blocks_before_loading_generation() -> None:
    db = _database()
    db["embedding_reindex_jobs"].find_one.return_value = {"_id": RECORD_ID}

    with pytest.raises(purge.SafetyError, match="reindex is active"):
        await purge.vector_generation(db)

    db["model_gateway_selection"].find_one.assert_not_awaited()


@pytest.mark.parametrize("generation", ["", "not-an-id", 1, {}, ObjectId(GENERATION)])
async def test_invalid_vector_generation_aborts(generation: Any) -> None:
    db = _database()
    db["model_gateway_selection"].find_one.return_value = {"embeddingCollectionGeneration": generation}
    with pytest.raises(purge.SafetyError, match="generation is invalid"):
        await purge.vector_generation(db)


def _vector_repository(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    from scripts import _purge_user_vectors

    repository = MagicMock()
    repository.delete_by_server_id = AsyncMock(return_value=2)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=repository)
    context.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=context)
    monkeypatch.setattr(_purge_user_vectors, "open_vector_repository", factory)
    return repository, factory


async def test_vectors_delete_once_per_server_using_selected_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _database()
    plan = _plan(with_server=True)
    plan.resources["mcpservers"][str(RECORD_ID)] = {"_id": str(RECORD_ID), "author": USER_A}
    generation = AsyncMock(return_value=GENERATION)
    monkeypatch.setattr(purge, "vector_generation", generation)
    repository, factory = _vector_repository(monkeypatch)

    await purge.delete_vectors(db, plan)

    factory.assert_called_once_with(GENERATION)
    assert repository.delete_by_server_id.await_args_list == [
        call(str(SERVER_ID), "test-server"),
        call(str(RECORD_ID), None),
    ]
    assert generation.await_count == 4
    factory.return_value.__aexit__.assert_awaited_once()


@pytest.mark.parametrize(
    "generations,delete_count", [([GENERATION, str(USER_B)], 0), ([GENERATION, GENERATION, str(USER_B)], 1)]
)
async def test_generation_change_aborts_and_closes_repository(
    generations: list[str], delete_count: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _database()
    monkeypatch.setattr(purge, "vector_generation", AsyncMock(side_effect=generations))
    repository, factory = _vector_repository(monkeypatch)

    with pytest.raises(purge.SafetyError, match="generation changed"):
        await purge.delete_vectors(db, _plan(with_server=True))

    assert repository.delete_by_server_id.await_count == delete_count
    factory.return_value.__aexit__.assert_awaited_once()
    _assert_no_deletes(db)


async def test_safe_vector_error_preserves_operator_detail_and_blocks_mongo(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts._purge_user_vectors import VectorCleanupError

    db = _database()
    plan = _plan(with_server=True, users=frozenset({USER_A}))
    monkeypatch.setattr(purge, "build_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(purge, "vector_generation", AsyncMock(return_value=None))
    repository, _ = _vector_repository(monkeypatch)
    repository.delete_by_server_id.side_effect = VectorCleanupError(
        "Vector deletion partially failed: 3 deleted, 1 failed"
    )

    with pytest.raises(purge.SafetyError, match="3 deleted, 1 failed"):
        await purge.apply_plan(db, plan, skip_vector=False, allow_unmapped=False)

    _assert_no_deletes(db)


async def test_raw_client_is_closed_after_connection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.admin.command = AsyncMock(side_effect=OperationFailure("failed"))
    client.close = AsyncMock()
    monkeypatch.setattr(purge, "MongoSettings", MagicMock())
    monkeypatch.setattr(purge, "create_mongo_client", MagicMock(return_value=(client, "isolated_test")))
    run = AsyncMock()
    monkeypatch.setattr(purge, "run_purge", run)

    with pytest.raises(OperationFailure):
        await purge.async_main(argparse.Namespace())

    client.close.assert_awaited_once()
    run.assert_not_awaited()


def test_main_redacts_database_error_payload(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(purge, "async_main", AsyncMock(side_effect=OperationFailure("secret-db-uri-and-document")))
    assert purge.main(["--user-id", str(USER_A)]) == 1
    output = capsys.readouterr().out
    assert "OperationFailure" in output
    assert "secret-db-uri-and-document" not in output
