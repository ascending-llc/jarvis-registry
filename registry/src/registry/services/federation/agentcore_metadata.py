from __future__ import annotations

from typing import Any


def extract_runtime_arn(metadata: dict[str, Any] | None) -> str | None:
    if not metadata:
        return None
    runtime_arn = metadata.get("runtimeArn")
    if not runtime_arn:
        runtime_arn = metadata.get("agentName")
    return str(runtime_arn) if runtime_arn else None


def extract_runtime_version(metadata: dict[str, Any] | None) -> str | None:
    if not metadata:
        return None
    version = metadata.get("runtimeVersion")
    if version is None:
        version = metadata.get("agentVersion")
    if version is None:
        return None
    return str(version)


def detect_runtime_version_change(
    existing_metadata: dict[str, Any] | None,
    new_metadata: dict[str, Any] | None,
) -> list[str]:
    old_version = extract_runtime_version(existing_metadata)
    new_version = extract_runtime_version(new_metadata)
    if old_version == new_version:
        return []
    version_label = "runtimeVersion"
    if (
        new_metadata
        and new_metadata.get("agentVersion") is not None
        or existing_metadata
        and existing_metadata.get("agentVersion") is not None
    ):
        version_label = "agentVersion"
    return [f"{version_label}: {old_version} -> {new_version}"]
