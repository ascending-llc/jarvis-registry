"""Prompt rendering for workflow step executors.

Single source of truth for how step intention is assembled into the Markdown
prompt that an MCP- or A2A-backed LLM actually receives.

Architecture note
-----------------
Intention data (step_objective, workflow_description, dependency objectives and
their runtime outputs) travels from the compiler into each executor call via
``StepInput.additional_data``.  The compiler's ``_with_intention_data`` wrapper
injects this data per-node; ``build_prompt`` in helpers.py reads it back and
delegates to ``render_step_prompt`` here.

``render_step_prompt`` is the **only** place Markdown gets built.
"""

from __future__ import annotations

from dataclasses import dataclass

from registry_pkgs.workflows.serialization import content_to_str  # noqa: F401 — re-exported for helpers


@dataclass(frozen=True)
class DependencySpec:
    """One upstream node this step explicitly depends on.

    Attributes:
        name:      The node name as it appears in referenced_node_names.
        objective: The upstream node's own step_objective — shown even when
                   content is absent so the LLM knows the intended source.
        content:   Stringified output of the upstream node, or None when that
                   node has not yet produced a result (e.g. a not-yet-executed
                   parallel branch).  None means "list in Dependencies but omit
                   from Current Step Inputs".
    """

    name: str
    objective: str
    content: str | None = None


_GOAL_PREFIX = "**IMPORTANT: The goal of this step is to"
_WORKFLOW_CTX_PREFIX = "This step is part of a larger workflow:"
_DEPS_HEADER = "Dependencies:"
_INPUTS_HEADER = "Current Step Inputs:"
_TRIGGER_LABEL = "Workflow trigger input"


def render_step_prompt(
    *,
    step_objective: str,
    workflow_description: str | None,
    dependencies: list[DependencySpec],
    initial_input: str | None,
) -> str:
    """Assemble the Markdown prompt handed to an MCP/A2A executor's underlying LLM.

    Prompt structure (all sections separated by blank lines):

        **IMPORTANT: The goal of this step is to {step_objective}.**

        This step is part of a larger workflow: {workflow_description}

        Dependencies:
        - "{dep.name}": {dep.objective}.
        [... one line per dependency ...]

        Current Step Inputs:
        - "{dep.name}" outputs:

          ```
          {dep.content}
          ```
        [... one block per dependency that has produced output ...]

    Rules:
    - ``workflow_description`` section omitted when None.
    - ``Dependencies`` section always lists every declared dependency with its
      objective, even when content is not yet available (parallel branches).
    - ``Current Step Inputs`` lists only dependencies whose content is not None.
    - When there are no dependencies AND this is an entry node (``initial_input``
      is not None), ``Current Step Inputs`` shows the original workflow trigger
      instead of dependency outputs.
    - A mid-graph STEP node with no ``referenced_node_names`` and no available
      ``initial_input`` receives only the goal line — this is intentional
      (explicit-over-clever: every cross-step dependency must be declared).

    Args:
        step_objective:      Plain-language description of what this step must do.
                             Required; the returned string is meaningless without it.
        workflow_description: Optional top-level workflow context injected once at
                             the top of the prompt as orientation for the LLM.
        dependencies:        Resolved upstream nodes.  ``DependencySpec.content``
                             is None when the node hasn't executed yet.
        initial_input:       The original workflow trigger text, passed only for
                             entry nodes (``previous_step_outputs`` empty).
    """
    sections: list[str] = []

    # 1. Goal — always first and most prominent
    sections.append(f"{_GOAL_PREFIX} {step_objective}.**")

    # 2. Workflow context — orientation without being prescriptive
    if workflow_description:
        sections.append(f"{_WORKFLOW_CTX_PREFIX} {workflow_description}")

    if dependencies:
        # 3a. List every declared dependency and its objective (even if no output yet)
        dep_lines = "\n".join(f'- "{d.name}": {d.objective}.' for d in dependencies)
        sections.append(f"{_DEPS_HEADER}\n{dep_lines}")

        # 3b. Current Step Inputs — only dependencies that have produced output
        with_content = [d for d in dependencies if d.content is not None]
        if with_content:
            input_blocks = "\n\n".join(f'- "{d.name}" outputs:\n\n  ```\n  {d.content}\n  ```' for d in with_content)
            sections.append(f"{_INPUTS_HEADER}\n{input_blocks}")

    elif initial_input:
        # 3c. Entry node with no dependencies — show original trigger
        sections.append(f"{_INPUTS_HEADER}\n- {_TRIGGER_LABEL}:\n\n  ```\n  {initial_input}\n  ```")

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# additional_data key constants
# ---------------------------------------------------------------------------
# These string keys are the contract between compiler._with_intention_data
# (writer) and helpers.build_prompt (reader).  Centralising them here means
# a typo in either file is caught at import time.

ADDITIONAL_DATA_STEP_OBJECTIVE = "step_objective"
ADDITIONAL_DATA_WORKFLOW_DESCRIPTION = "workflow_description"
ADDITIONAL_DATA_DEPENDENCY_NODE_NAMES = "dependency_node_names"
ADDITIONAL_DATA_DEPENDENCY_OBJECTIVES = "dependency_objectives"
