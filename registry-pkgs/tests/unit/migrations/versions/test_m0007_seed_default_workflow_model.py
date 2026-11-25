"""Contract tests for explicit legacy workflow-model seeding."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId
from pymongo.errors import AutoReconnect, DuplicateKeyError

from registry_pkgs.migrations.versions import m0007_seed_default_workflow_model as migration
from registry_pkgs.models.model_source import ModelSource


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> dict[str, SimpleNamespace]:
    for key in ["AWS_BEDROCK_SONNET_AIP_ARN", "AWS_WORKFLOW_LLM_MODEL", "AWS_BEDROCK_REQUIRE_AIP", "AWS_REGION"]:
        monkeypatch.delenv(key, raising=False)
    selections = SimpleNamespace(
        find_one=AsyncMock(return_value=None),
        update_one=AsyncMock(
            return_value=SimpleNamespace(matched_count=0, modified_count=0, upserted_id=migration.SELECTION_ID)
        ),
    )
    sources = SimpleNamespace(insert_one=AsyncMock(), find_one=AsyncMock())
    return {"model_gateway_selection": selections, "model_sources": sources}


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", "  "])
async def test_no_explicit_model_makes_no_business_writes(
    store: dict[str, SimpleNamespace], monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is not None:
        monkeypatch.setenv("AWS_BEDROCK_SONNET_AIP_ARN", value)
        monkeypatch.setenv("AWS_WORKFLOW_LLM_MODEL", value)
    monkeypatch.setenv("AWS_BEDROCK_REQUIRE_AIP", "true")
    await migration.up(store)
    store["model_sources"].insert_one.assert_not_awaited()
    store["model_gateway_selection"].update_one.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arn,model,expected", [(" profile ", "other", "profile"), ("  ", " model ", "model"), ("", "model", "model")]
)
@pytest.mark.parametrize("region", [None, "  ", " us-west-2 "])
async def test_seed_document_and_conditional_selection(
    store: dict[str, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
    arn: str,
    model: str,
    expected: str,
    region: str | None,
) -> None:
    monkeypatch.setenv("AWS_BEDROCK_SONNET_AIP_ARN", arn)
    monkeypatch.setenv("AWS_WORKFLOW_LLM_MODEL", model)
    if region is not None:
        monkeypatch.setenv("AWS_REGION", region)
    monkeypatch.setattr(ModelSource, "get_pymongo_collection", lambda *_args: store["model_sources"])
    await migration.up(store)
    doc = store["model_sources"].insert_one.await_args.args[0]
    assert doc["_id"] == migration.MIGRATED_MODEL_SOURCE_ID
    assert doc["mode"] == "chat"
    assert doc["tags"] == []
    assert doc["createdAt"] == doc["updatedAt"]
    assert doc["createdAt"].tzinfo is not None
    assert all(value is not None for value in doc.values())
    assert doc["providerConfig"] == {
        "providerType": "aws_bedrock",
        "awsRegion": "us-west-2" if region and region.strip() else "us-east-1",
        "modelIdOrArn": expected,
        "baseModelId": expected,
    }
    assert ModelSource.model_validate(doc).id == migration.MIGRATED_MODEL_SOURCE_ID
    store["model_gateway_selection"].update_one.assert_awaited_once_with(
        {"_id": migration.SELECTION_ID, "defaultWorkflowModelSourceId": None},
        {"$set": {"defaultWorkflowModelSourceId": migration.MIGRATED_MODEL_SOURCE_ID, "updatedAt": doc["updatedAt"]}},
        upsert=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("default", [ObjectId(), "bad-id", ""])
async def test_existing_selection_is_never_repaired(
    store: dict[str, SimpleNamespace], monkeypatch: pytest.MonkeyPatch, default: ObjectId | str
) -> None:
    monkeypatch.setenv("AWS_WORKFLOW_LLM_MODEL", "model")
    store["model_gateway_selection"].find_one.return_value = {"defaultWorkflowModelSourceId": default}
    await migration.up(store)
    store["model_sources"].insert_one.assert_not_awaited()
    store["model_gateway_selection"].update_one.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", [None, "metadata", "deleted", "mode", "identity", "region", "missing", "bad-config", "empty-base"]
)
async def test_duplicate_model_requires_matching_available_source(
    store: dict[str, SimpleNamespace], monkeypatch: pytest.MonkeyPatch, change: str | None
) -> None:
    monkeypatch.setenv("AWS_WORKFLOW_LLM_MODEL", "model")
    await migration.up(store)
    doc = deepcopy(store["model_sources"].insert_one.await_args.args[0])
    if change == "metadata":
        doc["providerConfig"]["baseModelId"] = "corrected-base-model"
    elif change == "deleted":
        doc["deletedAt"] = doc["updatedAt"]
    elif change == "mode":
        doc["mode"] = "embedding"
    elif change == "identity":
        doc["providerConfig"]["modelIdOrArn"] = "different"
    elif change == "region":
        doc["providerConfig"]["awsRegion"] = "other-region"
    elif change == "bad-config":
        doc["providerConfig"] = "bad"
    elif change == "empty-base":
        doc["providerConfig"]["baseModelId"] = " "
    store["model_sources"].insert_one.side_effect = DuplicateKeyError("duplicate")
    store["model_sources"].find_one.return_value = None if change == "missing" else doc
    store["model_gateway_selection"].update_one.reset_mock()
    if change in [None, "metadata"]:
        await migration.up(store)
        store["model_gateway_selection"].update_one.assert_awaited_once()
    else:
        with pytest.raises(ValueError, match="Cannot reuse"):
            await migration.up(store)
        store["model_gateway_selection"].update_one.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("winner", [ObjectId(), None])
async def test_selection_duplicate_requires_a_concurrent_selection(
    store: dict[str, SimpleNamespace], monkeypatch: pytest.MonkeyPatch, winner: ObjectId | None
) -> None:
    monkeypatch.setenv("AWS_WORKFLOW_LLM_MODEL", "model")
    store["model_gateway_selection"].find_one.side_effect = [None, {"defaultWorkflowModelSourceId": winner}]
    store["model_gateway_selection"].update_one.side_effect = DuplicateKeyError("duplicate")
    if winner is None:
        with pytest.raises(DuplicateKeyError):
            await migration.up(store)
    else:
        await migration.up(store)
    assert store["model_gateway_selection"].update_one.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["insert", "select"])
async def test_non_duplicate_database_errors_propagate(
    store: dict[str, SimpleNamespace], monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    monkeypatch.setenv("AWS_WORKFLOW_LLM_MODEL", "model")
    method = store["model_sources"].insert_one if operation == "insert" else store["model_gateway_selection"].update_one
    method.side_effect = AutoReconnect("offline")
    with pytest.raises(AutoReconnect):
        await migration.up(store)


@pytest.mark.asyncio
@pytest.mark.parametrize("reused_base", [None, "arn", "anthropic.claude-sonnet"])
async def test_aip_placeholder_warns_until_canonical_base_model_is_configured(
    store: dict[str, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    reused_base: str | None,
) -> None:
    import logging

    arn = "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/test"
    monkeypatch.setenv("AWS_BEDROCK_SONNET_AIP_ARN", arn)
    if reused_base is not None:
        store["model_sources"].insert_one.side_effect = DuplicateKeyError("duplicate")
        store["model_sources"].find_one.return_value = {
            "mode": "chat",
            "providerConfig": {
                "providerType": "aws_bedrock",
                "awsRegion": "us-east-1",
                "modelIdOrArn": arn,
                "baseModelId": arn if reused_base == "arn" else reused_base,
            },
        }
    with caplog.at_level(logging.WARNING, logger=migration.__name__):
        await migration.up(store)
    if reused_base == "anthropic.claude-sonnet":
        assert "SkillSpector" not in caplog.text
    else:
        assert "canonical baseModelId" in caplog.text
        assert "SkillSpector may scan without an LLM" in caplog.text
        assert "dedicated scan model" in caplog.text
    store["model_gateway_selection"].update_one.assert_awaited_once()
