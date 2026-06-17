import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from registry.schemas.workflow_api_schemas import (
    RouterChoiceInput,
    WorkflowCreateRequest,
    WorkflowNodeInput,
)
from registry.services import workflow_service
from registry.services.workflow_service import WorkflowService
from registry_pkgs.database.mongodb import MongoDB


class _WorkflowFindQuery:
    def __init__(self, *, total: int = 0, workflows: list | None = None):
        self._total = total
        self._workflows = workflows or []

    async def count(self):
        return self._total

    def sort(self, *_args, **_kwargs):
        return self

    def skip(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    async def to_list(self):
        return self._workflows


class _ListQuery:
    def __init__(self, items: list):
        self._items = items

    async def to_list(self):
        return self._items


def _patch_executor_ref_queries(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mcp_names: set[str] | None = None,
    a2a_paths: set[str] | None = None,
) -> list[tuple[str, dict]]:
    captured_queries: list[tuple[str, dict]] = []
    mcp_names = mcp_names or set()
    a2a_paths = a2a_paths or set()

    def fake_mcp_find(query: dict, **_kwargs):
        captured_queries.append(("mcp", query))
        requested = set(query["serverName"]["$in"])
        return _ListQuery([SimpleNamespace(serverName=name) for name in sorted(mcp_names & requested)])

    def fake_a2a_find(query: dict, **_kwargs):
        captured_queries.append(("a2a", query))
        requested = set(query["path"]["$in"])
        return _ListQuery([SimpleNamespace(path=path) for path in sorted(a2a_paths & requested)])

    monkeypatch.setattr(workflow_service.ExtendedMCPServer, "find", fake_mcp_find)
    monkeypatch.setattr(workflow_service.A2AAgent, "find", fake_a2a_find)
    return captured_queries


@pytest.mark.asyncio
async def test_list_workflows_escapes_regex_query(monkeypatch: pytest.MonkeyPatch):
    captured_filters = []

    def fake_find(filters):
        captured_filters.append(filters)
        return _WorkflowFindQuery()

    monkeypatch.setattr(workflow_service.WorkflowDefinition, "find", fake_find)

    await WorkflowService().list_workflows(query="a.b[")

    search_pattern = captured_filters[0]["$or"][0]["name"]
    assert search_pattern == {"$regex": re.escape("a.b["), "$options": "i"}


@pytest.mark.asyncio
async def test_create_workflow_does_not_convert_unexpected_errors_to_value_error(monkeypatch: pytest.MonkeyPatch):
    class FailingWorkflowDefinition:
        def __init__(self, **_kwargs):
            pass

        async def insert(self):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(workflow_service, "WorkflowDefinition", FailingWorkflowDefinition)
    monkeypatch.setattr(WorkflowService, "_validate_executor_refs", AsyncMock(return_value=None))

    request = WorkflowCreateRequest(
        name="Demo workflow",
        canvas={"viewport": {"x": 0, "y": 0, "zoom": 1}},
        nodes=[WorkflowNodeInput(name="Fetch", nodeType="step", executorKey="tool")],
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await WorkflowService().create_workflow(request)


@pytest.mark.asyncio
async def test_validate_executor_refs_passes_for_valid_mcp_server_and_a2a_agent(monkeypatch: pytest.MonkeyPatch):
    service = WorkflowService()
    captured_queries = _patch_executor_ref_queries(
        monkeypatch,
        mcp_names={"github"},
        a2a_paths={"deep-intel", "researcher"},
    )
    nodes = [
        service._convert_api_node_to_model(WorkflowNodeInput(name="mcp", nodeType="step", executorKey="github")),
        service._convert_api_node_to_model(WorkflowNodeInput(name="a2a", nodeType="step", executorKey="deep-intel")),
        service._convert_api_node_to_model(WorkflowNodeInput(name="pool", nodeType="step", a2aPool=["researcher"])),
    ]

    await service._validate_executor_refs(nodes)

    assert captured_queries[0] == (
        "mcp",
        {"serverName": {"$in": ["deep-intel", "github"]}, "config.enabled": True},
    )
    assert captured_queries[1] == ("a2a", {"path": {"$in": ["deep-intel"]}, "config.enabled": True})
    assert captured_queries[2] == ("a2a", {"path": {"$in": ["researcher"]}, "config.enabled": True})


@pytest.mark.asyncio
async def test_validate_executor_refs_returns_400_for_unknown_executor_key(monkeypatch: pytest.MonkeyPatch):
    service = WorkflowService()
    _patch_executor_ref_queries(monkeypatch, mcp_names=set(), a2a_paths=set())
    nodes = [service._convert_api_node_to_model(WorkflowNodeInput(name="bad", nodeType="step", executorKey="typo"))]

    with pytest.raises(HTTPException) as exc_info:
        await service._validate_executor_refs(nodes)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Unknown executor key: 'typo'"


@pytest.mark.asyncio
async def test_validate_executor_refs_returns_400_for_unknown_a2a_pool_path(monkeypatch: pytest.MonkeyPatch):
    service = WorkflowService()
    _patch_executor_ref_queries(monkeypatch, a2a_paths=set())
    nodes = [service._convert_api_node_to_model(WorkflowNodeInput(name="pool", nodeType="step", a2aPool=["missing"]))]

    with pytest.raises(HTTPException) as exc_info:
        await service._validate_executor_refs(nodes)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Unknown a2aPool agent path: 'missing'"


@pytest.mark.asyncio
async def test_validate_executor_refs_finds_bad_key_in_nested_step(monkeypatch: pytest.MonkeyPatch):
    service = WorkflowService()
    _patch_executor_ref_queries(monkeypatch, mcp_names=set(), a2a_paths=set())
    parallel = service._convert_api_node_to_model(
        WorkflowNodeInput(
            name="par",
            nodeType="parallel",
            children=[
                WorkflowNodeInput(name="ok", nodeType="step", executorKey="echo"),
                WorkflowNodeInput(name="bad", nodeType="step", executorKey="missing"),
            ],
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        await service._validate_executor_refs([parallel])

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Unknown executor key: 'missing'"


def test_convert_step_node_preserves_executor_key():
    api_node = WorkflowNodeInput(name="fetch", nodeType="step", executorKey="tool-fetch")
    model = WorkflowService()._convert_api_node_to_model(api_node)
    assert model.name == "fetch"
    assert model.node_type == "step"
    assert model.executor_key == "tool-fetch"
    assert model.children == []
    assert model.true_steps == []
    assert model.false_steps == []
    assert model.choices == []


def test_convert_condition_node_maps_true_and_false_steps_recursively():
    """AS-1606 motivating shape: CONDITION with multi-step true & false branches."""
    api_node = WorkflowNodeInput(
        name="B",
        nodeType="condition",
        conditionCel="input.routeToTrue == true",
        trueSteps=[
            WorkflowNodeInput(name="C", nodeType="step", executorKey="tool-c"),
            WorkflowNodeInput(name="E", nodeType="step", executorKey="tool-e"),
            WorkflowNodeInput(name="G", nodeType="step", executorKey="tool-g"),
        ],
        falseSteps=[
            WorkflowNodeInput(name="D", nodeType="step", executorKey="tool-d"),
            WorkflowNodeInput(name="F", nodeType="step", executorKey="tool-f"),
            WorkflowNodeInput(name="H", nodeType="step", executorKey="tool-h"),
        ],
    )

    model = WorkflowService()._convert_api_node_to_model(api_node)

    assert model.node_type == "condition"
    assert model.condition_cel == "input.routeToTrue == true"
    assert [n.name for n in model.true_steps] == ["C", "E", "G"]
    assert [n.executor_key for n in model.true_steps] == ["tool-c", "tool-e", "tool-g"]
    assert [n.name for n in model.false_steps] == ["D", "F", "H"]
    assert model.children == []
    assert model.choices == []


def test_convert_router_node_maps_choices_with_multi_step_pipelines():
    api_node = WorkflowNodeInput(
        name="research-router",
        nodeType="router",
        conditionCel="input.strategy",
        choices=[
            RouterChoiceInput(
                name="tech",
                steps=[
                    WorkflowNodeInput(name="hn", nodeType="step", executorKey="hn-tool"),
                    WorkflowNodeInput(name="deep", nodeType="step", executorKey="deep-tool"),
                ],
            ),
            RouterChoiceInput(
                name="general",
                steps=[WorkflowNodeInput(name="web", nodeType="step", executorKey="web-tool")],
            ),
        ],
    )

    model = WorkflowService()._convert_api_node_to_model(api_node)

    assert model.node_type == "router"
    assert model.condition_cel == "input.strategy"
    assert model.children == []
    assert model.true_steps == []
    assert model.false_steps == []
    assert [c.name for c in model.choices] == ["tech", "general"]
    assert [s.name for s in model.choices[0].steps] == ["hn", "deep"]
    assert [s.executor_key for s in model.choices[0].steps] == ["hn-tool", "deep-tool"]
    assert [s.name for s in model.choices[1].steps] == ["web"]


def test_convert_condition_node_with_nested_router_recursively_converts():
    """Recursion must reach into router.choices[*].steps inside a condition branch."""
    api_node = WorkflowNodeInput(
        name="outer",
        nodeType="condition",
        conditionCel="x",
        trueSteps=[
            WorkflowNodeInput(
                name="inner-router",
                nodeType="router",
                conditionCel="step_choices[0]",
                choices=[
                    RouterChoiceInput(
                        name="a",
                        steps=[
                            WorkflowNodeInput(name="a1", nodeType="step", executorKey="tool-a1"),
                            WorkflowNodeInput(name="a2", nodeType="step", executorKey="tool-a2"),
                        ],
                    ),
                    RouterChoiceInput(
                        name="b",
                        steps=[WorkflowNodeInput(name="b1", nodeType="step", executorKey="tool-b1")],
                    ),
                ],
            ),
        ],
    )

    model = WorkflowService()._convert_api_node_to_model(api_node)

    assert model.node_type == "condition"
    assert len(model.true_steps) == 1
    inner = model.true_steps[0]
    assert inner.node_type == "router"
    assert [c.name for c in inner.choices] == ["a", "b"]
    assert [s.name for s in inner.choices[0].steps] == ["a1", "a2"]
    assert [s.name for s in inner.choices[1].steps] == ["b1"]


def test_convert_api_node_generates_id_when_missing():
    api_node = WorkflowNodeInput(name="x", nodeType="step", executorKey="tool")
    model = WorkflowService()._convert_api_node_to_model(api_node)
    assert isinstance(model.id, str) and len(model.id) > 0


def test_convert_api_node_preserves_explicit_id():
    api_node = WorkflowNodeInput(id="custom-id", name="x", nodeType="step", executorKey="tool")
    model = WorkflowService()._convert_api_node_to_model(api_node)
    assert model.id == "custom-id"


# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------
class _FakeWorkflow:
    """Stand-in for a loaded WorkflowDefinition supporting version + save."""

    def __init__(self, version: int = 1):
        from datetime import UTC, datetime

        from beanie import PydanticObjectId

        self.id = PydanticObjectId()
        self.name = "old-name"
        self.description = None
        self.version = version
        self.updated_at = datetime.now(UTC)
        self.saved = False
        self.enabled = True

    def model_dump(self, mode: str | None = None):
        return {"name": self.name, "version": self.version}

    def model_dump_json(self):
        return f'{{"name": "{self.name}", "version": {self.version}}}'

    async def save(self):
        self.saved = True


# ── Transaction + collection mocks for update_workflow ─────────────────────────


class _FakeTxnCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeTxnSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def start_transaction(self):
        return _FakeTxnCtx()


class _FakeTxnClient:
    def start_session(self):
        return _FakeTxnSession()


def _patch_update_transaction(monkeypatch, *, found_doc, refreshed=None, insert_exc=None):
    """Patch the transaction + collection machinery used by update_workflow.

    Args:
        found_doc:   What ``find_one_and_update`` returns (None simulates a lost
                     optimistic-lock race → 409).
        refreshed:   Object returned by the final ``WorkflowDefinition.get`` re-fetch.
        insert_exc:  Exception raised by ``WorkflowVersion.insert`` (e.g. DuplicateKeyError).

    Returns ``(collection, inserted)`` where ``collection.find_one_and_update`` is an
    AsyncMock and ``inserted`` collects archived WorkflowVersion kwargs.
    """
    from registry.services.workflow_service import WorkflowDefinition

    monkeypatch.setattr(MongoDB, "get_client", lambda: _FakeTxnClient())
    collection = SimpleNamespace(find_one_and_update=AsyncMock(return_value=found_doc))
    fake_db = SimpleNamespace(get_collection=lambda _name: collection)
    monkeypatch.setattr(MongoDB, "get_database", lambda: fake_db)
    monkeypatch.setattr(WorkflowDefinition, "get_settings", lambda: SimpleNamespace(name="workflow_definitions"))
    monkeypatch.setattr(WorkflowDefinition, "get", AsyncMock(return_value=refreshed))

    inserted: list[dict] = []

    class _FakeWorkflowVersion:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def insert(self, session=None):
            if insert_exc is not None:
                raise insert_exc
            inserted.append(self.kwargs)

    monkeypatch.setattr(workflow_service, "WorkflowVersion", _FakeWorkflowVersion)
    return collection, inserted


@pytest.mark.asyncio
async def test_update_workflow_bumps_version_and_snapshots_history(monkeypatch: pytest.MonkeyPatch):
    from registry.schemas.workflow_api_schemas import WorkflowUpdateRequest

    fake_wf = _FakeWorkflow(version=4)

    async def fake_get(self, workflow_id, session=None):
        return fake_wf

    monkeypatch.setattr(WorkflowService, "get_workflow_by_id", fake_get)

    refreshed = SimpleNamespace(id=fake_wf.id, version=5, name="new-name")
    collection, inserted = _patch_update_transaction(
        monkeypatch, found_doc={"_id": fake_wf.id, "version": 5}, refreshed=refreshed
    )

    result = await WorkflowService().update_workflow(
        workflow_id=str(fake_wf.id),
        data=WorkflowUpdateRequest(name="new-name"),
    )

    # Returned the re-fetched, version-bumped document.
    assert result.version == 5
    assert result.name == "new-name"

    # The bump used an optimistic conditional filter (version == previous_version).
    call = collection.find_one_and_update.await_args
    assert call.args[0] == {"_id": fake_wf.id, "version": 4}
    assert call.args[1]["$set"]["version"] == 5

    # Prior version (4) archived into history with a checksum.
    assert len(inserted) == 1
    assert inserted[0]["version"] == 4
    assert inserted[0]["workflow_id"] == fake_wf.id
    assert isinstance(inserted[0]["checksum"], str) and len(inserted[0]["checksum"]) == 64


@pytest.mark.asyncio
async def test_update_workflow_returns_409_on_concurrent_modification(monkeypatch: pytest.MonkeyPatch):
    """When a concurrent writer bumped the version first, the conditional update
    matches nothing → 409 (lost-update prevention), and no history row is written."""
    from registry.schemas.workflow_api_schemas import WorkflowUpdateRequest

    fake_wf = _FakeWorkflow(version=4)

    async def fake_get(self, workflow_id, session=None):
        return fake_wf

    monkeypatch.setattr(WorkflowService, "get_workflow_by_id", fake_get)
    _collection, inserted = _patch_update_transaction(monkeypatch, found_doc=None)

    with pytest.raises(HTTPException) as exc_info:
        await WorkflowService().update_workflow(
            workflow_id=str(fake_wf.id), data=WorkflowUpdateRequest(name="new-name")
        )

    assert exc_info.value.status_code == 409
    assert inserted == []  # no version archived when the bump lost the race


@pytest.mark.asyncio
async def test_update_workflow_returns_409_on_duplicate_version(monkeypatch: pytest.MonkeyPatch):
    """A racing archive of the same (workflow_id, version) violates the unique index;
    the DuplicateKeyError is mapped to 409 rather than bubbling up as a 500."""
    from registry.schemas.workflow_api_schemas import WorkflowUpdateRequest

    fake_wf = _FakeWorkflow(version=4)

    async def fake_get(self, workflow_id, session=None):
        return fake_wf

    monkeypatch.setattr(WorkflowService, "get_workflow_by_id", fake_get)
    _patch_update_transaction(
        monkeypatch,
        found_doc={"_id": fake_wf.id, "version": 5},
        insert_exc=DuplicateKeyError("E11000 duplicate key"),
    )

    with pytest.raises(HTTPException) as exc_info:
        await WorkflowService().update_workflow(
            workflow_id=str(fake_wf.id), data=WorkflowUpdateRequest(name="new-name")
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_update_workflow_invalid_nodes_writes_nothing(monkeypatch: pytest.MonkeyPatch):
    """A model-validation failure mid-update must not bump the version or leave an
    orphan version-history row (validation runs before any DB write)."""
    from registry.schemas.workflow_api_schemas import WorkflowNodeInput, WorkflowUpdateRequest

    fake_wf = _FakeWorkflow(version=4)

    async def fake_get(self, workflow_id, session=None):
        return fake_wf

    monkeypatch.setattr(WorkflowService, "get_workflow_by_id", fake_get)
    collection, inserted = _patch_update_transaction(monkeypatch, found_doc={"_id": fake_wf.id, "version": 5})

    # A parallel node with no children fails WorkflowNode shape validation during conversion.
    bad_update = WorkflowUpdateRequest(nodes=[WorkflowNodeInput(name="p", nodeType="parallel")])

    with pytest.raises(ValueError, match="parallel node requires at least 2 children"):
        await WorkflowService().update_workflow(workflow_id=str(fake_wf.id), data=bad_update)

    # Validation raised before the conditional update or any history write.
    collection.find_one_and_update.assert_not_awaited()
    assert inserted == []


@pytest.mark.asyncio
async def test_list_versions_includes_history_and_current(monkeypatch: pytest.MonkeyPatch):
    fake_wf = _FakeWorkflow(version=3)

    async def fake_get(self, workflow_id, session=None):
        return fake_wf

    monkeypatch.setattr(WorkflowService, "get_workflow_by_id", fake_get)

    from datetime import UTC, datetime
    from types import SimpleNamespace

    history = [
        SimpleNamespace(version=1, created_at=datetime.now(UTC), checksum="c1"),
        SimpleNamespace(version=2, created_at=datetime.now(UTC), checksum="c2"),
    ]

    class _Query:
        def sort(self, *_a, **_k):
            return self

        async def to_list(self):
            return history

    monkeypatch.setattr(workflow_service.WorkflowVersion, "find", lambda *_a, **_k: _Query())

    versions = await WorkflowService().list_versions(str(fake_wf.id))

    assert [v["version"] for v in versions] == [1, 2, 3]  # history + current
    assert versions[-1]["version"] == 3  # current appended last


@pytest.mark.asyncio
async def test_trigger_run_resolves_requested_historical_version(monkeypatch: pytest.MonkeyPatch):
    from types import SimpleNamespace

    fake_wf = _FakeWorkflow(version=3)

    async def fake_get(self, workflow_id, session=None):
        return fake_wf

    monkeypatch.setattr(WorkflowService, "get_workflow_by_id", fake_get)

    historical = SimpleNamespace(version=2, definition={"snapshot": "v2"})

    async def fake_find_one(*_a, **_k):
        return historical

    monkeypatch.setattr(workflow_service.WorkflowVersion, "find_one", fake_find_one)

    captured: dict = {}

    class _FakeRun:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            from beanie import PydanticObjectId

            self.id = PydanticObjectId()
            self.status = kwargs.get("status")
            self.trigger_source = kwargs.get("trigger_source")
            self.workflow_definition_id = kwargs.get("workflow_definition_id")

        async def insert(self):
            return None

    monkeypatch.setattr(workflow_service, "WorkflowRun", _FakeRun)

    await WorkflowService().trigger_workflow_run(workflow_id=str(fake_wf.id), version=2)

    assert captured["workflow_version"] == 2
    assert captured["definition_snapshot"] == {"snapshot": "v2"}


@pytest.mark.asyncio
async def test_delete_workflow_cascades_agno_sessions(monkeypatch: pytest.MonkeyPatch):
    """delete_workflow must purge agno_workflow_sessions keyed by run id (GC + PII cleanup)."""
    workflow_oid = PydanticObjectId()
    run_ids = [PydanticObjectId(), PydanticObjectId()]
    deleted_session_query = {}
    workflow_deleted = []

    async def _wf_delete():
        workflow_deleted.append(True)

    workflow = SimpleNamespace(id=workflow_oid, name="wf", delete=_wf_delete)
    monkeypatch.setattr(WorkflowService, "get_workflow_by_id", AsyncMock(return_value=workflow))

    runs = [SimpleNamespace(id=rid) for rid in run_ids]

    class _RunQuery:
        async def to_list(self):
            return runs

        async def delete(self):
            return None

    class _DeleteQuery:
        async def delete(self):
            return None

    # Replace the whole Beanie classes: ``delete_workflow`` evaluates
    # ``WorkflowRun.workflow_definition_id == ...`` at class level, which is a
    # field descriptor only available once Beanie is initialized.
    class _FakeWorkflowRun:
        workflow_definition_id = "workflow_definition_id"

        @staticmethod
        def find(*_a, **_k):
            return _RunQuery()

    class _FakeNodeRun:
        @staticmethod
        def find(*_a, **_k):
            return _DeleteQuery()

    class _FakeWorkflowVersion:
        @staticmethod
        def find(*_a, **_k):
            return _DeleteQuery()

    monkeypatch.setattr(workflow_service, "WorkflowRun", _FakeWorkflowRun)
    monkeypatch.setattr(workflow_service, "NodeRun", _FakeNodeRun)
    monkeypatch.setattr(workflow_service, "WorkflowVersion", _FakeWorkflowVersion)

    class _Collection:
        async def delete_many(self, query):
            deleted_session_query.update(query)
            return SimpleNamespace(deleted_count=len(run_ids))

    class _Db:
        def get_collection(self, name):
            assert name == "agno_workflow_sessions"
            return _Collection()

    monkeypatch.setattr(workflow_service.MongoDB, "get_database", staticmethod(lambda: _Db()))

    result = await WorkflowService().delete_workflow(str(workflow_oid))

    assert result is True
    assert workflow_deleted == [True]
    assert deleted_session_query == {"session_id": {"$in": [str(rid) for rid in run_ids]}}


@pytest.mark.asyncio
async def test_trigger_workflow_run_persists_triggering_identity(monkeypatch: pytest.MonkeyPatch):
    """The live trigger path must persist the triggering user's non-sensitive
    identity (user_id / username / scopes) so an HITL resume can re-mint a
    service JWT on their behalf."""
    fake_wf = _FakeWorkflow(version=3)

    async def fake_get(self, workflow_id, session=None):
        return fake_wf

    monkeypatch.setattr(WorkflowService, "get_workflow_by_id", fake_get)

    historical = SimpleNamespace(version=2, definition={"snapshot": "v2"})

    async def fake_find_one(*_a, **_k):
        return historical

    monkeypatch.setattr(workflow_service.WorkflowVersion, "find_one", fake_find_one)

    captured: dict = {}

    class _FakeRun:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.id = PydanticObjectId()

        async def insert(self):
            return None

    monkeypatch.setattr(workflow_service, "WorkflowRun", _FakeRun)

    await WorkflowService().trigger_workflow_run(
        workflow_id=str(fake_wf.id),
        version=2,
        triggering_user_id="user-42",
        triggering_username="alice",
        triggering_scopes=["workflows-read", "workflows-control"],
    )

    assert captured["triggering_user_id"] == "user-42"
    assert captured["triggering_username"] == "alice"
    assert captured["triggering_scopes"] == ["workflows-read", "workflows-control"]


class _ChildRunQuery:
    """Chainable stand-in for ``WorkflowRun.find(...)`` in list_child_runs."""

    def __init__(self, runs: list, captured_filters: list):
        self._runs = runs
        self._captured = captured_filters

    async def count(self):
        return len(self._runs)

    def sort(self, *_a, **_k):
        return self

    def skip(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    async def to_list(self):
        return self._runs


@pytest.mark.asyncio
async def test_list_child_runs_filters_by_parent_run_id(monkeypatch: pytest.MonkeyPatch):
    """list_child_runs must scope the query to the parent's workflow AND parent_run_id."""
    workflow_oid = PydanticObjectId()
    parent_oid = PydanticObjectId()
    child = SimpleNamespace(id=PydanticObjectId(), workflow_definition_id=workflow_oid)

    workflow = SimpleNamespace(id=workflow_oid, name="wf")
    monkeypatch.setattr(WorkflowService, "get_workflow_by_id", AsyncMock(return_value=workflow))

    captured_filters: list = []

    class _FakeWorkflowRun:
        id = "id"
        workflow_definition_id = "workflow_definition_id"

        @staticmethod
        async def find_one(*_a, **_k):
            return SimpleNamespace(id=parent_oid, workflow_definition_id=workflow_oid)

        @staticmethod
        def find(filters, *_a, **_k):
            captured_filters.append(filters)
            return _ChildRunQuery([child], captured_filters)

    class _FakeNodeRun:
        @staticmethod
        def find(*_a, **_k):
            return _ChildRunQuery([], captured_filters)

    monkeypatch.setattr(workflow_service, "WorkflowRun", _FakeWorkflowRun)
    monkeypatch.setattr(workflow_service, "NodeRun", _FakeNodeRun)

    runs_with_nodes, total = await WorkflowService().list_child_runs(
        workflow_id=str(workflow_oid),
        parent_run_id=str(parent_oid),
    )

    assert total == 1
    assert runs_with_nodes[0][0] is child
    # Both the count and the page query must carry the parent_run_id filter.
    assert all(f["parent_run_id"] == parent_oid for f in captured_filters)
    assert all(f["workflow_definition_id"] == workflow_oid for f in captured_filters)


@pytest.mark.asyncio
async def test_list_child_runs_raises_when_parent_missing(monkeypatch: pytest.MonkeyPatch):
    """A non-existent parent run must raise ValueError (mapped to 404 by the route)."""
    workflow_oid = PydanticObjectId()
    parent_oid = PydanticObjectId()

    workflow = SimpleNamespace(id=workflow_oid, name="wf")
    monkeypatch.setattr(WorkflowService, "get_workflow_by_id", AsyncMock(return_value=workflow))

    class _FakeWorkflowRun:
        id = "id"
        workflow_definition_id = "workflow_definition_id"

        @staticmethod
        async def find_one(*_a, **_k):
            return None

    monkeypatch.setattr(workflow_service, "WorkflowRun", _FakeWorkflowRun)

    with pytest.raises(ValueError, match="not found"):
        await WorkflowService().list_child_runs(
            workflow_id=str(workflow_oid),
            parent_run_id=str(parent_oid),
        )
