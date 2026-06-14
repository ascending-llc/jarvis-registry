from unittest.mock import MagicMock, patch

from registry_pkgs.vector.backends.weaviate_store import WeaviateStore


def _make_store(*, enabled: bool) -> WeaviateStore:
    store = WeaviateStore(
        embedding=MagicMock(),
        config={"host": "localhost", "port": 8080},
        embedding_config={"region": "us-east-1"},
        rerank_config={
            "enabled": enabled,
            "model_id": "cohere.rerank-v3-5:0",
            "region": "us-east-1",
            "access_key_id": "AKIA",
            "secret_access_key": "secret",
            "session_token": None,
        },
    )
    # Avoid touching a real Weaviate client / filter normalization.
    store.search = MagicMock(return_value=[MagicMock()])
    store.normalize_filters = MagicMock(side_effect=lambda f: f)
    return store


def test_search_with_rerank_disabled_skips_reranker():
    """When rerank is disabled, fall straight through to plain search."""
    store = _make_store(enabled=False)

    with patch("registry_pkgs.vector.backends.weaviate_store.create_reranker") as mock_create:
        store.search_with_rerank(query="q", k=5)

    mock_create.assert_not_called()
    store.search.assert_called_once()
    assert store.search.call_args.kwargs["k"] == 5


def test_search_with_rerank_injects_bedrock_config():
    """Enabled path injects region/model_id/creds/top_n from store config into the reranker."""
    store = _make_store(enabled=True)
    reranker = MagicMock()
    reranker.compress_documents.return_value = ["doc"]

    with patch("registry_pkgs.vector.backends.weaviate_store.create_reranker", return_value=reranker) as mock_create:
        result = store.search_with_rerank(query="q", k=3)

    assert result == ["doc"]
    mock_create.assert_called_once_with(
        reranker_type="bedrock_cohere",
        region="us-east-1",
        model_id="cohere.rerank-v3-5:0",
        access_key_id="AKIA",
        secret_access_key="secret",
        session_token=None,
        top_n=3,
    )
