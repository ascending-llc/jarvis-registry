from skill_scan_worker.config import SkillScanWorkerSettings, settings


def test_defaults() -> None:
    assert settings.concurrency == 2
    assert settings.tool_timeout_seconds == 600.0
    assert settings.llm_max_text_bytes == 524288
    assert settings.work_dir == "/scan-tmp"
    assert settings.tools_dir == "/opt/scanners"
    assert settings.max_output_bytes == 16 * 1024 * 1024
    assert settings.orphan_sweep_interval_seconds == 86400.0
    assert settings.poll_interval_seconds == 1.0


def test_env_override(monkeypatch) -> None:
    monkeypatch.setenv("SKILL_SCAN_WORKER_CONCURRENCY", "4")
    monkeypatch.setenv("SKILL_SCAN_WORKER_TOOL_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("SKILL_SCAN_WORKER_WORK_DIR", "/tmp/scan")
    s = SkillScanWorkerSettings()
    assert s.concurrency == 4
    assert s.tool_timeout_seconds == 120.0
    assert s.work_dir == "/tmp/scan"


def test_mongo_config_mirrors_uri() -> None:
    assert settings.mongo_config.mongo_uri == settings.mongo_uri
