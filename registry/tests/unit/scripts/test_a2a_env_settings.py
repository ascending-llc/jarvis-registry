"""Unit tests for sample A2A agent Bedrock model selection."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[4]
_AGENT_ENV_SETTINGS = (
    _ROOT / "agents" / "a2a" / "src" / "flight-booking-agent" / "env_settings.py",
    _ROOT / "agents" / "a2a" / "src" / "travel-assistant-agent" / "env_settings.py",
)
_GOVERNANCE_HAIKU_AIP_ARN = "arn:aws:bedrock:us-east-1:897729109735:application-inference-profile/rbi3mxnqa5vz"
_GOVERNANCE_SONNET_AIP_ARN = "arn:aws:bedrock:us-east-1:897729109735:application-inference-profile/1rh94g6d583t"


def _load_module(path: Path):
    module_name = f"a2a_env_settings_{path.parent.name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
@pytest.mark.parametrize("module_path", _AGENT_ENV_SETTINGS)
def test_a2a_env_settings_default_to_sonnet_aip(module_path: Path, monkeypatch) -> None:
    module = _load_module(module_path)
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.delenv("AWS_BEDROCK_SONNET_AIP_ARN", raising=False)

    model_id, source = module._get_bedrock_model_config()

    assert module.DEFAULT_BEDROCK_SONNET_AIP_ARN == _GOVERNANCE_SONNET_AIP_ARN
    assert model_id == _GOVERNANCE_SONNET_AIP_ARN
    assert source == module.DEFAULT_BEDROCK_SONNET_AIP_SOURCE


@pytest.mark.unit
@pytest.mark.parametrize("module_path", _AGENT_ENV_SETTINGS)
def test_a2a_env_settings_accept_governance_haiku_aip(module_path: Path, monkeypatch) -> None:
    module = _load_module(module_path)
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.setenv("AWS_BEDROCK_SONNET_AIP_ARN", f" {_GOVERNANCE_HAIKU_AIP_ARN} ")

    model_id, source = module._get_bedrock_model_config()

    assert model_id == _GOVERNANCE_HAIKU_AIP_ARN
    assert source == "AWS_BEDROCK_SONNET_AIP_ARN"


@pytest.mark.unit
@pytest.mark.parametrize("module_path", _AGENT_ENV_SETTINGS)
def test_a2a_env_settings_prefer_explicit_model_id(module_path: Path, monkeypatch) -> None:
    module = _load_module(module_path)
    monkeypatch.setenv("BEDROCK_MODEL_ID", " explicit-model ")
    monkeypatch.setenv("AWS_BEDROCK_SONNET_AIP_ARN", module.DEFAULT_BEDROCK_SONNET_AIP_ARN)

    model_id, source = module._get_bedrock_model_config()

    assert model_id == "explicit-model"
    assert source == "BEDROCK_MODEL_ID"


@pytest.mark.unit
@pytest.mark.parametrize("module_path", _AGENT_ENV_SETTINGS)
def test_a2a_env_settings_use_local_fallback_when_aip_env_is_blank(module_path: Path, monkeypatch) -> None:
    module = _load_module(module_path)
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.setenv("AWS_BEDROCK_SONNET_AIP_ARN", " ")

    model_id, source = module._get_bedrock_model_config()

    assert model_id == module.DEFAULT_BEDROCK_MODEL_ID
    assert source == module.DEFAULT_BEDROCK_MODEL_SOURCE
