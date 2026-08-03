"""Validate A2A result -> StepOutput conversion across all payload shapes (AS-1725).

Runs a battery of offline checks (no DB / network needed) that feed realistic
A2A ``Message`` / ``Task`` payloads through ``_a2a_result_to_step_output`` and
``build_prompt``, asserting the resulting ``StepOutput`` and dependency-prompt
summary match the AS-1725 requirements:

  * TextPart      -> StepOutput.content
  * image  FilePart -> StepOutput.images
  * video  FilePart -> StepOutput.videos
  * audio  FilePart -> StepOutput.audio
  * other  FilePart -> StepOutput.files
  * DataPart        -> JSON File in StepOutput.files
  * FileWithBytes and FileWithUri both supported
  * success / error passthrough
  * prompt summary: media metadata shown, previews truncated, no base64 blobs,
    upstream code fences don't break prompt structure

Usage:
    uv run python scripts/validate_step_output.py
"""

from __future__ import annotations

import base64
import json
import sys
from collections.abc import Callable

from a2a.types import Message, Task
from agno.media import File, Image
from agno.workflow import StepInput, StepOutput

from registry_pkgs.workflows.a2a_client import A2ACallResult
from registry_pkgs.workflows.a2a_executor import _a2a_result_to_step_output
from registry_pkgs.workflows.helpers import build_prompt, step_output_to_prompt_text
from registry_pkgs.workflows.prompt import (
    ADDITIONAL_DATA_DEPENDENCY_NODE_NAMES,
    ADDITIONAL_DATA_DEPENDENCY_OBJECTIVES,
    ADDITIONAL_DATA_STEP_OBJECTIVE,
)

_JPEG_BYTES = b"\xff\xd8\xff\xe0fake-jpeg-data"
_JPEG_B64 = base64.b64encode(_JPEG_BYTES).decode()
_PDF_B64 = base64.b64encode(b"%PDF-1.7 fake").decode()
_MP4_B64 = base64.b64encode(b"\x00\x00\x00 ftypisom fake").decode()
_WAV_B64 = base64.b64encode(b"RIFFfakeWAVE").decode()

_failures: list[str] = []


def _check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
        return
    print(f"  FAIL  {label}  {detail}")
    _failures.append(label)


def _message_result(parts: list[dict]) -> A2ACallResult:
    message = Message.model_validate(
        {
            "kind": "message",
            "messageId": "m-1",
            "role": "agent",
            "parts": parts,
        }
    )
    return A2ACallResult(success=True, message=message)


def _task_result(
    *,
    status_text: str | None = None,
    artifacts: list[dict] | None = None,
) -> A2ACallResult:
    status: dict = {"state": "completed"}
    if status_text is not None:
        status["message"] = {
            "kind": "message",
            "messageId": "s-1",
            "role": "agent",
            "parts": [{"kind": "text", "text": status_text}],
        }
    task = Task.model_validate(
        {
            "kind": "task",
            "id": "t-1",
            "contextId": "c-1",
            "status": status,
            "artifacts": artifacts or [],
        }
    )
    return A2ACallResult(success=True, task=task)


def _file_part_bytes(b64: str, mime: str, name: str) -> dict:
    return {"kind": "file", "file": {"bytes": b64, "mimeType": mime, "name": name}}


def _file_part_uri(uri: str, mime: str, name: str) -> dict:
    return {"kind": "file", "file": {"uri": uri, "mimeType": mime, "name": name}}


def check_message_text_only() -> None:
    print("[1] Message with TextPart only")
    out = _a2a_result_to_step_output(_message_result([{"kind": "text", "text": "hello world"}]))
    _check("content is the text", out.content == "hello world", f"got {out.content!r}")
    _check("no media fields set", not any([out.images, out.videos, out.audio, out.files]))
    _check("success passthrough", out.success is True)


def check_message_all_part_kinds() -> None:
    print("[2] Message with TextPart + image/video/audio/pdf FilePart + DataPart")
    out = _a2a_result_to_step_output(
        _message_result(
            [
                {"kind": "text", "text": "multi-modal reply"},
                _file_part_bytes(_JPEG_B64, "image/jpeg", "pic.jpg"),
                _file_part_bytes(_MP4_B64, "video/mp4", "clip.mp4"),
                _file_part_bytes(_WAV_B64, "audio/wav", "voice.wav"),
                _file_part_bytes(_PDF_B64, "application/pdf", "doc.pdf"),
                {"kind": "data", "data": {"score": 0.9, "labels": ["a", "b"]}},
            ]
        )
    )
    _check("content is the text", out.content == "multi-modal reply", f"got {out.content!r}")
    _check("1 image, decoded bytes", bool(out.images) and out.images[0].content == _JPEG_BYTES)
    _check("1 video", bool(out.videos) and out.videos[0].mime_type == "video/mp4")
    _check("1 audio", bool(out.audio) and out.audio[0].mime_type == "audio/wav")
    files = out.files or []
    _check("2 files (pdf + DataPart json)", len(files) == 2, f"got {[f.filename for f in files]}")
    pdf = next((f for f in files if f.filename == "doc.pdf"), None)
    _check("pdf kept as File with mime", pdf is not None and pdf.mime_type == "application/pdf")
    data_file = next((f for f in files if f.mime_type == "application/json"), None)
    _check(
        "DataPart JSON round-trips",
        data_file is not None and json.loads(data_file.content) == {"score": 0.9, "labels": ["a", "b"]},
    )


def check_task_spec_sample() -> None:
    print("[3] Task: status.message + image artifact + metadata DataPart (spec sample shape)")
    out = _a2a_result_to_step_output(
        _task_result(
            status_text="completed",
            artifacts=[
                {
                    "artifactId": "image-0",
                    "name": "Cover Image",
                    "parts": [_file_part_bytes(_JPEG_B64, "image/jpeg", "img_1.jpg")],
                },
                {
                    "artifactId": "metadata",
                    "name": "image_metadata",
                    "parts": [{"kind": "data", "data": {"value": [{"media_type": "image/jpeg"}]}}],
                },
            ],
        )
    )
    _check("content from status.message", out.content == "completed", f"got {out.content!r}")
    _check("image extracted from artifact", bool(out.images) and out.images[0].id == "img_1.jpg")
    files = out.files or []
    _check(
        "DataPart named after artifact",
        len(files) == 1 and files[0].filename == "image_metadata-data-1.json",
        f"got {[f.filename for f in files]}",
    )


def check_task_text_artifacts_join() -> None:
    print("[4] Task: status text + named text artifacts join into content")
    out = _a2a_result_to_step_output(
        _task_result(
            status_text="done",
            artifacts=[
                {"artifactId": "a1", "name": "report", "parts": [{"kind": "text", "text": "body text"}]},
            ],
        )
    )
    _check("status + artifact text both present", out.content == "done\n\n[report]\nbody text", f"got {out.content!r}")


def check_uri_files() -> None:
    print("[5] FileWithUri variants map to url-based media")
    out = _a2a_result_to_step_output(
        _message_result(
            [
                _file_part_uri("https://cdn.example.com/pic.png", "image/png", "pic.png"),
                _file_part_uri("https://cdn.example.com/doc.csv", "text/csv", "doc.csv"),
            ]
        )
    )
    _check(
        "image via url, no bytes",
        bool(out.images) and out.images[0].url == "https://cdn.example.com/pic.png" and out.images[0].content is None,
    )
    files = out.files or []
    _check("csv file via url", len(files) == 1 and files[0].url == "https://cdn.example.com/doc.csv")
    _check("empty text -> content is None", out.content is None, f"got {out.content!r}")


def check_unsupported_mime_not_dropped() -> None:
    print("[6] Non-agno MIME type is kept, not silently dropped")
    out = _a2a_result_to_step_output(
        _message_result([_file_part_bytes(_PDF_B64, "application/x-custom-binary", "blob.bin")])
    )
    files = out.files or []
    _check("file survives unknown mime", len(files) == 1, f"got {len(files)} files")
    _check("original mime kept in file_type", bool(files) and files[0].file_type == "application/x-custom-binary")
    _check("agno mime_type left unset", bool(files) and files[0].mime_type is None)


def check_failure_passthrough() -> None:
    print("[7] Failed call: success/error passthrough")
    out = _a2a_result_to_step_output(A2ACallResult(success=False, error="agent timed out"))
    _check("success False", out.success is False)
    _check("error preserved", out.error == "agent timed out")
    _check("content None on empty result", out.content is None)


def _prompt_for(prev: StepOutput, objective: str = "consume upstream output") -> str:
    step_input = StepInput(
        input="trigger",
        previous_step_outputs={"Upstream": prev},
        additional_data={
            ADDITIONAL_DATA_STEP_OBJECTIVE: objective,
            ADDITIONAL_DATA_DEPENDENCY_NODE_NAMES: ["Upstream"],
            ADDITIONAL_DATA_DEPENDENCY_OBJECTIVES: {"Upstream": "produce data"},
        },
    )
    return build_prompt(step_input)


def check_prompt_media_summary() -> None:
    print("[8] Prompt summary: media metadata included, image bytes excluded")
    prev = StepOutput(
        step_name="Upstream",
        content="see attached",
        images=[Image(content=_JPEG_BYTES, id="pic.jpg", mime_type="image/jpeg")],
        files=[File(content='{"k": 1}', mime_type="application/json", filename="meta.json", name="meta.json")],
        success=True,
    )
    prompt = _prompt_for(prev)
    _check("text output present", "see attached" in prompt)
    _check("image metadata line present", "pic.jpg" in prompt and "image/jpeg" in prompt)
    _check("json preview present", '{"k": 1}' in prompt)
    _check("no raw image bytes / base64 in prompt", _JPEG_B64 not in prompt and str(_JPEG_BYTES) not in prompt)


def check_prompt_truncation() -> None:
    print("[9] Prompt summary: long text and JSON previews are truncated")
    long_text = "T" * 20_000
    long_json = '{"data": "' + "J" * 20_000 + '"}'
    prev = StepOutput(
        step_name="Upstream",
        content=long_text,
        files=[File(content=long_json, mime_type="application/json", filename="big.json", name="big.json")],
        success=True,
    )
    summary = step_output_to_prompt_text(prev)
    _check("content truncated", "T" * 8001 not in summary and "[truncated:" in summary)
    _check("json preview truncated", "J" * 8001 not in summary)
    _check("summary bounded", len(summary) < 20_000, f"len={len(summary)}")


def check_prompt_code_fence_safety() -> None:
    print("[10] Prompt summary: upstream code fences don't break prompt structure")
    prev = StepOutput(
        step_name="Upstream",
        content="```python\nprint('hi')\n```",
        success=True,
    )
    prompt = _prompt_for(prev)
    _check("fence content rendered", "print('hi')" in prompt)
    _check("fence carried as indented block", "  ```python" in prompt)
    _check("prompt sections intact", prompt.startswith("**IMPORTANT:") and "Current Step Inputs:" in prompt)


def check_prompt_failed_dependency() -> None:
    print("[11] Prompt summary: failed upstream shows error, not silence")
    prev = StepOutput(step_name="Upstream", content=None, success=False, error="boom")
    prompt = _prompt_for(prev)
    _check("failure surfaced", "Success: false" in prompt and "boom" in prompt)


def main() -> int:
    checks: list[Callable[[], None]] = [
        check_message_text_only,
        check_message_all_part_kinds,
        check_task_spec_sample,
        check_task_text_artifacts_join,
        check_uri_files,
        check_unsupported_mime_not_dropped,
        check_failure_passthrough,
        check_prompt_media_summary,
        check_prompt_truncation,
        check_prompt_code_fence_safety,
        check_prompt_failed_dependency,
    ]
    for check in checks:
        check()
        print()

    if _failures:
        print(f"RESULT: {len(_failures)} check(s) FAILED: {_failures}")
        return 1
    print("RESULT: all StepOutput validation checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
