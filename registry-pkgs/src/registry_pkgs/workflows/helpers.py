from __future__ import annotations

import json
from typing import Any

from agno.workflow import StepInput, StepOutput

from registry_pkgs.workflows.prompt import (
    ADDITIONAL_DATA_DEPENDENCY_NODE_NAMES,
    ADDITIONAL_DATA_DEPENDENCY_OBJECTIVES,
    ADDITIONAL_DATA_STEP_OBJECTIVE,
    ADDITIONAL_DATA_WORKFLOW_DESCRIPTION,
    DependencySpec,
    render_step_prompt,
)
from registry_pkgs.workflows.serialization import content_to_str

_MAX_DEPENDENCY_CONTENT_CHARS = 8000

_MediaSummaryFields = tuple[tuple[str, int | None], ...]
_IMAGE_SUMMARY_FIELDS: _MediaSummaryFields = (("mime_type", None), ("format", None), ("alt_text", 500))
_VIDEO_SUMMARY_FIELDS: _MediaSummaryFields = (("mime_type", None), ("format", None), ("duration", None))
_AUDIO_SUMMARY_FIELDS: _MediaSummaryFields = (
    ("mime_type", None),
    ("format", None),
    ("duration", None),
    ("transcript", 500),
)


def _truncate(value: str, *, limit: int = _MAX_DEPENDENCY_CONTENT_CHARS) -> str:
    """Cap prompt-bound text at *limit* chars, appending a note about what was cut."""
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n[truncated: {len(value)} chars total, showing first {limit}]"


def _safe_file_preview(file_content: Any, mime_type: str | None) -> str | None:
    """Return decodable json/text file content for prompt preview; None for anything binary."""
    if file_content is None:
        return None
    if isinstance(file_content, bytes):
        if mime_type == "application/json" or (mime_type or "").startswith("text/"):
            try:
                return file_content.decode("utf-8")
            except UnicodeDecodeError:
                return None
        return None
    if isinstance(file_content, str) and (mime_type == "application/json" or (mime_type or "").startswith("text/")):
        return file_content
    return None


def _media_summary_lines(items: list[Any], fallback_label: str, fields: _MediaSummaryFields) -> str:
    """Render one '- label, attr=value, ...' bullet per media item from its metadata fields."""
    lines = []
    for item in items:
        details = [item.id or item.mime_type or fallback_label]
        for attr, limit in fields:
            value = getattr(item, attr, None)
            if value is None or value == "":
                continue
            details.append(f"{attr}={_truncate(str(value), limit=limit) if limit else value}")
        lines.append(f"- {', '.join(details)}")
    return "\n".join(lines)


def step_output_to_prompt_text(output: StepOutput) -> str:
    """Render a StepOutput into a compact, prompt-safe text summary (metadata only, never bytes)."""
    sections: list[str] = []
    if output.content is not None:
        sections.append(f"Text output:\n{_truncate(content_to_str(output.content))}")

    if output.images:
        sections.append("Images:\n" + _media_summary_lines(output.images, "image", _IMAGE_SUMMARY_FIELDS))

    if output.videos:
        sections.append("Videos:\n" + _media_summary_lines(output.videos, "video", _VIDEO_SUMMARY_FIELDS))

    if output.audio:
        sections.append("Audio:\n" + _media_summary_lines(output.audio, "audio", _AUDIO_SUMMARY_FIELDS))

    if output.files:
        lines = []
        for file in output.files:
            label = file.filename or file.name or file.id or file.url or "file"
            details = [str(label)]
            if file.mime_type:
                details.append(f"mime_type={file.mime_type}")
            if file.file_type:
                details.append(f"file_type={file.file_type}")
            if file.size is not None:
                details.append(f"size={file.size}")
            lines.append(f"- {', '.join(details)}")
            preview = _safe_file_preview(file.content, file.mime_type)
            if preview:
                lines.append(f"  Preview:\n{_truncate(preview)}")
        sections.append("Files:\n" + "\n".join(lines))

    if not output.success:
        sections.append(f"Success: false\nError: {output.error or '(no error detail)'}")
    elif output.error:
        sections.append(f"Error: {output.error}")

    return "\n\n".join(sections)


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
    dependencies: list[DependencySpec] = []
    for name in dependency_node_names:
        content = None
        if name in previous:
            summary = step_output_to_prompt_text(previous[name])
            content = summary or None
        dependencies.append(
            DependencySpec(
                name=name,
                objective=dependency_objectives.get(name, ""),
                content=content,
            )
        )

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
