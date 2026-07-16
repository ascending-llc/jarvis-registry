"""Media-metadata snapshot helpers for workflow StepOutput persistence and replay.

AS-1725 P2 (lightweight): NodeRun snapshots persist only media *metadata*
(id, url, mime_type, filename, ...) — never bytes — so Mongo documents stay
small.  On rerun/retry, cached outputs are rebuilt as metadata-only "shell"
media objects.  Prompt summaries render identically to a live run because
``step_output_to_prompt_text`` only reads metadata; the binary content itself
is NOT restored (full artifact handoff is out of scope, see the AS-1725 plan).

Kept separate from serialization.py so that module stays dependency-free.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agno.media import Audio, File, Image, Video
from agno.workflow import StepOutput
from pydantic import ValidationError

MEDIA_SNAPSHOT_KEYS: tuple[str, ...] = ("images", "videos", "audio", "files")

_IMAGE_FIELDS = ("id", "url", "mime_type", "format", "alt_text")
_VIDEO_FIELDS = ("id", "url", "mime_type", "format", "duration")
_AUDIO_FIELDS = ("id", "url", "mime_type", "format", "duration", "transcript")
_FILE_FIELDS = ("id", "url", "mime_type", "file_type", "filename", "name", "size")

_FIELDS_BY_KEY: dict[str, tuple[str, ...]] = {
    "images": _IMAGE_FIELDS,
    "videos": _VIDEO_FIELDS,
    "audio": _AUDIO_FIELDS,
    "files": _FILE_FIELDS,
}


def _metadata_dict(item: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    """Pick the whitelisted non-None attributes off a media object into a Mongo-safe dict."""
    metadata: dict[str, Any] = {}
    for field in fields:
        value = getattr(item, field, None)
        if value is None:
            continue
        metadata[field] = value if isinstance(value, (int, float, bool)) else str(value)
    return metadata


def serialize_media_items(items: Any, key: str) -> list[dict[str, Any]]:
    """Serialize a raw list of agno media objects (Image/Video/Audio/File) to metadata-only dicts."""
    if not items:
        return []
    fields = _FIELDS_BY_KEY[key]
    return [_metadata_dict(item, fields) for item in items]


def serialize_step_output_media(output: StepOutput) -> dict[str, list[dict[str, Any]]]:
    """Extract Mongo-safe media metadata (no bytes) from a StepOutput; only non-empty keys are emitted."""
    snapshot: dict[str, list[dict[str, Any]]] = {}
    for key in MEDIA_SNAPSHOT_KEYS:
        serialized = serialize_media_items(getattr(output, key, None), key)
        if serialized:
            snapshot[key] = serialized
    return snapshot


def _shell_kwargs(metadata: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """Pick the whitelisted non-None snapshot values as constructor kwargs for a shell."""
    return {field: metadata[field] for field in fields if metadata.get(field) is not None}


def _media_shell(metadata: Mapping[str, Any], fields: tuple[str, ...], cls: type[Audio | Image | Video]) -> Any:
    """Rebuild an Image/Video/Audio shell; content=b'' placates agno's url/filepath/content requirement."""
    kwargs = _shell_kwargs(metadata, fields)
    if not kwargs.get("url"):
        kwargs["content"] = b""  # bytes were never persisted — this is a metadata-only shell
    return cls(**kwargs)


def _file_shell(metadata: Mapping[str, Any]) -> File:
    """Rebuild a File shell; agno File needs an identity field, so fall back to filename/name."""
    kwargs = _shell_kwargs(metadata, _FILE_FIELDS)
    if not (kwargs.get("id") or kwargs.get("url")):
        kwargs["id"] = kwargs.get("filename") or kwargs.get("name") or "file"
    return File(**kwargs)


_SHELL_BUILDERS: dict[str, Any] = {
    "images": lambda metadata: _media_shell(metadata, _IMAGE_FIELDS, Image),
    "videos": lambda metadata: _media_shell(metadata, _VIDEO_FIELDS, Video),
    "audio": lambda metadata: _media_shell(metadata, _AUDIO_FIELDS, Audio),
    "files": _file_shell,
}


def media_from_snapshot(snapshot: Mapping[str, Any]) -> dict[str, list[Any] | None]:
    """Rebuild metadata-only shell media objects from a persisted snapshot.

    Returns kwargs suitable for ``StepOutput(**media_from_snapshot(...))``.
    Malformed entries are skipped rather than failing the whole replay.
    """
    result: dict[str, list[Any] | None] = {}
    for key in MEDIA_SNAPSHOT_KEYS:
        entries = snapshot.get(key)
        if not isinstance(entries, list):
            result[key] = None
            continue
        shells: list[Any] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            try:
                shells.append(_SHELL_BUILDERS[key](entry))
            except (ValidationError, TypeError, ValueError):
                # A malformed snapshot entry must not break the whole replay.
                continue
        result[key] = shells or None
    return result
