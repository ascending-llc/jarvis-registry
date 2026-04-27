from __future__ import annotations

from agno.workflow import StepInput


def build_prompt(step_input: StepInput) -> str:
    """Assemble a prompt string from step_input fields."""
    parts: list[str] = []
    if step_input.previous_step_content:
        parts.append(f"Context from previous step:\n{step_input.previous_step_content}")
    if step_input.input:
        parts.append(step_input.input)
    return "\n\n".join(parts) or "(no input)"
