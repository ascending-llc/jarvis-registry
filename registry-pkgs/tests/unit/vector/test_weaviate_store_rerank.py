from unittest.mock import MagicMock, patch

from pydantic import SecretStr

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
    # Pre-seed the cached Bedrock client so _get_bedrock_client() returns it
    # without constructing a real boto3 client.
    store._bedrock_client = MagicMock(name="bedrock_client")
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
        client=store._bedrock_client,
        region="us-east-1",
        model_id="cohere.rerank-v3-5:0",
        access_key_id="AKIA",
        secret_access_key="secret",
        session_token=None,
        top_n=3,
    )


def test_search_with_rerank_returns_reranked_candidates():
    """Happy path: candidates are fetched once then reranked, returning top k."""
    store = _make_store(enabled=True)
    candidates = [MagicMock(name=f"cand{i}") for i in range(9)]
    store.search = MagicMock(return_value=candidates)

    reranker = MagicMock()
    reranker.compress_documents.return_value = candidates[:3]

    with patch("registry_pkgs.vector.backends.weaviate_store.create_reranker", return_value=reranker):
        result = store.search_with_rerank(query="q", k=3)

    assert result == candidates[:3]
    store.search.assert_called_once()
    assert store.search.call_args.kwargs["k"] == 9
    reranker.compress_documents.assert_called_once_with(documents=candidates, query="q")


def test_search_with_rerank_falls_back_to_candidates_on_failure():
    """When reranking raises, return candidates[:k] without a second search."""
    store = _make_store(enabled=True)
    candidates = [MagicMock(name=f"cand{i}") for i in range(9)]
    store.search = MagicMock(return_value=candidates)

    reranker = MagicMock()
    reranker.compress_documents.side_effect = RuntimeError("bedrock rerank exploded")

    with patch("registry_pkgs.vector.backends.weaviate_store.create_reranker", return_value=reranker):
        result = store.search_with_rerank(query="q", k=3)

    assert result == candidates[:3]
    store.search.assert_called_once()


def test_search_with_rerank_returns_empty_when_no_candidates():
    """No candidates means an empty result without invoking the reranker."""
    store = _make_store(enabled=True)
    store.search = MagicMock(return_value=[])

    with patch("registry_pkgs.vector.backends.weaviate_store.create_reranker") as mock_create:
        result = store.search_with_rerank(query="q", k=3)

    assert result == []
    mock_create.assert_not_called()


def test_get_bedrock_client_is_cached():
    """The Bedrock client is built once and reused across calls (singleton store)."""
    store = _make_store(enabled=True)
    store._bedrock_client = None  # reset the pre-seeded sentinel

    fake_client = MagicMock(name="bedrock_client")
    with patch(
        "registry_pkgs.vector.backends.weaviate_store.create_aws_client", return_value=fake_client
    ) as mock_create:
        first = store._get_bedrock_client()
        second = store._get_bedrock_client()

    assert first is fake_client
    assert second is fake_client
    mock_create.assert_called_once_with(
        service_name="bedrock-agent-runtime",
        region_name="us-east-1",
        aws_access_key_id=SecretStr("AKIA"),
        aws_secret_access_key=SecretStr("secret"),
    )


def test_get_bedrock_client_omits_creds_when_absent():
    """Without static creds, no credential kwargs are passed (default chain / IRSA)."""
    store = _make_store(enabled=True)
    store._bedrock_client = None
    store.rerank_config = {"enabled": True, "region": "us-west-2"}

    fake_client = MagicMock(name="bedrock_client")
    with patch(
        "registry_pkgs.vector.backends.weaviate_store.create_aws_client", return_value=fake_client
    ) as mock_create:
        store._get_bedrock_client()

    mock_create.assert_called_once_with(
        service_name="bedrock-agent-runtime",
        region_name="us-west-2",
    )
