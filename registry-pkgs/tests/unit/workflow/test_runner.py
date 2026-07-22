from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agno.run.base import RunStatus
from agno.run.workflow import WorkflowRunOutput
from beanie import PydanticObjectId

from registry_pkgs.models.enums import WorkflowRunStatus
from registry_pkgs.models.workflow import WorkflowDefinition, WorkflowNode, WorkflowRun
from registry_pkgs.workflows import runner
from registry_pkgs.workflows.control.wrapper import WorkflowCancelledError
from registry_pkgs.workflows.types import McpConsentRequiredError


class _FieldExpr:
    def __init__(self, name: str):
        self.name = name

    def __eq__(self, other):
        return (self.name, "==", other)


def _definition() -> WorkflowDefinition:
    return WorkflowDefinition.model_construct(
        id=PydanticObjectId(),
        name="demo-workflow",
        nodes=[WorkflowNode(name="fetch", executor_key="tool", step_objective="fetch data")],
    )


def _run() -> WorkflowRun:
    return WorkflowRun.model_construct(
        id=PydanticObjectId(),
        workflow_definition_id=PydanticObjectId(),
        status=WorkflowRunStatus.RUNNING,
        finished_at=None,
    )


def _make_runner(**kwargs) -> runner.WorkflowRunner:
    """Return a WorkflowRunner with sensible test defaults."""
    from registry_pkgs.core.config import JwtSigningConfig

    defaults = {
        "llm": object(),
        "db_client": object(),
        "db_name": "jarvis",
        "jwt_config": JwtSigningConfig(
            jwt_private_key="fake-pem",
            jwt_issuer="https://jarvis.example.com",
            jwt_self_signed_kid="kid-v1",
            jwt_audience="jarvis-services",
            registry_app_name="jarvis-registry-client",
        ),
    }
    defaults.update(kwargs)
    return runner.WorkflowRunner(**defaults)


@pytest.mark.unit
class TestWorkflowRunnerInit:
    def test_requires_db_client(self):
        with pytest.raises(ValueError, match="db_client"):
            _make_runner(db_client=None)

    def test_requires_db_name(self):
        with pytest.raises(ValueError, match="db_name"):
            _make_runner(db_name="")

    def test_selector_llm_defaults_to_none(self):
        r = _make_runner()
        # selector_llm=None is valid; build_executor_registry falls back to llm.
        assert r._selector_llm is None


@pytest.mark.unit
class TestWorkflowRunnerRun:
    @pytest.mark.asyncio
    async def test_raises_when_definition_not_found(self, monkeypatch: pytest.MonkeyPatch):
        run_doc = SimpleNamespace(id=PydanticObjectId(), definition_snapshot=None)
        monkeypatch.setattr(runner.WorkflowRun, "get", AsyncMock(return_value=run_doc))
        monkeypatch.setattr(runner.WorkflowDefinition, "get", AsyncMock(return_value=None))
        r = _make_runner()

        with pytest.raises(ValueError, match="not found"):
            await r.run(str(PydanticObjectId()), "hello", auth_context=None, user_id=None, existing_run_id="any-id")

    @pytest.mark.asyncio
    async def test_orchestrates_build_registry_execute_and_returns_node_runs(self, monkeypatch: pytest.MonkeyPatch):
        """run() must load existing run, call _build_registry → _execute in order."""
        definition = _definition()
        run_doc = SimpleNamespace(
            id=PydanticObjectId(),
            status=WorkflowRunStatus.PENDING,
            definition_snapshot=None,
            save=AsyncMock(),
        )
        node_runs = [SimpleNamespace(node_name="fetch")]
        fake_registry = {"tool": object()}
        auth_context = {"user_id": "user-1"}

        monkeypatch.setattr(runner.WorkflowDefinition, "get", AsyncMock(return_value=definition))
        monkeypatch.setattr(runner.WorkflowRun, "get", AsyncMock(return_value=run_doc))
        monkeypatch.setattr(runner.WorkflowRunner, "_build_registry", AsyncMock(return_value=fake_registry))
        monkeypatch.setattr(runner.WorkflowRunner, "_execute", AsyncMock())

        monkeypatch.setattr(runner.NodeRun, "workflow_run_id", _FieldExpr("workflow_run_id"), raising=False)
        find_query = SimpleNamespace(to_list=AsyncMock(return_value=node_runs))
        monkeypatch.setattr(runner.NodeRun, "find", lambda *args, **kwargs: find_query)

        r = _make_runner()
        user_id = "user-1"
        run_id = str(run_doc.id)
        actual_run, actual_nodes = await r.run(
            str(definition.id),
            "hello",
            auth_context=auth_context,
            user_id=user_id,
            existing_run_id=run_id,
        )

        assert actual_run is run_doc
        assert actual_nodes == node_runs
        runner.WorkflowRunner._build_registry.assert_awaited_once_with(definition, auth_context, user_id)
        runner.WorkflowRunner._execute.assert_awaited_once_with(run_doc, definition, "hello", fake_registry, None, None)

    @pytest.mark.asyncio
    async def test_run_uses_existing_definition_snapshot_instead_of_live_definition(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """run() must honor a pre-created run snapshot for deterministic replay."""
        snapshot_definition = _definition()
        run_doc = SimpleNamespace(
            id=PydanticObjectId(),
            status=WorkflowRunStatus.PENDING,
            definition_snapshot=snapshot_definition.model_dump(mode="json"),
            save=AsyncMock(),
        )
        node_runs = [SimpleNamespace(node_name="fetch")]
        fake_registry = {"tool": object()}

        get_live_definition = AsyncMock(side_effect=AssertionError("live definition should not be loaded"))
        monkeypatch.setattr(runner.WorkflowDefinition, "get", get_live_definition)
        monkeypatch.setattr(runner.WorkflowRun, "get", AsyncMock(return_value=run_doc))
        monkeypatch.setattr(runner.WorkflowRunner, "_build_registry", AsyncMock(return_value=fake_registry))
        monkeypatch.setattr(runner.WorkflowRunner, "_execute", AsyncMock())

        monkeypatch.setattr(runner.NodeRun, "workflow_run_id", _FieldExpr("workflow_run_id"), raising=False)
        find_query = SimpleNamespace(to_list=AsyncMock(return_value=node_runs))
        monkeypatch.setattr(runner.NodeRun, "find", lambda *args, **kwargs: find_query)

        r = _make_runner()
        await r.run(
            str(snapshot_definition.id),
            "hello",
            auth_context=None,
            user_id="user-1",
            existing_run_id=str(run_doc.id),
        )

        get_live_definition.assert_not_awaited()
        built_definition = runner.WorkflowRunner._build_registry.await_args.args[0]
        assert built_definition.name == snapshot_definition.name
        assert built_definition.nodes[0].id == snapshot_definition.nodes[0].id

    @pytest.mark.asyncio
    async def test_run_executes_existing_run_without_creating_new_run(self, monkeypatch: pytest.MonkeyPatch):
        definition = _definition()
        existing_run = SimpleNamespace(
            id=PydanticObjectId(),
            workflow_definition_id=definition.id,
            status=WorkflowRunStatus.PENDING,
            error_summary="old error",
            finished_at=datetime.now(UTC),
            initial_input=None,
            definition_snapshot=None,
            save=AsyncMock(),
        )
        node_runs = [SimpleNamespace(node_name="fetch")]
        fake_registry = {"tool": object()}
        auth_context = {"user_id": "user-1"}

        monkeypatch.setattr(runner.WorkflowDefinition, "get", AsyncMock(return_value=definition))
        monkeypatch.setattr(runner.WorkflowRun, "get", AsyncMock(return_value=existing_run))
        monkeypatch.setattr(runner.WorkflowRunner, "_build_registry", AsyncMock(return_value=fake_registry))
        monkeypatch.setattr(runner.WorkflowRunner, "_execute", AsyncMock())

        monkeypatch.setattr(runner.NodeRun, "workflow_run_id", _FieldExpr("workflow_run_id"), raising=False)
        find_query = SimpleNamespace(to_list=AsyncMock(return_value=node_runs))
        monkeypatch.setattr(runner.NodeRun, "find", lambda *args, **kwargs: find_query)

        r = _make_runner()
        actual_run, actual_nodes = await r.run(
            str(definition.id),
            "hello",
            auth_context=auth_context,
            user_id="user-1",
            existing_run_id=str(existing_run.id),
        )

        assert actual_run is existing_run
        assert actual_nodes == node_runs
        assert existing_run.status == WorkflowRunStatus.RUNNING
        assert existing_run.definition_snapshot["name"] == definition.name
        existing_run.save.assert_awaited_once()
        runner.WorkflowRunner._build_registry.assert_awaited_once_with(
            definition,
            auth_context,
            "user-1",
        )
        runner.WorkflowRunner._execute.assert_awaited_once_with(
            existing_run, definition, "hello", fake_registry, None, None
        )

    @pytest.mark.asyncio
    async def test_consent_preflight_pauses_before_workflow_execution(self, monkeypatch: pytest.MonkeyPatch):
        definition = _definition()
        run_doc = SimpleNamespace(
            id=PydanticObjectId(),
            status=WorkflowRunStatus.PENDING,
            definition_snapshot=None,
            pending_requirements=[],
            error_summary="old error",
            finished_at=datetime.now(UTC),
            save=AsyncMock(),
        )
        consent_error = McpConsentRequiredError(
            auth_url="https://registry.example.com/consent/server?nonce=abc",
            server_name="github",
            elicitation_id="elicitation-1",
        )
        monkeypatch.setattr(runner.WorkflowDefinition, "get", AsyncMock(return_value=definition))
        monkeypatch.setattr(runner.WorkflowRun, "get", AsyncMock(return_value=run_doc))
        monkeypatch.setattr(runner.WorkflowRunner, "_build_registry", AsyncMock(side_effect=consent_error))
        execute = AsyncMock()
        monkeypatch.setattr(runner.WorkflowRunner, "_execute", execute)
        monkeypatch.setattr(runner.NodeRun, "workflow_run_id", _FieldExpr("workflow_run_id"), raising=False)
        monkeypatch.setattr(
            runner.NodeRun,
            "find",
            lambda *args, **kwargs: SimpleNamespace(to_list=AsyncMock(return_value=[])),
        )

        actual_run, _ = await _make_runner().run(
            str(definition.id),
            "hello",
            auth_context={"user_id": "user-1"},
            user_id="user-1",
            existing_run_id=str(run_doc.id),
        )

        assert actual_run.status == WorkflowRunStatus.AWAITING_APPROVAL
        assert actual_run.pending_requirements[0]["requirement_kind"] == "mcp_consent"
        assert actual_run.pending_requirements[0]["consent_url"].endswith("nonce=abc")
        execute.assert_not_awaited()


@pytest.mark.unit
class TestBuildRegistry:
    @pytest.mark.asyncio
    async def test_extracts_keys_and_pool_nodes_from_definition(self, monkeypatch: pytest.MonkeyPatch):
        """_build_registry must forward executor_keys + pool_nodes to build_executor_registry."""
        from registry_pkgs.models.workflow import WorkflowNode

        definition = WorkflowDefinition.model_construct(
            id=PydanticObjectId(),
            name="test",
            nodes=[
                WorkflowNode(name="mcp-step", executor_key="mcp-tool", step_objective="run MCP step"),
                WorkflowNode(name="pool-step", a2a_pool=["agent-a", "agent-b"], step_objective="run pool step"),
            ],
        )

        captured = {}
        auth_context = {"user_id": "user-1"}

        async def fake_build(
            executor_keys,
            *,
            llm,
            auth_context,
            jwt_config,
            user_id,
            pool_nodes,
            selector_llm,
            a2a_httpx_client=None,
            headers_provider=None,
            redis_client=None,
            redis_key_prefix=None,
            mcp_access_authorizer=None,
            mcp_headers_provider=None,
        ):
            captured["executor_keys"] = executor_keys
            captured["pool_nodes"] = [n.name for n in pool_nodes]
            captured["auth_context"] = auth_context
            captured["user_id"] = user_id
            captured["redis_key_prefix"] = redis_key_prefix
            captured["mcp_access_authorizer"] = mcp_access_authorizer
            captured["mcp_headers_provider"] = mcp_headers_provider
            return {}

        monkeypatch.setattr(runner, "build_executor_registry", fake_build)

        access_authorizer = AsyncMock()
        r = _make_runner(
            redis_key_prefix="test-registry",
            mcp_access_authorizer=access_authorizer,
            mcp_headers_provider=lambda *args, **kwargs: {},
        )
        await r._build_registry(definition, auth_context, "user-1")

        assert captured["executor_keys"] == ["mcp-tool"]
        assert captured["pool_nodes"] == ["pool-step"]
        assert captured["auth_context"] is auth_context
        assert captured["user_id"] == "user-1"
        assert captured["redis_key_prefix"] == "test-registry"
        assert captured["mcp_access_authorizer"] is access_authorizer
        assert captured["mcp_headers_provider"] is r._mcp_headers_provider


@pytest.mark.unit
class TestRunSetsRunningStatus:
    @pytest.mark.asyncio
    async def test_transitions_pending_run_to_running_and_stamps_snapshot(self, monkeypatch: pytest.MonkeyPatch):
        """run() must set status=RUNNING and definition_snapshot before executing."""
        definition = _definition()
        run_doc = SimpleNamespace(
            id=PydanticObjectId(),
            status=WorkflowRunStatus.PENDING,
            definition_snapshot=None,
            save=AsyncMock(),
        )

        monkeypatch.setattr(runner.WorkflowDefinition, "get", AsyncMock(return_value=definition))
        monkeypatch.setattr(runner.WorkflowRun, "get", AsyncMock(return_value=run_doc))
        monkeypatch.setattr(runner.WorkflowRunner, "_build_registry", AsyncMock(return_value={}))
        monkeypatch.setattr(runner.WorkflowRunner, "_execute", AsyncMock())
        monkeypatch.setattr(runner.NodeRun, "workflow_run_id", _FieldExpr("workflow_run_id"), raising=False)
        find_query = SimpleNamespace(to_list=AsyncMock(return_value=[]))
        monkeypatch.setattr(runner.NodeRun, "find", lambda *args, **kwargs: find_query)

        r = _make_runner()
        await r.run(str(definition.id), "hello", auth_context=None, user_id=None, existing_run_id=str(run_doc.id))

        assert run_doc.status == WorkflowRunStatus.RUNNING
        assert run_doc.definition_snapshot["name"] == definition.name
        run_doc.save.assert_awaited_once()


@pytest.mark.unit
class TestExecute:
    @pytest.mark.asyncio
    async def test_compiles_workflow_and_syncs_run_after_success(self, monkeypatch: pytest.MonkeyPatch):
        workflow = SimpleNamespace(
            arun=AsyncMock(return_value=WorkflowRunOutput(content="done", status=RunStatus.completed, step_results=[]))
        )
        run_doc = SimpleNamespace(
            sync=AsyncMock(),
            save=AsyncMock(),
            id="run-1",
            status=WorkflowRunStatus.RUNNING,
            error_summary=None,
            final_output=None,
            finished_at=None,
        )
        fake_registry = {"tool": object()}

        monkeypatch.setattr(runner, "compile_workflow", lambda *args, **kwargs: workflow)
        r = _make_runner()

        await r._execute(run_doc, _definition(), "hello", fake_registry)

        workflow.arun.assert_awaited_once_with(
            input="hello",
            session_id="run-1",
            session_state={"user_text": "hello", "_workflow_run_id": "run-1"},
        )
        run_doc.sync.assert_awaited_once()
        run_doc.save.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_marks_run_failed_and_reraises_when_workflow_raises(self, monkeypatch: pytest.MonkeyPatch):
        workflow = SimpleNamespace(arun=AsyncMock(side_effect=RuntimeError("boom")))
        run_doc = SimpleNamespace(
            status=WorkflowRunStatus.RUNNING,
            error_summary=None,
            finished_at=None,
            save=AsyncMock(),
            id="run-1",
        )

        monkeypatch.setattr(runner, "compile_workflow", lambda *args, **kwargs: workflow)
        r = _make_runner()

        with pytest.raises(RuntimeError, match="boom"):
            await r._execute(run_doc, _definition(), "hello", {})

        assert run_doc.status == WorkflowRunStatus.FAILED
        assert run_doc.error_summary == "boom"
        assert isinstance(run_doc.finished_at, datetime)
        assert run_doc.finished_at.tzinfo == UTC
        run_doc.save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_marks_run_cancelled_when_workflow_is_cancelled(self, monkeypatch: pytest.MonkeyPatch):
        workflow = SimpleNamespace(arun=AsyncMock(side_effect=WorkflowCancelledError("Workflow cancelled by user")))
        run_doc = SimpleNamespace(
            status=WorkflowRunStatus.RUNNING,
            error_summary=None,
            finished_at=None,
            save=AsyncMock(),
            sync=AsyncMock(),
            id="run-1",
        )

        monkeypatch.setattr(runner, "compile_workflow", lambda *args, **kwargs: workflow)
        r = _make_runner()

        await r._execute(run_doc, _definition(), "hello", {})

        assert run_doc.status == WorkflowRunStatus.CANCELLED
        assert run_doc.error_summary == "Workflow cancelled by user"
        assert isinstance(run_doc.finished_at, datetime)
        assert run_doc.finished_at.tzinfo == UTC
        run_doc.save.assert_awaited_once()
        run_doc.sync.assert_not_called()


@pytest.mark.unit
class TestContinueRunHydrationFailure:
    @pytest.mark.asyncio
    async def test_hydration_failure_preserves_pending_and_marks_failed(self, monkeypatch: pytest.MonkeyPatch):
        """If hydrate_requirement fails, pending_requirements must survive and the run
        must be finalized FAILED — never wiped and stranded as RUNNING."""
        from unittest.mock import Mock

        run_oid = PydanticObjectId()
        original_pending = [{"step_id": "s1", "schema_version": 1}]
        run_doc = SimpleNamespace(
            id=run_oid,
            status=WorkflowRunStatus.RUNNING,
            definition_snapshot={"name": "demo"},
            pending_requirements=original_pending,
            agno_run_id=None,
            error_summary=None,
            finished_at=None,
            save=AsyncMock(),
        )

        collection = SimpleNamespace(update_one=AsyncMock(return_value=SimpleNamespace(modified_count=1)))
        fake_db = SimpleNamespace(get_collection=lambda name: collection)

        class _FakeClient:
            def __getitem__(self, name):
                return fake_db

        monkeypatch.setattr(runner.WorkflowRun, "get_settings", lambda: SimpleNamespace(name="workflow_runs"))
        monkeypatch.setattr(runner.WorkflowRun, "get", AsyncMock(return_value=run_doc))
        monkeypatch.setattr(runner, "definition_from_snapshot", lambda snapshot: SimpleNamespace())
        monkeypatch.setattr(runner, "hydrate_requirement", Mock(side_effect=RuntimeError("schema drift")))

        r = _make_runner(db_client=_FakeClient())

        with pytest.raises(RuntimeError, match="schema drift"):
            await r.continue_run(existing_run_id=str(run_oid), auth_context=None, user_id="u1")

        # The persisted pending requirements were NOT cleared (still recoverable).
        assert run_doc.pending_requirements == original_pending
        # The run was finalized FAILED rather than left stranded as RUNNING.
        assert run_doc.status == WorkflowRunStatus.FAILED
        run_doc.save.assert_awaited()
