import os
from types import SimpleNamespace

import pytest
from beanie import PydanticObjectId

from registry.core.config import settings
from registry.services.federation.azure_ai_foundry_client import AzureAIFoundryFederationClient
from registry.services.federation.azure_ai_foundry_client_provider import AzureAIFoundryClientProvider


@pytest.mark.unit
@pytest.mark.asyncio
class TestAzureAIFoundryClientProvider:
    async def test_get_client_caches_by_endpoint(self, monkeypatch):
        provider = AzureAIFoundryClientProvider()
        created = []

        def fake_create_client(project_endpoint: str):
            created.append(project_endpoint)
            return {"endpoint": project_endpoint}

        monkeypatch.setattr(provider, "_create_client", fake_create_client)

        first = await provider.get_client("https://example.projects.ai.azure.com/")
        second = await provider.get_client("https://example.projects.ai.azure.com")

        assert first is second
        assert created == ["https://example.projects.ai.azure.com"]

    async def test_hydrate_credential_environment_from_settings_uses_settings_values(self, monkeypatch):
        provider = AzureAIFoundryClientProvider()

        monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
        monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)
        monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
        monkeypatch.setattr(settings, "azure_client_id", "settings-client-id")
        monkeypatch.setattr(settings, "azure_client_secret", "settings-client-secret")
        monkeypatch.setattr(settings, "azure_tenant_id", "settings-tenant-id")

        provider._hydrate_credential_environment_from_settings()

        assert os.environ["AZURE_CLIENT_ID"] == "settings-client-id"
        assert os.environ["AZURE_CLIENT_SECRET"] == "settings-client-secret"
        assert os.environ["AZURE_TENANT_ID"] == "settings-tenant-id"

    async def test_hydrate_credential_environment_from_settings_does_not_override_process_env(self, monkeypatch):
        provider = AzureAIFoundryClientProvider()

        monkeypatch.setenv("AZURE_CLIENT_ID", "process-client-id")
        monkeypatch.setenv("AZURE_CLIENT_SECRET", "process-client-secret")
        monkeypatch.setenv("AZURE_TENANT_ID", "process-tenant-id")
        monkeypatch.setattr(settings, "azure_client_id", "settings-client-id")
        monkeypatch.setattr(settings, "azure_client_secret", "settings-client-secret")
        monkeypatch.setattr(settings, "azure_tenant_id", "settings-tenant-id")

        provider._hydrate_credential_environment_from_settings()

        assert os.environ["AZURE_CLIENT_ID"] == "process-client-id"
        assert os.environ["AZURE_CLIENT_SECRET"] == "process-client-secret"
        assert os.environ["AZURE_TENANT_ID"] == "process-tenant-id"


@pytest.mark.unit
@pytest.mark.asyncio
class TestAzureAIFoundryFederationClient:
    async def test_discover_entities_maps_agents_to_a2a_agents(self):
        fake_agents = [
            SimpleNamespace(
                name="Customer Support",
                versions=SimpleNamespace(
                    latest=SimpleNamespace(
                        id="asst_abc123",
                        version="7",
                        metadata={"env": "prod", "team": "platform"},
                        description="Answers support questions",
                        created_at="2026-04-03T10:00:00Z",
                        definition=SimpleNamespace(
                            model="gpt-4.1",
                            tools=[
                                {
                                    "type": "function",
                                    "name": "lookup_ticket",
                                    "description": "Lookup a support ticket",
                                },
                                {
                                    "type": "code_interpreter",
                                    "name": "python",
                                },
                            ],
                        ),
                    )
                ),
            )
        ]
        fake_client = SimpleNamespace(agents=SimpleNamespace(list=lambda: fake_agents))
        fake_provider = SimpleNamespace(get_client=lambda _endpoint: None)

        async def fake_get_client(_endpoint: str):
            return fake_client

        fake_provider.get_client = fake_get_client
        client = AzureAIFoundryFederationClient(
            project_endpoint="https://example.projects.ai.azure.com",
            client_provider=fake_provider,
        )

        result = await client.discover_entities(author_id=PydanticObjectId())

        assert len(result["a2a_agents"]) == 1
        assert result["skipped_agents"] == []
        mapped = result["a2a_agents"][0]
        assert mapped.card.name == "Customer Support"
        assert mapped.card.description == "Answers support questions"
        assert mapped.card.url == "https://example.projects.ai.azure.com"
        assert mapped.path == "/azure-ai-foundry/a2a/customer-support"
        assert mapped.federationRefId is None
        assert mapped.federationMetadata["providerType"] == "azure_ai_foundry"
        assert mapped.federationMetadata["agentName"] == "Customer Support"
        assert mapped.federationMetadata["agentVersion"] == "7"
        assert mapped.federationMetadata["agentVersionId"] == "asst_abc123"
        assert mapped.federationMetadata["runtimeArn"] == "Customer Support"
        assert mapped.federationMetadata["runtimeVersion"] == "7"
        assert len(mapped.card.skills) == 1
        assert mapped.card.skills[0].name == "lookup_ticket"

    async def test_discover_entities_applies_metadata_filter(self):
        fake_agents = [
            SimpleNamespace(
                name="Prod Agent",
                versions=SimpleNamespace(
                    latest=SimpleNamespace(
                        id="asst_prod",
                        version="7",
                        metadata={"env": "prod"},
                        description="Production agent",
                        created_at="2026-04-03T10:00:00Z",
                        definition=SimpleNamespace(model="gpt-4.1", tools=[]),
                    )
                ),
            ),
            SimpleNamespace(
                name="Dev Agent",
                versions=SimpleNamespace(
                    latest=SimpleNamespace(
                        id="asst_dev",
                        version="4",
                        metadata={"env": "dev"},
                        description="Development agent",
                        created_at="2026-04-03T10:00:00Z",
                        definition=SimpleNamespace(model="gpt-4.1", tools=[]),
                    )
                ),
            ),
        ]
        fake_client = SimpleNamespace(agents=SimpleNamespace(list=lambda: fake_agents))

        async def fake_get_client(_endpoint: str):
            return fake_client

        fake_provider = SimpleNamespace(get_client=fake_get_client)
        client = AzureAIFoundryFederationClient(
            project_endpoint="https://example.projects.ai.azure.com",
            metadata_filter={"env": "prod"},
            client_provider=fake_provider,
        )

        result = await client.discover_entities(author_id=PydanticObjectId())

        assert [agent.card.name for agent in result["a2a_agents"]] == ["Prod Agent"]
        assert result["skipped_agents"] == []

    async def test_discover_entities_skips_agents_missing_required_identity(self):
        fake_agents = [SimpleNamespace(name="Broken Agent", versions=SimpleNamespace(latest=None))]
        fake_client = SimpleNamespace(agents=SimpleNamespace(list=lambda: fake_agents))

        async def fake_get_client(_endpoint: str):
            return fake_client

        fake_provider = SimpleNamespace(get_client=fake_get_client)
        client = AzureAIFoundryFederationClient(
            project_endpoint="https://example.projects.ai.azure.com",
            client_provider=fake_provider,
        )

        result = await client.discover_entities(author_id=PydanticObjectId())

        assert result["a2a_agents"] == []
        assert len(result["skipped_agents"]) == 1
        assert result["skipped_agents"][0]["reason"] == "missing_required_identity"
