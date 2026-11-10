import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

import pytest

_ARN = "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/test"
_BASE_MODEL = "anthropic.claude-sonnet-4-5"


@pytest.fixture(params=["workspace", "repository"])
def recorder(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    workspace = Path(__file__).resolve().parents[2]
    path = (
        workspace / "scripts" / "record_fixtures.py"
        if request.param == "workspace"
        else workspace.parent / "scripts" / "skill_scan" / "record_fixtures.py"
    )
    spec = importlib.util.spec_from_file_location("record_fixtures", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.delenv("SCAN_RECORD_BEDROCK_BASE_MODEL", raising=False)
    return module


async def test_arn_without_base_model_rejected_before_any_recording(
    recorder: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAN_RECORD_BEDROCK_MODEL", _ARN)
    record = AsyncMock()
    monkeypatch.setattr(recorder, "_record", record)
    fixture = tmp_path / "malicious-llm.json"
    fixture.write_text("existing fixture")
    monkeypatch.setattr(recorder, "_FIXTURES", tmp_path)

    with pytest.raises(SystemExit, match="SCAN_RECORD_BEDROCK_BASE_MODEL"):
        await recorder._main(with_llm=True)

    record.assert_not_awaited()
    assert fixture.read_text() == "existing fixture"


@pytest.mark.parametrize("model", [_ARN, _BASE_MODEL])
async def test_llm_recordings_keep_llm_enabled(
    recorder: ModuleType, monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    monkeypatch.setenv("SCAN_RECORD_BEDROCK_MODEL", model)
    if model == _ARN:
        monkeypatch.setenv("SCAN_RECORD_BEDROCK_BASE_MODEL", _BASE_MODEL)
    configured = recorder._record_llm()
    assert configured.bedrock_model == model
    assert configured.base_model_id == _BASE_MODEL
    record = AsyncMock()
    monkeypatch.setattr(recorder, "_record", record)

    await recorder._main(with_llm=True)

    llm_calls = [call for call in record.await_args_list if call.args[3] is not None]
    assert len(llm_calls) == len(recorder.ENABLED_ADAPTERS) * 2
    for call in llm_calls:
        adapter, skill_dir, output, llm = call.args
        assert adapter.build_invocation(skill_dir, output, llm).llm_used
        assert llm.bedrock_model == (model if output.name == "malicious-llm.json" else recorder._INVALID_MODEL)


def _repository_recorder() -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "scripts" / "skill_scan" / "record_fixtures.py"
    spec = importlib.util.spec_from_file_location("repository_record_fixtures", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_redact_strips_account_ids_and_role_sessions() -> None:
    # Recorded fixtures are committed to a public repo.
    redact = _repository_recorder()._redact
    report = (
        '{"model": "bedrock/converse/arn:aws:bedrock:us-east-1:111122223333:application-inference-profile/p",'
        ' "error": "User: arn:aws:sts::111122223333:assumed-role/ExampleSsoRole/someone@example.com'
        ' is not authorized"}'
    )
    redacted = redact(report)
    assert "111122223333" not in redacted
    assert "someone@example.com" not in redacted
    assert "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/p" in redacted
    assert "assumed-role/example-role/example-session is not authorized" in redacted
    assert redact("Error: Directory does not exist: /w/skills/x") == "Error: Directory does not exist: /w/skills/x"
