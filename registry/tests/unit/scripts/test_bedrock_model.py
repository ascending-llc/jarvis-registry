"""Unit tests for workflow script Bedrock model selection."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[4] / "scripts" / "bedrock_model.py"
_SPEC = importlib.util.spec_from_file_location("bedrock_model", _SCRIPT_PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
bedrock_model = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bedrock_model)

_GOVERNANCE_HAIKU_AIP_ARN = "arn:aws:bedrock:us-east-1:897729109735:application-inference-profile/rbi3mxnqa5vz"
_GOVERNANCE_SONNET_AIP_ARN = "arn:aws:bedrock:us-east-1:897729109735:application-inference-profile/1rh94g6d583t"


@pytest.mark.unit
def test_resolve_bedrock_model_id_defaults_to_sonnet_aip(monkeypatch) -> None:
    monkeypatch.delenv("AWS_BEDROCK_SONNET_AIP_ARN", raising=False)
    monkeypatch.delenv("BEDROCK_MODEL", raising=False)

    model_id = bedrock_model.resolve_bedrock_model_id(
        model_env_var="BEDROCK_MODEL",
        fallback_model_id="us.amazon.nova-lite-v1:0",
    )

    assert bedrock_model.DEFAULT_BEDROCK_SONNET_AIP_ARN == _GOVERNANCE_SONNET_AIP_ARN
    assert model_id == _GOVERNANCE_SONNET_AIP_ARN


@pytest.mark.unit
def test_resolve_bedrock_model_id_accepts_governance_haiku_aip(monkeypatch) -> None:
    monkeypatch.setenv("AWS_BEDROCK_SONNET_AIP_ARN", f" {_GOVERNANCE_HAIKU_AIP_ARN} ")
    monkeypatch.setenv("BEDROCK_MODEL", "us.amazon.nova-lite-v1:0")

    model_id = bedrock_model.resolve_bedrock_model_id(
        model_env_var="BEDROCK_MODEL",
        fallback_model_id="fallback",
    )

    assert model_id == _GOVERNANCE_HAIKU_AIP_ARN


@pytest.mark.unit
def test_resolve_bedrock_model_id_prefers_trimmed_aip_env(monkeypatch) -> None:
    aip_arn = "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/custom"
    monkeypatch.setenv("AWS_BEDROCK_SONNET_AIP_ARN", f" {aip_arn} ")
    monkeypatch.setenv("BEDROCK_MODEL", "us.amazon.nova-lite-v1:0")

    model_id = bedrock_model.resolve_bedrock_model_id(
        model_env_var="BEDROCK_MODEL",
        fallback_model_id="fallback",
    )

    assert model_id == aip_arn


@pytest.mark.unit
def test_resolve_bedrock_model_id_uses_model_env_when_aip_env_is_blank(monkeypatch) -> None:
    monkeypatch.setenv("AWS_BEDROCK_SONNET_AIP_ARN", " ")
    monkeypatch.setenv("BEDROCK_MODEL", " us.amazon.nova-lite-v1:0 ")

    model_id = bedrock_model.resolve_bedrock_model_id(
        model_env_var="BEDROCK_MODEL",
        fallback_model_id="fallback",
    )

    assert model_id == "us.amazon.nova-lite-v1:0"


@pytest.mark.unit
def test_resolve_bedrock_model_id_uses_fallback_when_aip_and_model_env_are_blank(monkeypatch) -> None:
    monkeypatch.setenv("AWS_BEDROCK_SONNET_AIP_ARN", " ")
    monkeypatch.setenv("BEDROCK_MODEL", " ")

    model_id = bedrock_model.resolve_bedrock_model_id(
        model_env_var="BEDROCK_MODEL",
        fallback_model_id="fallback",
    )

    assert model_id == "fallback"
