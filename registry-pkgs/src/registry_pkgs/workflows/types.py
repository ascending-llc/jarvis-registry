from __future__ import annotations

# Synthetic executor-registry key used for pool A2A nodes.
# Format: "__pool__<node.id>"
POOL_KEY_PREFIX = "__pool__"

# Session-state bucket where wrapped step executors store the exact input each
# node received. WorkflowRunSyncer reads this to persist NodeRun.input_snapshot.
NODE_INPUT_SNAPSHOTS_KEY = "__node_input_snapshots__"


class WorkflowConfigError(ValueError):
    """Base for workflow configuration problems that are expected in certain environments.

    Distinguishable from unexpected runtime failures so callers can choose a
    lower log level (WARNING instead of ERROR with traceback).
    """
