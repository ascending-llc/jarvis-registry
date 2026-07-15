from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from azure.ai.projects.models import AgentEndpointProtocol
from beanie import PydanticObjectId

from registry.services.federation.azure_foundry_auth import AzureFoundryAuthService
from registry.services.federation.azure_foundry_discovery import AzureFoundryDiscoveryClient
from registry_pkgs.models import A2AAgent
from registry_pkgs.models.a2a_agent import AgentConfig, WellKnownConfig
from registry_pkgs.models.federation import AzureAiFoundryProviderConfig

PROJECT_ENDPOINT = "https://acc.services.ai.azure.com/api/projects/p"
AUTHOR_ID = PydanticObjectId()


def _provider_config(**overrides) -> AzureAiFoundryProviderConfig:
    defaults = {
        "projectEndpoint": PROJECT_ENDPOINT,
        "tenantId": "tenant",
        "clientId": "client",
        "clientSecret": "plain-secret",
    }
    defaults.update(overrides)
    return AzureAiFoundryProviderConfig(**defaults)


def _agent_summary(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def _agent_detail(
    name: str,
    *,
    a2a: bool = True,
    version: str = "3",
    status: str = "active",
    description: str = "",
    skills: list[dict] | None = None,
    metadata: dict | None = None,
) -> SimpleNamespace:
    # Use real AgentEndpointProtocol enum members (as the SDK returns), not raw
    # strings — str(member) is "AgentEndpointProtocol.A2A", so this guards the
    # value-based detection in _is_a2a_enabled.
    protocols = (
        [AgentEndpointProtocol.RESPONSES, AgentEndpointProtocol.A2A] if a2a else [AgentEndpointProtocol.RESPONSES]
    )
    endpoint = SimpleNamespace(
        protocols=protocols,
        authorization_schemes=[{"type": "Entra"}],
    )
    latest = {
        "id": f"{name}:{version}",
        "version": version,
        "description": description,
        "status": status,
        "agent_guid": "guid-" + name,
        "created_at": 1,
        "metadata": dict(metadata or {}),
    }
    versions = SimpleNamespace(latest=latest)
    # Azure's embedded agent_card skills omit required A2A fields like `tags`;
    # default to that shape so transform must tolerate it.
    card_skills = skills if skills is not None else [{"id": "qa", "name": "QA", "description": "x"}]
    card = {"version": "1.0", "description": description or f"desc {name}", "skills": card_skills}
    return SimpleNamespace(
        name=name,
        versions=versions,
        agent_endpoint=endpoint,
        agent_card=card,
    )


class _FakeAgents:
    def __init__(self, summaries: list, details_map: dict, list_error: Exception | None = None):
        self._summaries = summaries
        self._details_map = details_map
        self._list_error = list_error

    def list(self):
        if self._list_error is not None:
            raise self._list_error

        async def _iter():
            for item in self._summaries:
                yield item

        return _iter()

    async def get(self, name: str):
        return self._details_map[name]


class _FakeProjectClient:
    def __init__(self, agents: _FakeAgents):
        self.agents = agents

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _patch_project(agents: _FakeAgents):
    return patch(
        "registry.services.federation.azure_foundry_discovery.AIProjectClient",
        return_value=_FakeProjectClient(agents),
    )


def _patch_httpx():
    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    return patch(
        "registry.services.federation.azure_foundry_discovery.httpx.AsyncClient",
        return_value=fake_client,
    )


def _make_auth_stub() -> AzureFoundryAuthService:
    auth = AzureFoundryAuthService(_provider_config())
    auth.credential = MagicMock(return_value=MagicMock())
    auth.build_headers = AsyncMock(return_value={"Authorization": "Bearer tok"})
    return auth


def _make_fake_agent_card(card_data: dict, registry_fields: dict) -> SimpleNamespace:
    """Stand-in for A2AAgent that bypasses Beanie Document machinery."""
    card_attrs = dict(card_data)
    # Convert preferredTransport (alias) into preferred_transport so discovery
    # post-enrichment behaviour can read it from the fake card.
    preferred = card_attrs.get("preferredTransport") or card_attrs.get("preferred_transport") or "JSONRPC"
    card = SimpleNamespace(
        name=card_attrs.get("name"),
        description=card_attrs.get("description"),
        version=card_attrs.get("version", "0"),
        preferred_transport=preferred,
        model_dump=lambda **_: dict(card_attrs),
    )

    well_known_field = registry_fields.get("wellKnown")
    if isinstance(well_known_field, WellKnownConfig):
        well_known = well_known_field
    elif isinstance(well_known_field, dict):
        well_known = WellKnownConfig(**well_known_field)
    else:
        well_known = None

    config_field = registry_fields.get("config")
    config = config_field if isinstance(config_field, AgentConfig) else None

    fake = SimpleNamespace(
        path=registry_fields.get("path") or "/" + (card_attrs.get("name") or "x"),
        card=card,
        config=config,
        author=registry_fields.get("author"),
        tags=registry_fields.get("tags", []),
        registeredBy=registry_fields.get("registeredBy"),
        registeredAt=registry_fields.get("registeredAt"),
        wellKnown=well_known,
        federationRefId=registry_fields.get("federationRefId"),
        federationMetadata=dict(registry_fields.get("federationMetadata") or {}),
    )
    return fake


@pytest.fixture
def fake_a2a_agent_factory(monkeypatch):
    def _from_a2a_agent_card(*, card_data, path, **registry_fields):
        return _make_fake_agent_card(card_data, {**registry_fields, "path": path})

    monkeypatch.setattr(A2AAgent, "from_a2a_agent_card", staticmethod(_from_a2a_agent_card))
    yield


def _fake_card_payload(name: str, *, transport: str = "JSONRPC") -> SimpleNamespace:
    payload = {
        "name": name,
        "description": "remote",
        "url": "ignored-url",
        "version": "1.0",
        "protocolVersion": "0.3",
        "preferredTransport": transport,
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "skills": [],
    }
    return SimpleNamespace(model_dump=lambda **_: dict(payload))


@pytest.mark.asyncio
async def test_discover_filters_to_a2a_enabled_agents_only(fake_a2a_agent_factory):
    agents_fake = _FakeAgents(
        summaries=[_agent_summary("with-a2a"), _agent_summary("without-a2a")],
        details_map={
            "with-a2a": _agent_detail("with-a2a", a2a=True, version="3"),
            "without-a2a": _agent_detail("without-a2a", a2a=False),
        },
    )

    resolver = MagicMock()
    resolver.get_agent_card = AsyncMock(return_value=_fake_card_payload("with-a2a"))

    with (
        _patch_project(agents_fake),
        _patch_httpx(),
        patch(
            "registry.services.federation.azure_foundry_discovery.A2ACardResolver",
            return_value=resolver,
        ),
    ):
        client = AzureFoundryDiscoveryClient()
        result = await client.discover_a2a_agents(
            provider_config=_provider_config(),
            auth=_make_auth_stub(),
            author_id=AUTHOR_ID,
        )

    assert len(result) == 1
    agent = result[0]
    assert agent.federationMetadata["agentName"] == "with-a2a"
    assert agent.federationMetadata["runtimeArn"] == "with-a2a"  # D2 mirror
    assert agent.federationMetadata["agentVersion"] == "3"
    assert agent.wellKnown.lastSyncStatus == "success"
    assert agent.path == "/with-a2a"
    assert "enrichmentError" not in agent.federationMetadata


@pytest.mark.asyncio
async def test_discover_marks_enrichment_failure_when_card_fetch_fails(fake_a2a_agent_factory):
    agents_fake = _FakeAgents(
        summaries=[_agent_summary("a")],
        details_map={"a": _agent_detail("a", a2a=True)},
    )

    resolver = MagicMock()
    resolver.get_agent_card = AsyncMock(side_effect=RuntimeError("boom"))

    with (
        _patch_project(agents_fake),
        _patch_httpx(),
        patch(
            "registry.services.federation.azure_foundry_discovery.A2ACardResolver",
            return_value=resolver,
        ),
    ):
        client = AzureFoundryDiscoveryClient()
        result = await client.discover_a2a_agents(
            provider_config=_provider_config(),
            auth=_make_auth_stub(),
            author_id=AUTHOR_ID,
        )

    assert len(result) == 1
    agent = result[0]
    assert agent.federationMetadata["enrichmentError"].startswith("a2a enrichment failed")
    assert agent.wellKnown.lastSyncStatus == "failed"


@pytest.mark.asyncio
async def test_discover_raises_with_recognised_prefix_when_list_fails():
    agents_fake = _FakeAgents(
        summaries=[],
        details_map={},
        list_error=RuntimeError("forbidden"),
    )

    with _patch_project(agents_fake):
        client = AzureFoundryDiscoveryClient()
        with pytest.raises(RuntimeError, match="Failed to list Azure AI Foundry agents"):
            await client.discover_a2a_agents(
                provider_config=_provider_config(),
                auth=_make_auth_stub(),
                author_id=AUTHOR_ID,
            )


@pytest.mark.asyncio
async def test_agent_names_bypass_list_call(fake_a2a_agent_factory):
    agents_fake = _FakeAgents(
        summaries=[],  # list() would yield nothing
        details_map={"explicit": _agent_detail("explicit", a2a=True)},
    )

    resolver = MagicMock()
    resolver.get_agent_card = AsyncMock(return_value=_fake_card_payload("explicit"))

    with (
        _patch_project(agents_fake),
        _patch_httpx(),
        patch(
            "registry.services.federation.azure_foundry_discovery.A2ACardResolver",
            return_value=resolver,
        ),
    ):
        client = AzureFoundryDiscoveryClient()
        result = await client.discover_a2a_agents(
            provider_config=_provider_config(agentNames=["explicit"]),
            auth=_make_auth_stub(),
            author_id=AUTHOR_ID,
        )

    assert [a.federationMetadata["agentName"] for a in result] == ["explicit"]


@pytest.mark.asyncio
async def test_discover_applies_metadata_filter(fake_a2a_agent_factory):
    agents_fake = _FakeAgents(
        summaries=[_agent_summary("keep"), _agent_summary("drop")],
        details_map={
            "keep": _agent_detail("keep", a2a=True, metadata={"env": "prod"}),
            "drop": _agent_detail("drop", a2a=True, metadata={"env": "dev"}),
        },
    )

    resolver = MagicMock()
    resolver.get_agent_card = AsyncMock(return_value=_fake_card_payload("keep"))

    with (
        _patch_project(agents_fake),
        _patch_httpx(),
        patch(
            "registry.services.federation.azure_foundry_discovery.A2ACardResolver",
            return_value=resolver,
        ),
    ):
        client = AzureFoundryDiscoveryClient()
        result = await client.discover_a2a_agents(
            provider_config=_provider_config(metadataFilter={"env": "prod"}),
            auth=_make_auth_stub(),
            author_id=AUTHOR_ID,
        )

    assert [a.federationMetadata["agentName"] for a in result] == ["keep"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "version_status, expected_enabled",
    [
        ("active", True),
        ("inactive", False),
        ("draft", False),
    ],
)
async def test_config_enabled_reflects_foundry_version_status(fake_a2a_agent_factory, version_status, expected_enabled):
    agents_fake = _FakeAgents(
        summaries=[_agent_summary("agent")],
        details_map={"agent": _agent_detail("agent", a2a=True, status=version_status)},
    )

    resolver = MagicMock()
    resolver.get_agent_card = AsyncMock(return_value=_fake_card_payload("agent"))

    with (
        _patch_project(agents_fake),
        _patch_httpx(),
        patch(
            "registry.services.federation.azure_foundry_discovery.A2ACardResolver",
            return_value=resolver,
        ),
    ):
        client = AzureFoundryDiscoveryClient()
        result = await client.discover_a2a_agents(
            provider_config=_provider_config(),
            auth=_make_auth_stub(),
            author_id=AUTHOR_ID,
        )

    assert len(result) == 1
    assert result[0].config.enabled is expected_enabled
