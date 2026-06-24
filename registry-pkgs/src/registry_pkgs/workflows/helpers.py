from __future__ import annotations

import json
from typing import Any

from agno.workflow import StepInput


def extract_user_text(initial_input: dict[str, Any] | None) -> str:
    """Derive the runner's ``user_text`` string from a run's ``initial_input``."""
    if not initial_input:
        return ""
    user_text = initial_input.get("user_text", "")
    if user_text:
        return str(user_text)
    return json.dumps(initial_input)


def build_prompt(step_input: StepInput) -> str:
    """Assemble a prompt string from step_input fields."""
    parts: list[str] = []
    if step_input.previous_step_content:
        parts.append(f"Context from previous step:\n{step_input.previous_step_content}")
    if step_input.input:
        parts.append(step_input.input)
    return "\n\n".join(parts) or "(no input)"
