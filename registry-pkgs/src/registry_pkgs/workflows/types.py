from __future__ import annotations

# Synthetic executor-registry key used for pool A2A nodes.
# Format: "__pool__<node.id>"
POOL_KEY_PREFIX = "__pool__"


class WorkflowConfigError(ValueError):
    """Base for workflow configuration problems that are expected in certain environments.

    Distinguishable from unexpected runtime failures so callers can choose a
    lower log level (WARNING instead of ERROR with traceback).
    """


class MissingRegistryTokenError(WorkflowConfigError):
    """Raised when an MCP executor is requested but no registry_token was supplied.

    Typical cause: the run was triggered via a cookie-only session (no
    ``Authorization: Bearer`` header) without the service-JWT fallback in place.
    """
