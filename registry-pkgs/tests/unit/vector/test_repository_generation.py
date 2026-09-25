"""Repository.collection resolves the client's current generation on every access."""

from unittest.mock import MagicMock

from registry_pkgs.vector.client import collection_name_for
from registry_pkgs.vector.repository import Repository


class _Model:
    COLLECTION_NAME = "MCP_Servers"

    @staticmethod
    def from_document(doc):  # required by the VectorStorable protocol check
        return doc


def _repo(generation):
    db = MagicMock()
    db.collection_name_for.side_effect = lambda base: collection_name_for(base, generation)
    return Repository(db, _Model)


def test_collection_is_base_name_for_generation_none() -> None:
    assert _repo(None).collection == "MCP_Servers"


def test_collection_is_suffixed_for_a_generation() -> None:
    assert _repo("65f0c0ffee").collection == "MCP_Servers_65f0c0ffee"


def test_collection_follows_a_swap_without_reconstruction() -> None:
    # One repository instance, whose client's generation changes underneath it (a swap_adapter).
    db = MagicMock()
    gen = {"g": None}
    db.collection_name_for.side_effect = lambda base: collection_name_for(base, gen["g"])
    repo = Repository(db, _Model)
    assert repo.collection == "MCP_Servers"
    gen["g"] = "gen2"
    assert repo.collection == "MCP_Servers_gen2"
