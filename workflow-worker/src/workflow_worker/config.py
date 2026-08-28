from functools import cached_property
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from registry_pkgs.core.config import JwtSigningConfig, MongoConfig, RedisConfig


class WorkerSettings(BaseSettings):
    """Standalone worker settings loaded from the shared deployment environment."""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    max_sleep_seconds: float = Field(default=30.0, validation_alias="WORKFLOW_WORKER_MAX_SLEEP_SECONDS", gt=0)
    max_concurrent_runs: int = Field(default=5, validation_alias="WORKFLOW_WORKER_MAX_CONCURRENT_RUNS", ge=1)
    max_claimed_runs: int = Field(default=10, validation_alias="WORKFLOW_WORKER_MAX_CLAIMED_RUNS", ge=1)
    lease_duration_seconds: int = Field(default=300, validation_alias="WORKFLOW_WORKER_LEASE_DURATION_SECONDS", ge=30)

    mongo_uri: str = "mongodb://127.0.0.1:27017/jarvis"
    mongodb_username: str = ""
    mongodb_password: str = ""
    redis_uri: str = "redis://127.0.0.1:6379/1"
    redis_key_prefix: str = "jarvis-registry"

    workflow_llm_model_id: str = Field(
        default="amazon.nova-2-lite-v1:0",
        validation_alias="AWS_WORKFLOW_LLM_MODEL",
    )
    aws_region: str = "us-east-1"
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None

    jwt_private_key: str = ""
    jwt_issuer: str = "http://localhost:7860"
    jwt_self_signed_kid: str = "self-signed-key-v1"
    jwt_audience: str = "jarvis-services"
    registry_app_name: str = "jarvis-registry-client"

    creds_key: str = ""

    @model_validator(mode="after")
    def _validate_claim_capacity(self) -> Self:
        if self.max_claimed_runs < self.max_concurrent_runs:
            raise ValueError("WORKFLOW_WORKER_MAX_CLAIMED_RUNS must be greater than or equal to MAX_CONCURRENT_RUNS")
        return self

    @model_validator(mode="after")
    def _validate_creds_key(self) -> Self:
        if self.creds_key == "":
            raise ValueError("CREDS_KEY must be set for encryption/decryption.")
        try:
            bytes.fromhex(self.creds_key)
        except ValueError as exc:
            raise ValueError("CREDS_KEY must be a valid hex string.") from exc
        return self

    @cached_property
    def encryption_key(self) -> bytes:
        return bytes.fromhex(self.creds_key)

    @cached_property
    def mongo_config(self) -> MongoConfig:
        return MongoConfig(
            mongo_uri=self.mongo_uri,
            mongodb_username=self.mongodb_username,
            mongodb_password=self.mongodb_password,
        )

    @cached_property
    def redis_config(self) -> RedisConfig:
        return RedisConfig(redis_uri=self.redis_uri, redis_key_prefix=self.redis_key_prefix)

    @cached_property
    def jwt_signing_config(self) -> JwtSigningConfig:
        return JwtSigningConfig(
            jwt_private_key=self.jwt_private_key,
            jwt_issuer=self.jwt_issuer,
            jwt_self_signed_kid=self.jwt_self_signed_kid,
            jwt_audience=self.jwt_audience,
            registry_app_name=self.registry_app_name,
        )


settings = WorkerSettings()
