"""Settings for the skill-scan worker. No Redis, no HTTP server."""

from functools import cached_property

from pydantic import Field

from registry_pkgs.core.config import JarvisBaseSettings, MongoConfig

_DEFAULT_MAX_OUTPUT_BYTES = 16 * 1024 * 1024  # 16 MiB


class SkillScanWorkerSettings(JarvisBaseSettings):
    """Standalone worker settings. ``encryption_key`` and ``telemetry_config`` come from the base class."""

    # Number of job runners. Tools within a job run sequentially, so this is also the cap on scanner
    # processes per pod (D13).
    concurrency: int = Field(default=2, validation_alias="SKILL_SCAN_WORKER_CONCURRENCY", ge=1)
    # Per-tool wall clock.
    tool_timeout_seconds: float = Field(default=600.0, validation_alias="SKILL_SCAN_WORKER_TOOL_TIMEOUT_SECONDS", gt=0)
    # Above this, adapters with llm_input_unbounded=True run without the LLM.
    llm_max_text_bytes: int = Field(default=524288, validation_alias="SKILL_SCAN_WORKER_LLM_MAX_TEXT_BYTES", ge=0)
    # Root for per-run temp dirs (an emptyDir,).
    work_dir: str = Field(default="/scan-tmp", validation_alias="SKILL_SCAN_WORKER_WORK_DIR")
    # Root of per-tool virtualenvs; adapters build argv from it.
    tools_dir: str = Field(default="/opt/scanners", validation_alias="SKILL_SCAN_WORKER_TOOLS_DIR")
    # A tool output file larger than this is a parse_error.
    max_output_bytes: int = Field(
        default=_DEFAULT_MAX_OUTPUT_BYTES, validation_alias="SKILL_SCAN_WORKER_MAX_OUTPUT_BYTES", ge=0
    )
    # Orphan sweep cadence.
    orphan_sweep_interval_seconds: float = Field(
        default=86400.0, validation_alias="SKILL_SCAN_WORKER_ORPHAN_SWEEP_INTERVAL_SECONDS", gt=0
    )
    # Runner poll interval.
    poll_interval_seconds: float = Field(default=1.0, validation_alias="SKILL_SCAN_WORKER_POLL_INTERVAL_SECONDS", gt=0)

    @cached_property
    def mongo_config(self) -> MongoConfig:
        return MongoConfig(
            mongo_uri=self.mongo_uri,
            mongodb_username=self.mongodb_username,
            mongodb_password=self.mongodb_password,
        )


settings = SkillScanWorkerSettings()
