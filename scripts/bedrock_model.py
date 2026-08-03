"""Shared Bedrock model selection for workflow smoke scripts."""

from __future__ import annotations

import os

BEDROCK_AIP_ARN_ENV = "AWS_BEDROCK_SONNET_AIP_ARN"
DEFAULT_BEDROCK_SONNET_AIP_ARN = "arn:aws:bedrock:us-east-1:897729109735:application-inference-profile/1rh94g6d583t"


def resolve_bedrock_model_id(*, model_env_var: str, fallback_model_id: str) -> str:
    aip_arn = os.environ.get(BEDROCK_AIP_ARN_ENV, DEFAULT_BEDROCK_SONNET_AIP_ARN).strip()
    if aip_arn:
        return aip_arn

    model_id = (os.getenv(model_env_var) or "").strip()
    if model_id:
        return model_id

    return fallback_model_id
