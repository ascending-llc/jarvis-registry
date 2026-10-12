from functools import cached_property
from typing import Self

from pydantic import Field, model_validator

from registry_pkgs.core.config import JarvisBaseSettings, JwtSigningConfig, MongoConfig, RedisConfig

# The shutdown drain + finalize budget must stay under
# this so the pod finalizes its runs before SIGKILL.
_TERMINATION_GRACE_PERIOD_SECONDS = 60.0
# Reserved inside the grace period for SIGTERM propagation and the post-loop resource teardown
# (http/azure/redis/mongo close in main()), which run after the drain and are not themselves bounded.
_SHUTDOWN_HEADROOM_SECONDS = 10.0


class WorkerSettings(JarvisBaseSettings):
    """Standalone worker settings."""

    max_sleep_seconds: float = Field(default=30.0, validation_alias="WORKFLOW_WORKER_MAX_SLEEP_SECONDS", gt=0)
    max_concurrent_runs: int = Field(default=5, validation_alias="WORKFLOW_WORKER_MAX_CONCURRENT_RUNS", ge=1)
    max_claimed_runs: int = Field(default=10, validation_alias="WORKFLOW_WORKER_MAX_CLAIMED_RUNS", ge=1)
    lease_duration_seconds: int = Field(default=300, validation_alias="WORKFLOW_WORKER_LEASE_DURATION_SECONDS", ge=30)
    shutdown_drain_seconds: float = Field(default=30.0, validation_alias="WORKFLOW_WORKER_SHUTDOWN_DRAIN_SECONDS", gt=0)
    shutdown_finalize_seconds: float = Field(
        default=10.0, validation_alias="WORKFLOW_WORKER_SHUTDOWN_FINALIZE_SECONDS", gt=0
    )

    redis_uri: str = "redis://127.0.0.1:6379/1"
    redis_key_prefix: str = "jarvis-registry"

    workflow_llm_model_id: str = Field(default="amazon.nova-2-lite-v1:0", validation_alias="AWS_WORKFLOW_LLM_MODEL")
    aws_region: str = "us-east-1"
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None

    @model_validator(mode="after")
    def _validate_claim_capacity(self) -> Self:
        if self.max_claimed_runs < self.max_concurrent_runs:
            raise ValueError("WORKFLOW_WORKER_MAX_CLAIMED_RUNS must be greater than or equal to MAX_CONCURRENT_RUNS")
        return self

    @model_validator(mode="after")
    def _validate_shutdown_budget(self) -> Self:
        # The drain + finalize budget must fit inside K8s terminationGracePeriodSeconds (60s), leaving headroom for SIGTERM propagation and post-loop resource teardown, or the
        # pod is SIGKILLed mid-finalize and leaves orphaned runs.
        budget_ceiling = _TERMINATION_GRACE_PERIOD_SECONDS - _SHUTDOWN_HEADROOM_SECONDS
        if self.shutdown_drain_seconds + self.shutdown_finalize_seconds > budget_ceiling:
            raise ValueError(
                "WORKFLOW_WORKER_SHUTDOWN_DRAIN_SECONDS + WORKFLOW_WORKER_SHUTDOWN_FINALIZE_SECONDS must be "
                f"at most {budget_ceiling}s: the pod terminationGracePeriodSeconds is "
                f"{_TERMINATION_GRACE_PERIOD_SECONDS}s (AS-1911), with {_SHUTDOWN_HEADROOM_SECONDS}s reserved "
                "for SIGTERM propagation and resource teardown"
            )
        return self

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
