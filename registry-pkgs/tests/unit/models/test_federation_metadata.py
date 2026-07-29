"""Tests for typed federation metadata models."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from registry_pkgs.models.enums import FederationProviderType
from registry_pkgs.models.federation_metadata import (
    AgentCoreA2AFederationMetadata,
    AgentCoreMcpFederationMetadata,
    AzureFoundryFederationMetadata,
    detect_runtime_version_change,
    extract_runtime_arn,
    extract_runtime_version,
)

_A2A_ADAPTER = TypeAdapter(AgentCoreA2AFederationMetadata | AzureFoundryFederationMetadata)


class TestAgentCoreMcpFederationMetadata:
    def test_constructs_with_required_fields(self):
        metadata = AgentCoreMcpFederationMetadata(
            providerType=FederationProviderType.AWS_AGENTCORE,
            runtimeArn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/mcp",
            runtimeId="r1",
            runtimeName="mcp-runtime",
            runtimeVersion="1",
            runtimeStatus="READY",
            serverProtocol="MCP",
        )
        assert metadata.providerType == FederationProviderType.AWS_AGENTCORE.value
        assert metadata.runtimeArn == "arn:aws:bedrock-agentcore:us-east-1:123:runtime/mcp"

    def test_rejects_missing_required_field(self):
        with pytest.raises(ValidationError):
            AgentCoreMcpFederationMetadata(
                providerType=FederationProviderType.AWS_AGENTCORE,
                runtimeArn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/mcp",
                runtimeId="r1",
                runtimeName="mcp-runtime",
                runtimeVersion="1",
                runtimeStatus="READY",
                # missing serverProtocol
            )

    def test_rejects_invalid_provider_type(self):
        with pytest.raises(ValidationError):
            AgentCoreMcpFederationMetadata(
                providerType=FederationProviderType.AZURE_AI_FOUNDRY,
                runtimeArn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/mcp",
                runtimeId="r1",
                runtimeName="mcp-runtime",
                runtimeVersion="1",
                runtimeStatus="READY",
                serverProtocol="MCP",
            )

    def test_keeps_unknown_extra_fields(self):
        metadata = AgentCoreMcpFederationMetadata(
            providerType=FederationProviderType.AWS_AGENTCORE,
            runtimeArn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/mcp",
            runtimeId="r1",
            runtimeName="mcp-runtime",
            runtimeVersion="1",
            runtimeStatus="READY",
            serverProtocol="MCP",
            futureField="kept",
        )
        assert metadata.model_extra is not None
        assert metadata.model_extra.get("futureField") == "kept"

    def test_model_dump_preserves_fields(self):
        metadata = AgentCoreMcpFederationMetadata(
            providerType=FederationProviderType.AWS_AGENTCORE,
            runtimeArn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/mcp",
            runtimeId="r1",
            runtimeName="mcp-runtime",
            runtimeVersion="1",
            runtimeStatus="READY",
            serverProtocol="MCP",
        )
        payload = metadata.model_dump(mode="json")
        assert payload["providerType"] == FederationProviderType.AWS_AGENTCORE.value
        assert payload["runtimeArn"] == "arn:aws:bedrock-agentcore:us-east-1:123:runtime/mcp"


class TestAgentCoreA2AFederationMetadata:
    def test_constructs_with_required_fields(self):
        metadata = AgentCoreA2AFederationMetadata(
            providerType=FederationProviderType.AWS_AGENTCORE,
            runtimeArn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/a2a",
            runtimeId="r1",
            runtimeVersion="1",
            runtimeStatus="READY",
        )
        assert metadata.runtimeArn == "arn:aws:bedrock-agentcore:us-east-1:123:runtime/a2a"

    def test_runtime_version_accepts_int(self):
        metadata = AgentCoreA2AFederationMetadata(
            providerType=FederationProviderType.AWS_AGENTCORE,
            runtimeArn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/a2a",
            runtimeId="r1",
            runtimeVersion=2,
            runtimeStatus="READY",
        )
        assert metadata.runtimeVersion == 2

    def test_validate_assignment_enforces_type(self):
        metadata = AgentCoreA2AFederationMetadata(
            providerType=FederationProviderType.AWS_AGENTCORE,
            runtimeArn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/a2a",
            runtimeId="r1",
            runtimeVersion="1",
            runtimeStatus="READY",
        )
        with pytest.raises(ValidationError):
            metadata.runtimeVersion = None  # type: ignore[assignment]

    def test_authorizer_configuration_is_optional_container(self):
        metadata = AgentCoreA2AFederationMetadata(
            providerType=FederationProviderType.AWS_AGENTCORE,
            runtimeArn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/a2a",
            runtimeId="r1",
            runtimeVersion="1",
            runtimeStatus="READY",
            authorizerConfiguration={"authorizerType": "JWT"},
        )
        assert metadata.authorizerConfiguration == {"authorizerType": "JWT"}


class TestAzureFoundryFederationMetadata:
    def test_constructs_with_required_fields(self):
        metadata = AzureFoundryFederationMetadata(
            providerType=FederationProviderType.AZURE_AI_FOUNDRY,
            runtimeArn="azure-agent",
            agentName="azure-agent",
            agentVersion="1",
        )
        assert metadata.providerType == FederationProviderType.AZURE_AI_FOUNDRY.value
        assert metadata.agentCardPath is None

    def test_agent_version_accepts_int(self):
        metadata = AzureFoundryFederationMetadata(
            providerType=FederationProviderType.AZURE_AI_FOUNDRY,
            runtimeArn="azure-agent",
            agentName="azure-agent",
            agentVersion=3,
        )
        assert metadata.agentVersion == 3

    def test_keeps_extra_fields(self):
        metadata = AzureFoundryFederationMetadata(
            providerType=FederationProviderType.AZURE_AI_FOUNDRY,
            runtimeArn="azure-agent",
            agentName="azure-agent",
            agentVersion="1",
            versionId="asst_abc",
            legacyField="preserved",
        )
        assert metadata.versionId == "asst_abc"
        assert metadata.model_extra is not None
        assert metadata.model_extra.get("legacyField") == "preserved"


class TestA2AFederationMetadataDiscriminator:
    def test_a2a_union_resolves_agentcore(self):
        result = _A2A_ADAPTER.validate_python(
            {
                "providerType": FederationProviderType.AWS_AGENTCORE.value,
                "runtimeArn": "arn:aws:bedrock-agentcore:us-east-1:123:runtime/a2a",
                "runtimeId": "r1",
                "runtimeVersion": "1",
                "runtimeStatus": "READY",
            }
        )
        assert isinstance(result, AgentCoreA2AFederationMetadata)

    def test_a2a_union_resolves_azure(self):
        result = _A2A_ADAPTER.validate_python(
            {
                "providerType": FederationProviderType.AZURE_AI_FOUNDRY.value,
                "runtimeArn": "azure-agent",
                "agentName": "azure-agent",
                "agentVersion": "1",
            }
        )
        assert isinstance(result, AzureFoundryFederationMetadata)

    def test_a2a_union_rejects_unknown_provider(self):
        with pytest.raises(ValidationError):
            _A2A_ADAPTER.validate_python(
                {
                    "providerType": "unknown_provider",
                    "runtimeArn": "x",
                }
            )


class TestHelperFunctions:
    def test_extract_runtime_arn_prefers_runtime_arn(self):
        metadata = AgentCoreA2AFederationMetadata(
            providerType=FederationProviderType.AWS_AGENTCORE,
            runtimeArn="arn:aws:runtime/a2a",
            runtimeId="r1",
            runtimeVersion="1",
            runtimeStatus="READY",
        )
        assert extract_runtime_arn(metadata) == "arn:aws:runtime/a2a"

    def test_extract_runtime_version_for_agentcore(self):
        metadata = AgentCoreMcpFederationMetadata(
            providerType=FederationProviderType.AWS_AGENTCORE,
            runtimeArn="arn:aws:runtime/mcp",
            runtimeId="r1",
            runtimeName="mcp",
            runtimeVersion=5,
            runtimeStatus="READY",
            serverProtocol="MCP",
        )
        assert extract_runtime_version(metadata) == "5"

    def test_extract_runtime_version_for_azure(self):
        metadata = AzureFoundryFederationMetadata(
            providerType=FederationProviderType.AZURE_AI_FOUNDRY,
            runtimeArn="azure-agent",
            agentName="azure-agent",
            agentVersion=9,
        )
        assert extract_runtime_version(metadata) == "9"

    def test_detect_runtime_version_change_agentcore(self):
        existing = AgentCoreA2AFederationMetadata(
            providerType=FederationProviderType.AWS_AGENTCORE,
            runtimeArn="arn:aws:runtime/a2a",
            runtimeId="r1",
            runtimeVersion="1",
            runtimeStatus="READY",
        )
        new = AgentCoreA2AFederationMetadata(
            providerType=FederationProviderType.AWS_AGENTCORE,
            runtimeArn="arn:aws:runtime/a2a",
            runtimeId="r1",
            runtimeVersion="2",
            runtimeStatus="READY",
        )
        assert detect_runtime_version_change(existing, new) == ["runtimeVersion: 1 -> 2"]

    def test_detect_runtime_version_change_azure(self):
        existing = AzureFoundryFederationMetadata(
            providerType=FederationProviderType.AZURE_AI_FOUNDRY,
            runtimeArn="azure-agent",
            agentName="azure-agent",
            agentVersion="1",
        )
        new = AzureFoundryFederationMetadata(
            providerType=FederationProviderType.AZURE_AI_FOUNDRY,
            runtimeArn="azure-agent",
            agentName="azure-agent",
            agentVersion="2",
        )
        assert detect_runtime_version_change(existing, new) == ["agentVersion: 1 -> 2"]

    def test_detect_runtime_version_change_ignores_non_version_drift(self):
        existing = AgentCoreA2AFederationMetadata(
            providerType=FederationProviderType.AWS_AGENTCORE,
            runtimeArn="arn:aws:runtime/a2a",
            runtimeId="r1",
            runtimeVersion="1",
            runtimeStatus="READY",
        )
        new = AgentCoreA2AFederationMetadata(
            providerType=FederationProviderType.AWS_AGENTCORE,
            runtimeArn="arn:aws:runtime/a2a",
            runtimeId="r1",
            runtimeVersion="1",
            runtimeStatus="UPDATING",
        )
        assert detect_runtime_version_change(existing, new) == []
