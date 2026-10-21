"""``alist_entity_ids`` collects the distinct ``ENTITY_ID_PROPERTY`` values via the adapter cursor.

Both repositories declare the property (``server_id`` / ``agent_id``) so the orphan sweep can
enumerate what each collection stores.
"""

from types import SimpleNamespace

import pytest

from registry_pkgs.vector.repositories.a2a_agent_repository import A2AAgentRepository
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository


def _repo(repo_cls, values):
    captured = {}

    def iter_property_values(property_name, collection_name):
        captured["property_name"] = property_name
        captured["collection_name"] = collection_name
        return iter(values)

    adapter = SimpleNamespace(iter_property_values=iter_property_values)
    db_client = SimpleNamespace(adapter=adapter, collection_name_for=lambda base: f"{base}__gen")
    return repo_cls(db_client), captured


@pytest.mark.asyncio
async def test_server_repo_lists_distinct_server_ids():
    repo, captured = _repo(MCPServerRepository, ["a", "b", "a", "c", "b"])

    assert await repo.alist_entity_ids() == {"a", "b", "c"}
    assert captured["property_name"] == "server_id"
    assert captured["collection_name"].endswith("__gen")


@pytest.mark.asyncio
async def test_agent_repo_lists_distinct_agent_ids():
    repo, captured = _repo(A2AAgentRepository, ["x", "x", "y"])

    assert await repo.alist_entity_ids() == {"x", "y"}
    assert captured["property_name"] == "agent_id"
