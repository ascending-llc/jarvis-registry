"""Deterministic federation metadata factories shared by tests."""

from typing import Any

from registry_pkgs.models.enums import FederationProviderType
from registry_pkgs.models.federation_metadata import (
    AgentCoreA2AFederationMetadata,
    AgentCoreMcpFederationMetadata,
    AzureFoundryFederationMetadata,
)


def make_agentcore_a2a_metadata(
    *,
    runtime_arn: str = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/test-a2a",
    runtime_id: str = "test-a2a",
    runtime_version: str | int = "1",
    runtime_status: str = "READY",
    runtime_tags: dict[str, str] | None = None,
    enrichment_error: str | None = None,
    **overrides: Any,
) -> AgentCoreA2AFederationMetadata:
    """Build complete AWS AgentCore A2A metadata."""
    payload: dict[str, Any] = {
        "providerType": FederationProviderType.AWS_AGENTCORE,
        "runtimeArn": runtime_arn,
        "runtimeId": runtime_id,
        "runtimeVersion": runtime_version,
        "runtimeStatus": runtime_status,
    }
    if runtime_tags is not None:
        payload["runtimeTags"] = runtime_tags
    if enrichment_error is not None:
        payload["enrichmentError"] = enrichment_error
    payload.update(overrides)
    return AgentCoreA2AFederationMetadata(**payload)


def make_agentcore_mcp_metadata(
    *,
    runtime_arn: str = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/test-mcp",
    runtime_id: str = "test-mcp",
    runtime_name: str = "test-mcp",
    runtime_version: str | int = "1",
    runtime_status: str = "READY",
    server_protocol: str = "MCP",
    runtime_tags: dict[str, str] | None = None,
    enrichment_error: str | None = None,
    **overrides: Any,
) -> AgentCoreMcpFederationMetadata:
    """Build complete AWS AgentCore MCP metadata."""
    payload: dict[str, Any] = {
        "providerType": FederationProviderType.AWS_AGENTCORE,
        "runtimeArn": runtime_arn,
        "runtimeId": runtime_id,
        "runtimeName": runtime_name,
        "runtimeVersion": runtime_version,
        "runtimeStatus": runtime_status,
        "serverProtocol": server_protocol,
    }
    if runtime_tags is not None:
        payload["runtimeTags"] = runtime_tags
    if enrichment_error is not None:
        payload["enrichmentError"] = enrichment_error
    payload.update(overrides)
    return AgentCoreMcpFederationMetadata(**payload)


def make_azure_foundry_metadata(
    *,
    runtime_arn: str = "test-foundry-agent",
    agent_name: str = "test-foundry-agent",
    agent_version: str | int = "1",
    **overrides: Any,
) -> AzureFoundryFederationMetadata:
    """Build complete Azure AI Foundry A2A metadata."""
    payload: dict[str, Any] = {
        "providerType": FederationProviderType.AZURE_AI_FOUNDRY,
        "runtimeArn": runtime_arn,
        "agentName": agent_name,
        "agentVersion": agent_version,
    }
    payload.update(overrides)
    return AzureFoundryFederationMetadata(**payload)
