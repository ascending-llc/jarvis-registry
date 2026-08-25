from __future__ import annotations

from registry_pkgs.models.workflow import StepConfig

BUILTIN_ECHO_EXECUTOR_KEY = "echo"
BUILTIN_SET_VALUE_EXECUTOR_KEY = "set_value"
BUILTIN_EXECUTOR_KEYS = frozenset({BUILTIN_ECHO_EXECUTOR_KEY, BUILTIN_SET_VALUE_EXECUTOR_KEY})

# Synthetic executor-registry key used for pool A2A nodes.
# Format: "__pool__<node.id>"
POOL_KEY_PREFIX = "__pool__"

# Session-state bucket where wrapped step executors store the exact input each
# node received. WorkflowRunSyncer reads this to persist NodeRun.input_snapshot.
NODE_INPUT_SNAPSHOTS_KEY = "__node_input_snapshots__"


def is_skip_tolerated_failure(success: bool, step_config: StepConfig | None) -> bool:
    """Return whether a failed step is tolerated by its ``on_error="skip"`` policy."""
    if success:
        return False
    return bool(step_config and step_config.on_error == "skip")


class WorkflowConfigError(ValueError):
    """Base for workflow configuration problems that are expected in certain environments.

    Distinguishable from unexpected runtime failures so callers can choose a
    lower log level (WARNING instead of ERROR with traceback).
    """
