from datetime import UTC, datetime
from types import SimpleNamespace

import boto3
import pytest
from botocore.stub import Stubber

from registry.services.federation.agentcore_client import AgentCoreFederationClient


@pytest.mark.unit
@pytest.mark.asyncio
class TestAgentCoreFederationClient:
    async def test_discover_runtime_entities_classifies_mcp_and_a2a_with_stubber(self, monkeypatch):
        client = AgentCoreFederationClient(region="us-east-1")

        boto_client = boto3.client(
            "bedrock-agentcore-control",
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        stubber = Stubber(boto_client)
        stubber.add_response(
            "list_agent_runtimes",
            {
                "agentRuntimes": [
                    {
                        "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:123:runtime/r1",
                        "agentRuntimeId": "r1",
                        "agentRuntimeVersion": "1",
                        "agentRuntimeName": "runtime-mcp",
                        "description": "mcp runtime",
                        "lastUpdatedAt": datetime.now(UTC),
                        "status": "READY",
                    },
                    {
                        "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:123:runtime/r2",
                        "agentRuntimeId": "r2",
                        "agentRuntimeVersion": "2",
                        "agentRuntimeName": "runtime-a2a",
                        "description": "a2a runtime",
                        "lastUpdatedAt": datetime.now(UTC),
                        "status": "READY",
                    },
                    {
                        "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:123:runtime/r3",
                        "agentRuntimeId": "r3",
                        "agentRuntimeVersion": "1",
                        "agentRuntimeName": "runtime-http",
                        "description": "http runtime",
                        "lastUpdatedAt": datetime.now(UTC),
                        "status": "READY",
                    },
                ]
            },
            {"maxResults": 100},
        )
        stubber.add_response(
            "get_agent_runtime",
            {
                "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:123:runtime/r1",
                "agentRuntimeId": "r1",
                "agentRuntimeName": "runtime-mcp",
                "agentRuntimeVersion": "1",
                "status": "READY",
                "createdAt": datetime.now(UTC),
                "lastUpdatedAt": datetime.now(UTC),
                "roleArn": "arn:aws:iam::123:role/test-role",
                "networkConfiguration": {"networkMode": "PUBLIC"},
                "lifecycleConfiguration": {"idleRuntimeSessionTimeout": 900, "maxLifetime": 3600},
                "protocolConfiguration": {"serverProtocol": "MCP"},
            },
            {"agentRuntimeId": "r1", "agentRuntimeVersion": "1"},
        )
        stubber.add_response(
            "get_agent_runtime",
            {
                "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:123:runtime/r2",
                "agentRuntimeId": "r2",
                "agentRuntimeName": "runtime-a2a",
                "agentRuntimeVersion": "2",
                "status": "READY",
                "createdAt": datetime.now(UTC),
                "lastUpdatedAt": datetime.now(UTC),
                "roleArn": "arn:aws:iam::123:role/test-role",
                "networkConfiguration": {"networkMode": "PUBLIC"},
                "lifecycleConfiguration": {"idleRuntimeSessionTimeout": 900, "maxLifetime": 3600},
                "protocolConfiguration": {"serverProtocol": "A2A"},
            },
            {"agentRuntimeId": "r2", "agentRuntimeVersion": "2"},
        )
        stubber.add_response(
            "get_agent_runtime",
            {
                "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:123:runtime/r3",
                "agentRuntimeId": "r3",
                "agentRuntimeName": "runtime-http",
                "agentRuntimeVersion": "1",
                "status": "READY",
                "createdAt": datetime.now(UTC),
                "lastUpdatedAt": datetime.now(UTC),
                "roleArn": "arn:aws:iam::123:role/test-role",
                "networkConfiguration": {"networkMode": "PUBLIC"},
                "lifecycleConfiguration": {"idleRuntimeSessionTimeout": 900, "maxLifetime": 3600},
                "protocolConfiguration": {"serverProtocol": "HTTP"},
            },
            {"agentRuntimeId": "r3", "agentRuntimeVersion": "1"},
        )

        monkeypatch.setattr(client, "_get_control_client", _async_return(boto_client))
        monkeypatch.setattr(client, "_reconcile_runtime_type", _async_return(None))
        monkeypatch.setattr(
            client,
            "_transform_runtime_to_mcp_server",
            lambda runtime_detail, _region, _author_id=None: SimpleNamespace(
                federationMetadata={"runtimeVersion": runtime_detail["agentRuntimeVersion"]}
            ),
        )
        monkeypatch.setattr(
            client,
            "_transform_runtime_to_a2a_agent",
            lambda runtime_detail, _region, _author_id=None: SimpleNamespace(
                federationMetadata={"runtimeVersion": runtime_detail["agentRuntimeVersion"]}
            ),
        )

        with stubber:
            result = await client.discover_runtime_entities(enrich_protocol_payloads=False)

        assert len(result["mcp_servers"]) == 1
        assert len(result["a2a_agents"]) == 1
        assert len(result["skipped_runtimes"]) == 1
        assert result["mcp_servers"][0].federationMetadata["runtimeVersion"] == "1"
        assert result["a2a_agents"][0].federationMetadata["runtimeVersion"] == "2"

    async def test_discover_runtime_entities_filters_by_runtime_arns_with_stubber(self, monkeypatch):
        client = AgentCoreFederationClient(region="us-east-1")

        target_arn = "arn:aws:bedrock-agentcore:us-east-1:123:runtime/r2"
        boto_client = boto3.client(
            "bedrock-agentcore-control",
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        stubber = Stubber(boto_client)
        stubber.add_response(
            "list_agent_runtimes",
            {
                "agentRuntimes": [
                    {
                        "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:123:runtime/r1",
                        "agentRuntimeId": "r1",
                        "agentRuntimeVersion": "1",
                        "agentRuntimeName": "runtime-r1",
                        "description": "runtime r1",
                        "lastUpdatedAt": datetime.now(UTC),
                        "status": "READY",
                    },
                    {
                        "agentRuntimeArn": target_arn,
                        "agentRuntimeId": "r2",
                        "agentRuntimeVersion": "2",
                        "agentRuntimeName": "runtime-r2",
                        "description": "runtime r2",
                        "lastUpdatedAt": datetime.now(UTC),
                        "status": "READY",
                    },
                ]
            },
            {"maxResults": 100},
        )
        stubber.add_response(
            "get_agent_runtime",
            {
                "agentRuntimeArn": target_arn,
                "agentRuntimeId": "r2",
                "agentRuntimeName": "runtime-a2a",
                "agentRuntimeVersion": "2",
                "status": "READY",
                "createdAt": datetime.now(UTC),
                "lastUpdatedAt": datetime.now(UTC),
                "roleArn": "arn:aws:iam::123:role/test-role",
                "networkConfiguration": {"networkMode": "PUBLIC"},
                "lifecycleConfiguration": {"idleRuntimeSessionTimeout": 900, "maxLifetime": 3600},
                "protocolConfiguration": {"serverProtocol": "A2A"},
            },
            {"agentRuntimeId": "r2", "agentRuntimeVersion": "2"},
        )

        monkeypatch.setattr(client, "_get_control_client", _async_return(boto_client))
        monkeypatch.setattr(client, "_reconcile_runtime_type", _async_return(None))
        monkeypatch.setattr(
            client,
            "_transform_runtime_to_a2a_agent",
            lambda runtime_detail, _region, _author_id=None: SimpleNamespace(
                federationId=runtime_detail["agentRuntimeArn"]
            ),
        )

        with stubber:
            result = await client.discover_runtime_entities(runtime_arns=[target_arn], enrich_protocol_payloads=False)

        assert len(result["a2a_agents"]) == 1
        assert result["a2a_agents"][0].federationId == target_arn

    async def test_build_runtime_mcp_url_uses_invocations_with_qualifier(self):
        client = AgentCoreFederationClient(region="us-east-1")
        runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123:runtime/r1"
        mcp_url = f"{client._build_runtime_invocation_url(runtime_arn, 'us-east-1')}?qualifier=DEFAULT"
        assert mcp_url.endswith("/invocations?qualifier=DEFAULT")
        assert "/mcp/" not in mcp_url

    async def test_transform_runtime_to_mcp_server_fails_loudly_on_missing_required_fields(self):
        client = AgentCoreFederationClient(region="us-east-1")
        runtime_detail = {
            "agentRuntimeId": "r1",
            "agentRuntimeName": "runtime-mcp",
            "agentRuntimeVersion": "1",
            "status": "READY",
            "protocolConfiguration": {"serverProtocol": "MCP"},
        }

        with pytest.raises(KeyError):
            client._transform_runtime_to_mcp_server(runtime_detail, "us-east-1")


def _async_return(value):
    async def _inner(*_args, **_kwargs):
        return value

    return _inner
