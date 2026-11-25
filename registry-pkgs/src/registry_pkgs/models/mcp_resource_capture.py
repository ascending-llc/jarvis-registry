"""Proof that concrete resources were captured with the current shared server credentials.

Unknown/legacy lists fail closed. Bind the proof to both configuration and contents so an old
writer cannot keep a valid proof while replacing the list with one user's OAuth resources.
"""

import hashlib
import json
from typing import Any, TypedDict
from uuid import uuid4

RESOURCE_CAPTURE_KEY = "resourceCapture"
RESOURCE_CAPTURE_REVISION = "resource_capture_revision"
RESOURCE_KIND = "resource_kind"
RESOURCE_CAPTURE_CONTEXT_FIELDS = (
    "url",
    "type",
    "requiresOAuth",
    "oauth",
    "apiKey",
    "headers",
    "runtimeAccess",
    "customUserVars",
)


class ResourceCaptureProof(TypedDict):
    contextHash: str
    contentsHash: str
    revision: str


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def resource_capture_context(config: dict[str, Any]) -> str:
    """Fingerprint only endpoint and authorization inputs, never publish this hash in vectors."""
    return _digest({key: config[key] for key in RESOURCE_CAPTURE_CONTEXT_FIELDS if key in config})


def uses_shared_credentials(config: dict[str, Any]) -> bool:
    """Whether captures use credentials shared by every viewer (not one user's OAuth token)."""
    return bool(config.get("runtimeAccess")) or not (config.get("requiresOAuth") or "oauth" in config)


def create_resource_capture(config: dict[str, Any], resources: list[dict[str, Any]]) -> ResourceCaptureProof | None:
    """Create a proof only after a successful listing; a failed listing must not call this function."""
    if not resources or not uses_shared_credentials(config):
        return None
    context_hash, contents_hash = resource_capture_context(config), _digest(resources)
    previous = config.get(RESOURCE_CAPTURE_KEY)
    revision = previous.get("revision") if isinstance(previous, dict) else None
    if not (
        isinstance(revision, str)
        and revision
        and previous.get("contextHash") == context_hash
        and previous.get("contentsHash") == contents_hash
    ):
        revision = uuid4().hex
    return ResourceCaptureProof(contextHash=context_hash, contentsHash=contents_hash, revision=revision)


def resource_capture_revision(config: dict[str, Any]) -> str | None:
    """Return a trusted list's opaque revision, or None for stale, OAuth or unsigned contents."""
    proof = config.get(RESOURCE_CAPTURE_KEY)
    if not uses_shared_credentials(config) or not isinstance(proof, dict):
        return None
    revision = proof.get("revision")
    if (
        isinstance(revision, str)
        and revision
        and proof.get("contextHash") == resource_capture_context(config)
        and proof.get("contentsHash") == _digest(config.get("resources", []))
    ):
        return revision
    return None
