"""Bedrock model-id routing, kept dependency-free.

Separate from ``model_resolution`` (which imports litellm and agno) so a lightweight consumer — the
skill-scan Cisco adapter — can reuse the exact routing rule without pulling the LLM-client stack into its
process. ``model_resolution`` re-exports this for its own callers.
"""

from __future__ import annotations

_BEDROCK_APPLICATION_INFERENCE_PROFILE = ":application-inference-profile/"


def bedrock_litellm_model_id(model_id_or_arn: str) -> str:
    """Return the LiteLLM route for a Bedrock model or application inference profile."""
    route = "bedrock/converse/" if _BEDROCK_APPLICATION_INFERENCE_PROFILE in model_id_or_arn else "bedrock/"
    return f"{route}{model_id_or_arn}"
