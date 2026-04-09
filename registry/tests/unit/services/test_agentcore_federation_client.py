from datetime import UTC, datetime
from types import SimpleNamespace

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from registry.services.federation.agentcore_discovery import AgentCoreFederationClient


@pytest.mark.unit
@pytest.mark.asyncio
class TestAgentCoreFederationClient:
    async def test_discover_runtime_entities_classifies_mcp_and_a2a_with_stubber(self, monkeypatch):
        client = AgentCoreFederationClient()

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

        monkeypatch.setattr(client.client_provider, "get_control_client", _async_return(boto_client))
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
            result = await client.discover_runtime_entities(region="us-east-1")

        assert len(result["mcp_servers"]) == 1
        assert len(result["a2a_agents"]) == 1
        assert len(result["skipped_runtimes"]) == 1
        assert result["mcp_servers"][0].federationMetadata["runtimeVersion"] == "1"
        assert result["a2a_agents"][0].federationMetadata["runtimeVersion"] == "2"

    async def test_discover_runtime_entities_filters_by_runtime_arns_with_stubber(self, monkeypatch):
        client = AgentCoreFederationClient()

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

        monkeypatch.setattr(client.client_provider, "get_control_client", _async_return(boto_client))
        monkeypatch.setattr(client, "_reconcile_runtime_type", _async_return(None))
        monkeypatch.setattr(
            client,
            "_transform_runtime_to_a2a_agent",
            lambda runtime_detail, _region, _author_id=None: SimpleNamespace(
                federationMetadata={"runtimeArn": runtime_detail["runtimeArn"]}
            ),
        )

        with stubber:
            result = await client.discover_runtime_entities(
                runtime_arns=[target_arn],
                region="us-east-1",
            )

        assert len(result["a2a_agents"]) == 1
        assert result["a2a_agents"][0].federationMetadata["runtimeArn"] == target_arn

    async def test_discover_runtime_entities_applies_resource_tags_filter(self, monkeypatch):
        client = AgentCoreFederationClient()

        boto_client = boto3.client(
            "bedrock-agentcore-control",
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        stubber = Stubber(boto_client)
        runtime_one_arn = "arn:aws:bedrock-agentcore:us-east-1:123:runtime/r1"
        runtime_two_arn = "arn:aws:bedrock-agentcore:us-east-1:123:runtime/r2"

        stubber.add_response(
            "list_agent_runtimes",
            {
                "agentRuntimes": [
                    {
                        "agentRuntimeArn": runtime_one_arn,
                        "agentRuntimeId": "r1",
                        "agentRuntimeVersion": "1",
                        "agentRuntimeName": "runtime-prod",
                        "description": "mcp runtime",
                        "lastUpdatedAt": datetime.now(UTC),
                        "status": "READY",
                    },
                    {
                        "agentRuntimeArn": runtime_two_arn,
                        "agentRuntimeId": "r2",
                        "agentRuntimeVersion": "2",
                        "agentRuntimeName": "runtime-dev",
                        "description": "a2a runtime",
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
                "agentRuntimeArn": runtime_one_arn,
                "agentRuntimeId": "r1",
                "agentRuntimeName": "runtime-prod",
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
                "agentRuntimeArn": runtime_two_arn,
                "agentRuntimeId": "r2",
                "agentRuntimeName": "runtime-dev",
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
            "list_tags_for_resource",
            {"tags": {"env": "production", "team": "platform"}},
            {"resourceArn": runtime_one_arn},
        )
        stubber.add_response(
            "list_tags_for_resource",
            {"tags": {"env": "development", "team": "platform"}},
            {"resourceArn": runtime_two_arn},
        )

        monkeypatch.setattr(client.client_provider, "get_control_client", _async_return(boto_client))
        monkeypatch.setattr(client, "_reconcile_runtime_type", _async_return(None))
        monkeypatch.setattr(
            client,
            "_transform_runtime_to_mcp_server",
            lambda runtime_detail, _region, _author_id=None: SimpleNamespace(
                federationMetadata={
                    "runtimeArn": runtime_detail["runtimeArn"],
                    "runtimeTags": runtime_detail["tags"],
                }
            ),
        )
        monkeypatch.setattr(
            client,
            "_transform_runtime_to_a2a_agent",
            lambda runtime_detail, _region, _author_id=None: SimpleNamespace(
                federationMetadata={
                    "runtimeArn": runtime_detail["runtimeArn"],
                    "runtimeTags": runtime_detail["tags"],
                }
            ),
        )

        with stubber:
            result = await client.discover_runtime_entities(
                region="us-east-1",
                resource_tags_filter={"env": "production", "team": "platform"},
            )

        assert len(result["mcp_servers"]) == 1
        assert result["mcp_servers"][0].federationMetadata["runtimeArn"] == runtime_one_arn
        assert result["mcp_servers"][0].federationMetadata["runtimeTags"] == {
            "env": "production",
            "team": "platform",
        }
        assert len(result["a2a_agents"]) == 0
        assert len(result["skipped_runtimes"]) == 1
        assert result["skipped_runtimes"][0]["runtimeArn"] == runtime_two_arn
        assert result["skipped_runtimes"][0]["reason"] == "tag_filter_mismatch"
        assert result["skipped_runtimes"][0]["requiredTags"] == {"env": "production", "team": "platform"}
        assert result["skipped_runtimes"][0]["actualTags"] == {
            "env": "development",
            "team": "platform",
        }

    async def test_build_runtime_mcp_url_uses_invocations_with_qualifier(self):
        client = AgentCoreFederationClient()
        runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123:runtime/r1"
        mcp_url = f"{client._build_runtime_invocation_url(runtime_arn, 'us-east-1')}?qualifier=DEFAULT"
        assert mcp_url.endswith("/invocations?qualifier=DEFAULT")
        assert "/mcp/" not in mcp_url

    async def test_discover_runtime_entities_raises_when_runtime_listing_fails(self, monkeypatch):
        client = AgentCoreFederationClient()

        async def _execute_with_control_client(_region, operation, _assume_role_arn=None):
            return operation(object())

        def _list_runtime_summaries(_client):
            raise RuntimeError("Token has expired and refresh failed")

        monkeypatch.setattr(client.client_provider, "execute_with_control_client", _execute_with_control_client)
        monkeypatch.setattr(client, "_list_runtime_summaries", _list_runtime_summaries)

        with pytest.raises(RuntimeError, match="Failed to list AgentCore runtimes in us-east-1"):
            await client.discover_runtime_entities(region="us-east-1")

    async def test_discover_runtime_entities_refreshes_cached_client_after_expired_token(self, monkeypatch):
        client = AgentCoreFederationClient()
        stale_client = object()
        refreshed_client = object()
        execute_calls: list[tuple[str, str | None]] = []

        responses = [
            ClientError(
                error_response={
                    "Error": {
                        "Code": "ExpiredTokenException",
                        "Message": "The security token included in the request is expired",
                    }
                },
                operation_name="ListAgentRuntimes",
            ),
            [
                {
                    "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:123:runtime/r1",
                    "agentRuntimeId": "r1",
                    "agentRuntimeVersion": "1",
                    "agentRuntimeName": "runtime-mcp",
                }
            ],
        ]

        def _list_runtime_summaries(control_client):
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            assert control_client is refreshed_client
            return result

        async def _execute_with_control_client(region, operation, assume_role_arn=None):
            execute_calls.append((region, assume_role_arn))
            if len(execute_calls) == 1:
                with pytest.raises(ClientError):
                    operation(stale_client)
                return operation(refreshed_client)
            return operation(refreshed_client)

        monkeypatch.setattr(client.client_provider, "execute_with_control_client", _execute_with_control_client)
        monkeypatch.setattr(client, "_list_runtime_summaries", _list_runtime_summaries)
        monkeypatch.setattr(client, "_get_runtime_details", lambda *_args, **_kwargs: [])

        result = await client.discover_runtime_entities(
            region="us-east-1",
            assume_role_arn="arn:aws:iam::123456789012:role/TestRole",
        )

        assert result == {"a2a_agents": [], "mcp_servers": [], "skipped_runtimes": []}
        assert execute_calls == [("us-east-1", "arn:aws:iam::123456789012:role/TestRole")]


def _async_return(value):
    async def _inner(*_args, **_kwargs):
        return value

    return _inner
