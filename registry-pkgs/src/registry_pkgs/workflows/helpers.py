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
    """Build the prompt handed to an executor's LLM from a ``StepInput``.

    Reads per-node intention data injected by ``_with_intention_data`` in
    ``compiler.py`` via ``StepInput.additional_data``, then delegates to
    ``render_step_prompt`` for the actual Markdown assembly.

    Fallback
    --------
    When ``additional_data`` is absent or contains no ``step_objective``
    (e.g. builtin demo executors called directly in unit tests without going
    through the compiler), the function returns the raw trigger text via
    ``step_input.get_input_as_string()`` so those callers are not broken.

    Data flow
    ---------
    Writer: ``compiler._with_intention_data`` sets these keys on a
            ``copy.copy(step_input).additional_data`` before calling the executor.
    Reader: this function reads them back and resolves dependency content from
            ``step_input.previous_step_outputs``.

    Key                                  | Type              | Meaning
    -------------------------------------|-------------------|----------------------------
    ``step_objective``                   | str               | What this step must do
    ``workflow_description``             | str | None        | Top-level workflow context
    ``dependency_node_names``            | list[str]         | Ordered declared deps
    ``dependency_objectives``            | dict[str, str]    | Each dep's own objective
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
