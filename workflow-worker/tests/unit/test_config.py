import pytest
from pydantic import ValidationError

from workflow_worker.config import WorkerSettings


def test_worker_settings_build_shared_client_configs() -> None:
    settings = WorkerSettings(
        WORKFLOW_WORKER_MAX_SLEEP_SECONDS=45,
        WORKFLOW_WORKER_MAX_CLAIMED_RUNS=12,
        mongo_uri="mongodb://mongo:27017/jarvis",
        mongodb_username="worker",
        mongodb_password="password",
        redis_uri="redis://redis:6379/1",
        redis_key_prefix="worker-prefix",
        jwt_private_key="test-private-key",
        # jwt_issuer is computed from auth_server_external_url (inherited), not set directly.
        auth_server_external_url="https://issuer.example.com",
        jwt_self_signed_kid="test-kid",
        jwt_audience="test-audience",
        registry_app_name="test-worker",
        creds_key="00" * 16,
        _env_file=None,
    )

    assert settings.max_sleep_seconds == 45
    assert settings.max_claimed_runs == 12
    assert settings.mongo_config.mongo_uri == "mongodb://mongo:27017/jarvis"
    assert settings.mongo_config.mongodb_username == "worker"
    assert settings.redis_config.redis_uri == "redis://redis:6379/1"
    assert settings.redis_config.redis_key_prefix == "worker-prefix"
    # jwt_issuer derives from auth_server_external_url → canonical scheme://netloc, matching registry.
    assert settings.jwt_signing_config.jwt_issuer == "https://issuer.example.com"
    assert settings.jwt_signing_config.registry_app_name == "test-worker"
    assert settings.encryption_key == bytes.fromhex("00" * 16)


def test_worker_settings_reject_claim_capacity_below_execution_capacity() -> None:
    with pytest.raises(ValidationError, match="MAX_CLAIMED_RUNS must be greater than or equal"):
        WorkerSettings(
            WORKFLOW_WORKER_MAX_CONCURRENT_RUNS=5,
            WORKFLOW_WORKER_MAX_CLAIMED_RUNS=4,
            creds_key="00" * 16,
            _env_file=None,
        )
