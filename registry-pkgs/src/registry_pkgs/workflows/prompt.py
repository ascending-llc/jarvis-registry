"""Prompt rendering for workflow step executors.

Single source of truth for how step intention is assembled into the Markdown
prompt that an MCP- or A2A-backed LLM actually receives.

Architecture note
-----------------
Intention data (step_objective, workflow_description, the run's initial_input,
dependency objectives and their runtime outputs) travels from the compiler into
each executor call via ``StepInput.additional_data``.  The compiler's
``_with_intention_data`` wrapper injects this data per-node; ``build_prompt`` in
helpers.py reads it back and delegates to ``render_step_prompt`` here.

``render_step_prompt`` is the **only** place Markdown gets built.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from jinja2 import Template


@dataclass(frozen=True)
class DependencySpec:
    """One upstream node this step explicitly depends on.

    Attributes:
        name:      The node name as it appears in referenced_node_names.
        objective: The upstream node's own step_objective — shown even when
                   content is absent so the LLM knows the intended source.
        content:   Stringified output summary of the upstream node, or None when that
                   node has not yet produced a result (e.g. a not-yet-executed
                   parallel branch).  None means "list in Dependencies but omit
                   from Current Step Inputs".
    """

    name: str
    objective: str
    content: str | None = None


_STEP_PROMPT_TEMPLATE = Template(
    """# Step Objective

{{ step_objective }}

{% if trigger_parameters %}# Workflow Trigger Parameters

*Fixed for this run; available to every step.*

```json
{{ trigger_parameters }}
```

{% endif %}{% if workflow_description %}# Workflow Context

*This step is part of a larger multi-step workflow.*

{{ workflow_description }}

{% endif %}{% if dependencies %}# Dependencies

{% for dependency in dependencies %}## "{{ dependency.name }}"

{{ dependency.objective }}

{% endfor %}{% if dependencies_with_content %}# Current Step Inputs

{% for dependency in dependencies_with_content %}## "{{ dependency.name }}" — Output

{{ dependency.content }}

{% endfor %}{% endif %}{% elif initial_input %}# Current Step Inputs

## Workflow Trigger Input

{{ initial_input }}
{% endif %}""",
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_step_prompt(
    *,
    step_objective: str,
    workflow_description: str | None,
    dependencies: list[DependencySpec],
    initial_input: str | None,
    trigger_parameters: str | None = None,
) -> str:
    """Assemble the Markdown prompt handed to an MCP/A2A executor's underlying LLM.

    Potentially multi-line values are rendered verbatim beneath block-level
    Markdown headings. Trigger parameters are fenced as JSON.

    Rules:
    - ``trigger_parameters`` section omitted when falsy; otherwise rendered on
      *every* node regardless of dependencies or graph position — unlike
      ``initial_input`` below, it is not gated to entry nodes.
    - ``workflow_description`` section omitted when None.
    - ``Dependencies`` section always lists every declared dependency with its
      objective, even when content is not yet available (parallel branches).
    - ``Current Step Inputs`` lists only dependencies whose content is not None.
    - When there are no dependencies AND this is an entry node (``initial_input``
      is truthy), ``Current Step Inputs`` shows the original workflow trigger
      instead of dependency outputs.
    - A mid-graph STEP node with no ``referenced_node_names`` and no available
      ``initial_input`` receives only the goal line (plus ``trigger_parameters``
      when present) — this is intentional (explicit-over-clever: every
      cross-step dependency must be declared).

    Args:
        step_objective:      Plain-language description of what this step must do.
                             Required; the returned string is meaningless without it.
        workflow_description: Optional top-level workflow context injected once at
                             the top of the prompt as orientation for the LLM.
        dependencies:        Resolved upstream nodes.  ``DependencySpec.content``
                             is None when the node hasn't executed yet.
        initial_input:       The original workflow trigger text, passed only for
                             entry nodes (``previous_step_outputs`` empty).
        trigger_parameters:  JSON-rendered ``WorkflowRun.initial_input``, shown to
                             every node so any step can reference user-supplied
                             trigger fields (e.g. a Slack member ID) without that
                             data having to be relayed through upstream node output.
    """
    dependencies_with_content = [dependency for dependency in dependencies if dependency.content is not None]
    rendered = _STEP_PROMPT_TEMPLATE.render(
        step_objective=step_objective,
        workflow_description=workflow_description,
        dependencies=dependencies,
        dependencies_with_content=dependencies_with_content,
        initial_input=initial_input,
        trigger_parameters=trigger_parameters,
    )
    return re.sub(r"\n{3,}", "\n\n", rendered).strip()


# ---------------------------------------------------------------------------
# additional_data key constants
# ---------------------------------------------------------------------------
# These string keys are the contract between compiler._with_intention_data
# (writer) and helpers.build_prompt (reader).  Centralising them here means
# a typo in either file is caught at import time.

ADDITIONAL_DATA_STEP_OBJECTIVE = "jarvis_step_objective"
ADDITIONAL_DATA_WORKFLOW_DESCRIPTION = "jarvis_workflow_description"
ADDITIONAL_DATA_DEPENDENCY_NODE_NAMES = "jarvis_dependency_node_names"
ADDITIONAL_DATA_DEPENDENCY_OBJECTIVES = "jarvis_dependency_objectives"
ADDITIONAL_DATA_INITIAL_INPUT = "jarvis_initial_input"
