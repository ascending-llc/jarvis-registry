"""Unit tests for the read-only federation metadata audit."""

import json
from unittest.mock import AsyncMock

import pytest
from pydantic import TypeAdapter

from registry_pkgs.models.enums import FederationProviderType
from registry_pkgs.models.federation_metadata import (
    A2AFederationMetadata,
    AgentCoreMcpFederationMetadata,
)
from scripts.verify import check_federation_metadata


def _mcp_metadata(
    *,
    runtime_arn: str,
) -> dict[str, str]:
    return {
        "providerType": FederationProviderType.AWS_AGENTCORE.value,
        "runtimeArn": runtime_arn,
        "runtimeId": "runtime-id",
        "runtimeName": "runtime-name",
        "runtimeVersion": "1",
        "runtimeStatus": "READY",
        "serverProtocol": "MCP",
    }


class _FakeCursor:
    def __init__(
        self,
        documents: list[dict[str, object]],
    ) -> None:
        self._documents = iter(documents)

    def __aiter__(self) -> "_FakeCursor":
        return self

    async def __anext__(self) -> dict[str, object]:
        try:
            return next(self._documents)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _FakeCollection:
    def __init__(
        self,
        documents: list[dict[str, object]],
    ) -> None:
        self._documents = documents

    def find(
        self,
        _query: dict[str, object],
        _projection: dict[str, object],
    ) -> _FakeCursor:
        return _FakeCursor(self._documents)


class _FakeDatabase:
    def __init__(
        self,
        collections: dict[str, list[dict[str, object]]],
    ) -> None:
        self._collections = collections

    def get_collection(
        self,
        collection_name: str,
    ) -> _FakeCollection:
        return _FakeCollection(self._collections.get(collection_name, []))


def test_schema_checks_use_exact_collection_and_provider_model() -> None:
    mcp_metadata = _mcp_metadata(runtime_arn="arn:mcp")
    del mcp_metadata["runtimeName"]
    del mcp_metadata["serverProtocol"]

    assert check_federation_metadata._core_field_errors(mcp_metadata, "mcpservers") == [
        "runtimeName",
        "serverProtocol",
    ]
    assert check_federation_metadata._find_unknown_extras(
        {**_mcp_metadata(runtime_arn="arn:mcp"), "failureReason": "wrong shape"},
        FederationProviderType.AWS_AGENTCORE.value,
        "mcpservers",
    ) == ["failureReason"]
    assert check_federation_metadata._find_unknown_extras(
        {
            "providerType": FederationProviderType.AZURE_AI_FOUNDRY.value,
            "runtimeArn": "azure-agent",
            "agentName": "azure-agent",
            "agentVersion": "1",
            "enrichedAt": "2026-07-30T00:00:00Z",
        },
        FederationProviderType.AZURE_AI_FOUNDRY.value,
        "a2a_agents",
    ) == ["enrichedAt"]


@pytest.mark.asyncio
async def test_audit_classifies_provider_schema_and_datetime_issues(monkeypatch) -> None:
    documents = [
        {
            "_id": "missing-provider",
            "federationRefId": "federation-1",
            "federationMetadata": {"runtimeArn": "arn:missing-provider"},
        },
        {
            "_id": "invalid-provider",
            "federationRefId": "federation-1",
            "federationMetadata": {"providerType": "unknown", "runtimeArn": "arn:invalid"},
        },
        {
            "_id": "missing-core",
            "federationRefId": "federation-1",
            "federationMetadata": {
                "providerType": FederationProviderType.AWS_AGENTCORE.value,
                "runtimeArn": "arn:missing-core",
                "runtimeId": "runtime-id",
                "runtimeVersion": "1",
                "runtimeStatus": "READY",
            },
        },
        {
            "_id": "unknown-extra",
            "federationRefId": "federation-1",
            "federationMetadata": {
                **_mcp_metadata(runtime_arn="arn:extra"),
                "failureReason": "wrong model",
            },
        },
        {
            "_id": "bad-datetime",
            "federationRefId": "federation-1",
            "federationMetadata": {
                **_mcp_metadata(runtime_arn="arn:bad-datetime"),
                "createdAt": "not-a-date",
            },
        },
        {
            "_id": "empty-runtime-arn",
            "federationRefId": "federation-1",
            "federationMetadata": _mcp_metadata(runtime_arn=""),
        },
        {
            "_id": "azure-mcp",
            "federationRefId": "federation-1",
            "federationMetadata": {
                "providerType": FederationProviderType.AZURE_AI_FOUNDRY.value,
                "runtimeArn": "azure-agent",
                "agentName": "azure-agent",
                "agentVersion": "1",
            },
        },
    ]
    monkeypatch.setattr(
        check_federation_metadata.MongoDB,
        "get_database",
        lambda: _FakeDatabase({"mcpservers": documents}),
    )

    result = await check_federation_metadata._audit_collection(
        collection_name="mcpservers",
        metadata_adapter=TypeAdapter(AgentCoreMcpFederationMetadata),
        sample_limit=20,
    )

    assert result.issue_counts["missing_provider_type"] == 1
    assert result.issue_counts["invalid_provider"] == 1
    assert result.issue_counts["missing_core_fields"] == 2
    assert result.issue_counts["unknown_extras"] == 1
    assert result.issue_counts["bad_datetime"] == 1
    assert result.issue_counts["empty_runtime_arn"] == 1
    assert result.issue_counts["azure_mcp_anomaly"] == 1
    assert result.issue_counts["invalid_samples"] == 3


@pytest.mark.asyncio
async def test_audit_accepts_valid_azure_a2a_metadata(monkeypatch) -> None:
    documents = [
        {
            "_id": "azure-a2a",
            "federationRefId": "federation-1",
            "federationMetadata": {
                "providerType": FederationProviderType.AZURE_AI_FOUNDRY.value,
                "runtimeArn": "azure-agent",
                "agentName": "azure-agent",
                "agentVersion": "1",
                "createdAt": 1_784_276_472,
            },
        }
    ]
    monkeypatch.setattr(
        check_federation_metadata.MongoDB,
        "get_database",
        lambda: _FakeDatabase({"a2a_agents": documents}),
    )

    result = await check_federation_metadata._audit_collection(
        collection_name="a2a_agents",
        metadata_adapter=TypeAdapter(A2AFederationMetadata),
        sample_limit=20,
    )

    assert result.by_provider == {FederationProviderType.AZURE_AI_FOUNDRY.value: 1}
    assert result.issue_counts == {}


@pytest.mark.asyncio
async def test_audit_flags_inconsistent_federation_state(monkeypatch) -> None:
    documents = [
        {
            "_id": "missing-metadata",
            "federationRefId": "federation-1",
        },
        {
            "_id": "missing-reference",
            "federationMetadata": _mcp_metadata(runtime_arn="arn:missing-reference"),
        },
        {
            "_id": "plain-resource",
        },
    ]
    monkeypatch.setattr(
        check_federation_metadata.MongoDB,
        "get_database",
        lambda: _FakeDatabase({"mcpservers": documents}),
    )

    result = await check_federation_metadata._audit_collection(
        collection_name="mcpservers",
        metadata_adapter=TypeAdapter(AgentCoreMcpFederationMetadata),
        sample_limit=20,
    )

    assert result.total == 3
    assert result.federated == 2
    assert result.non_federated == 1
    assert result.issue_counts["inconsistent_federation_state"] == 2
    assert result.inconsistent_federation_state == [
        {
            "_id": "missing-metadata",
            "federationRefId": "federation-1",
            "reason": "missing federationMetadata",
        },
        {
            "_id": "missing-reference",
            "federationRefId": None,
            "reason": "missing federationRefId",
        },
    ]


@pytest.mark.asyncio
async def test_audit_detects_duplicate_runtime_arn_only_within_federation(monkeypatch) -> None:
    documents = [
        {
            "_id": "same-federation-1",
            "federationRefId": "federation-1",
            "federationMetadata": _mcp_metadata(runtime_arn="arn:shared"),
        },
        {
            "_id": "same-federation-2",
            "federationRefId": "federation-1",
            "federationMetadata": _mcp_metadata(runtime_arn="arn:shared"),
        },
        {
            "_id": "different-federation",
            "federationRefId": "federation-2",
            "federationMetadata": _mcp_metadata(runtime_arn="arn:shared"),
        },
    ]
    monkeypatch.setattr(
        check_federation_metadata.MongoDB,
        "get_database",
        lambda: _FakeDatabase({"mcpservers": documents}),
    )

    result = await check_federation_metadata._audit_collection(
        collection_name="mcpservers",
        metadata_adapter=TypeAdapter(AgentCoreMcpFederationMetadata),
        sample_limit=20,
    )

    assert result.duplicate_runtime_arns == [
        {
            "federationRefId": "federation-1",
            "runtimeArn": "arn:shared",
            "documentIds": ["same-federation-1", "same-federation-2"],
        }
    ]
    assert result.issue_counts["duplicate_runtime_arns"] == 1


@pytest.mark.asyncio
async def test_run_returns_failure_for_blocking_audit_issue(monkeypatch, capsys) -> None:
    database = _FakeDatabase(
        {
            "mcpservers": [
                {
                    "_id": "missing-metadata",
                    "federationRefId": "federation-1",
                }
            ],
            "a2a_agents": [],
        }
    )
    monkeypatch.setattr(check_federation_metadata.MongoDB, "get_database", lambda: database)
    connect_db = AsyncMock()
    close_db = AsyncMock()
    monkeypatch.setattr(check_federation_metadata.MongoDB, "connect_db", connect_db)
    monkeypatch.setattr(check_federation_metadata.MongoDB, "close_db", close_db)

    exit_code = await check_federation_metadata._run(sample_limit=0, db_name="jarvis")
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output["summary"]["has_blocking_issues"] is True
    assert output["results"][0]["issue_counts"]["inconsistent_federation_state"] == 1
    assert output["results"][0]["inconsistent_federation_state"] == []
    connect_db.assert_awaited_once()
    close_db.assert_awaited_once()
