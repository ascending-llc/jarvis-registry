from registry_pkgs.core.config import VectorConfig
from registry_pkgs.vector.config import BackendConfig, RerankConfig


def test_rerank_config_defaults_from_vector_config():
    """Defaults flow from VectorConfig into RerankConfig."""
    rerank = RerankConfig.from_vector_config(VectorConfig())
    assert rerank.enabled is True
    assert rerank.model_id == "cohere.rerank-v3-5:0"
    assert rerank.region == "us-east-1"


def test_rerank_config_sources_aws_credentials_from_top_level_fields():
    """AWS region/creds come from VectorConfig top-level aws_* fields, not embeddings."""
    cfg = VectorConfig(
        aws_region="us-west-2",
        aws_access_key_id="AKIA",
        aws_secret_access_key="secret",
        aws_session_token="token",
    )
    rerank = RerankConfig.from_vector_config(cfg)
    assert rerank.region == "us-west-2"
    assert rerank.access_key_id == "AKIA"
    assert rerank.secret_access_key == "secret"
    assert rerank.session_token == "token"


def test_rerank_config_respects_overrides():
    """Explicit overrides are carried through."""
    cfg = VectorConfig(rerank_enabled=False, rerank_model_id="cohere.rerank-v3-5:0")
    rerank = RerankConfig.from_vector_config(cfg)
    assert rerank.enabled is False
    assert rerank.model_id == "cohere.rerank-v3-5:0"


def test_rerank_config_blank_model_id_falls_back_to_default():
    """A blank model id falls back to the Cohere default."""
    rerank = RerankConfig.from_vector_config(VectorConfig(rerank_model_id="   "))
    assert rerank.model_id == "cohere.rerank-v3-5:0"


def test_backend_config_includes_rerank_dict():
    """BackendConfig assembles rerank config and exposes it as a dict."""
    backend = BackendConfig.from_vector_config(VectorConfig())
    assert backend.get_rerank_config_dict() == {
        "enabled": True,
        "model_id": "cohere.rerank-v3-5:0",
        "region": "us-east-1",
        "access_key_id": None,
        "secret_access_key": None,
        "session_token": None,
    }
