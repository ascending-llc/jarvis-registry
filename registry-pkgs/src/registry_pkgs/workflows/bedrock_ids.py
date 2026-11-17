"""Bedrock model-id routing, kept dependency-free.

Separate from ``model_resolution`` (which imports litellm and agno) so a lightweight consumer — the
skill-scan Cisco adapter — can reuse the exact routing rule without pulling the LLM-client stack into its
process. ``model_resolution`` re-exports this for its own callers.
"""

from __future__ import annotations

import re

# Matches both application inference profiles (``:application-inference-profile/<id>``) and system-defined
# cross-region profiles (``:inference-profile/<geo>.<model>``).
_BEDROCK_INFERENCE_PROFILE_ARN = re.compile(r"^arn:[^:]+:bedrock:.*:(?:application-)?inference-profile/")


def bedrock_litellm_model_id(model_id_or_arn: str) -> str:
    """Return the LiteLLM route for a Bedrock model id or inference profile ARN.

    Inference profile ARNs are pinned to Converse, which every profile-capable model serves. Without the
    pin, LiteLLM picks the route from its own model list, which can't see the model behind an application
    profile ARN and, before 1.108, routed a system profile ARN of a model missing from that list
    (``.../inference-profile/global.openai.gpt-6.1-sol``) to InvokeModel, which such models don't serve.
    """
    route = "bedrock/converse/" if _BEDROCK_INFERENCE_PROFILE_ARN.match(model_id_or_arn) else "bedrock/"
    return f"{route}{model_id_or_arn}"
