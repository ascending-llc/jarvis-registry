from __future__ import annotations

import json
from typing import Any

from agno.workflow import StepInput

from registry_pkgs.workflows.prompt import (
    ADDITIONAL_DATA_DEPENDENCY_NODE_NAMES,
    ADDITIONAL_DATA_DEPENDENCY_OBJECTIVES,
    ADDITIONAL_DATA_STEP_OBJECTIVE,
    ADDITIONAL_DATA_WORKFLOW_DESCRIPTION,
    DependencySpec,
    render_step_prompt,
)
from registry_pkgs.workflows.serialization import content_to_str


def extract_user_text(initial_input: dict[str, Any] | None) -> str:
    """Derive the runner's ``user_text`` string from a run's ``initial_input``."""
    if not initial_input:
        return ""
    user_text = initial_input.get("user_text", "")
    if user_text:
        return str(user_text)
    return json.dumps(initial_input)


def build_prompt(step_input: StepInput) -> str:
    """Render the Markdown prompt for an executor's LLM from per-node intention data.

    Reads ``jarvis_*`` keys injected into ``StepInput.additional_data`` by
    ``compiler._with_intention_data``, then delegates to ``render_step_prompt``.
    Falls back to raw trigger text when ``additional_data`` has no step_objective
    (e.g. demo executors or direct unit-test calls that bypass the compiler).
    """
    additional = step_input.additional_data or {}
    step_objective: str = additional.get(ADDITIONAL_DATA_STEP_OBJECTIVE) or ""

    # Graceful degradation for demo executors / direct test calls
    if not step_objective:
        return step_input.get_input_as_string() or "(no input)"

    workflow_description: str | None = additional.get(ADDITIONAL_DATA_WORKFLOW_DESCRIPTION)
    dependency_node_names: list[str] = additional.get(ADDITIONAL_DATA_DEPENDENCY_NODE_NAMES) or []
    dependency_objectives: dict[str, str] = additional.get(ADDITIONAL_DATA_DEPENDENCY_OBJECTIVES) or {}

    previous = step_input.previous_step_outputs or {}

    # Build DependencySpec list — content is None when the upstream node hasn't
    # produced output yet (e.g. a not-yet-executed parallel branch).  Those deps
    # still appear in the Dependencies section so the LLM knows the intended
    # source, but are omitted from Current Step Inputs.
    dependencies: list[DependencySpec] = [
        DependencySpec(
            name=name,
            objective=dependency_objectives.get(name, ""),
            content=content_to_str(previous[name].content)
            if name in previous and previous[name].content is not None
            else None,
        )
        for name in dependency_node_names
    ]

    # Entry-node condition: no upstream step has executed yet in this run.
    # Generalises correctly to multiple parallel entry points.
    is_entry = not previous
    initial_input = step_input.get_input_as_string() if is_entry and not dependencies else None

    return render_step_prompt(
        step_objective=step_objective,
        workflow_description=workflow_description,
        dependencies=dependencies,
        initial_input=initial_input,
    )
