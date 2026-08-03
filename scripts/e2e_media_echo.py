"""End-to-end test: invoke the media-echo A2A agent and validate StepOutput parsing.

Tests BOTH response modes (Message and Task) with ALL media types:
  - Image (PNG, JPEG) → StepOutput.images
  - Video (MP4)       → StepOutput.videos
  - Audio (WAV)       → StepOutput.audio
  - File  (PDF, CSV)  → StepOutput.files
  - DataPart (JSON)   → StepOutput.files (as .json File)
  - TextPart          → StepOutput.content

Usage:
    BEARER_TOKEN=<token> uv run python scripts/e2e_media_echo.py <agent_arn>
    BEARER_TOKEN=<token> uv run python scripts/e2e_media_echo.py <agent_arn> message   # message mode only
    BEARER_TOKEN=<token> uv run python scripts/e2e_media_echo.py <agent_arn> task      # task mode only
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from urllib.parse import quote

import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.client.base_client import BaseClient
from a2a.client.middleware import ClientCallContext
from a2a.types import Message, Part, Role, Task, TextPart

from registry_pkgs.workflows.a2a_client import A2ACallResult, _consume_stream, _result_from_task
from registry_pkgs.workflows.a2a_executor import _a2a_result_to_step_output
from registry_pkgs.workflows.helpers import step_output_to_prompt_text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_TIMEOUT = 120
_failures: list[str] = []


def _check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
        return
    print(f"  FAIL  {label}  {detail}")
    _failures.append(label)


async def _invoke(hc: httpx.AsyncClient, agent_card, text: str) -> Message | Task | None:
    """Send a text message and return the raw A2A response."""
    session_id = str(uuid.uuid4())
    headers = {
        "Authorization": hc.headers["Authorization"],
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
    }
    context = ClientCallContext(state={"http_kwargs": {"headers": headers, "timeout": _TIMEOUT}})
    config = ClientConfig(httpx_client=hc, streaming=False)
    client: BaseClient = ClientFactory(config).create(agent_card)  # type: ignore[assignment]

    msg = Message(
        kind="message",
        role=Role.user,
        parts=[Part(root=TextPart(kind="text", text=text))],
        message_id=uuid.uuid4().hex,
    )
    return await _consume_stream(client, msg, context=context)


def _validate_common(step_out, mode: str) -> None:
    """Checks shared by both modes."""
    _check(f"[{mode}] success", step_out.success is True, f"got {step_out.success}")
    _check(f"[{mode}] content has text", bool(step_out.content), f"got {step_out.content!r}")

    # images
    _check(f"[{mode}] images populated", bool(step_out.images), "images is empty")
    if step_out.images:
        mimes = {img.mime_type for img in step_out.images}
        _check(f"[{mode}] has PNG image", "image/png" in mimes, f"got {mimes}")
        _check(f"[{mode}] has JPEG image", "image/jpeg" in mimes, f"got {mimes}")
        for img in step_out.images:
            _check(
                f"[{mode}] image {img.id} has bytes",
                img.content is not None and len(img.content) > 0,
                "no image bytes",
            )
            print(f"    image: id={img.id}, mime={img.mime_type}, bytes={len(img.content or b'')}")

    # videos
    _check(f"[{mode}] videos populated", bool(step_out.videos), "videos is empty")
    if step_out.videos:
        for v in step_out.videos:
            _check(f"[{mode}] video mime", v.mime_type == "video/mp4", f"got {v.mime_type!r}")
            print(f"    video: id={v.id}, mime={v.mime_type}")

    # audio
    _check(f"[{mode}] audio populated", bool(step_out.audio), "audio is empty")
    if step_out.audio:
        for a in step_out.audio:
            _check(f"[{mode}] audio mime", a.mime_type == "audio/wav", f"got {a.mime_type!r}")
            print(f"    audio: id={a.id}, mime={a.mime_type}")

    # files (PDF, CSV, DataPart JSON)
    _check(f"[{mode}] files populated", bool(step_out.files), "files is empty")
    if step_out.files:
        file_mimes = {f.mime_type for f in step_out.files}
        file_types = {f.file_type for f in step_out.files if f.file_type}
        all_types = file_mimes | file_types
        _check(f"[{mode}] has PDF", "application/pdf" in all_types, f"got {all_types}")
        _check(f"[{mode}] has CSV", "text/csv" in all_types, f"got {all_types}")
        _check(f"[{mode}] has JSON (DataPart)", "application/json" in all_types, f"got {all_types}")
        for f in step_out.files:
            print(f"    file: {f.filename}, mime={f.mime_type}, file_type={f.file_type}, len={len(f.content or '')}")
            if f.mime_type == "application/json" and f.content:
                data = json.loads(f.content)
                _check(f"[{mode}] DataPart has media_summary", "media_summary" in data, f"keys={list(data.keys())}")

    # prompt rendering
    prompt = step_output_to_prompt_text(step_out)
    _check(f"[{mode}] prompt has image metadata", "image/png" in prompt, "missing from prompt")
    _check(f"[{mode}] no base64 leak in prompt", "iVBORw0KGgo" not in prompt, "base64 leaked")
    print(f"    prompt length: {len(prompt)} chars")


async def _test_message_mode(hc: httpx.AsyncClient, agent_card) -> None:
    print("\n" + "=" * 60)
    print("=== MESSAGE MODE ===")
    print("Sending 'message' to trigger Message response...\n")

    outcome = await _invoke(hc, agent_card, "message")
    if outcome is None:
        _check("[message] got response", False, "agent returned no events")
        return

    _check("[message] response is Message", isinstance(outcome, Message), f"got {type(outcome).__name__}")
    if not isinstance(outcome, Message):
        return

    result = A2ACallResult(message=outcome, success=True)
    step_out = _a2a_result_to_step_output(result)
    _validate_common(step_out, "message")


async def _test_task_mode(hc: httpx.AsyncClient, agent_card) -> None:
    print("\n" + "=" * 60)
    print("=== TASK MODE ===")
    print("Sending 'hello media test' to trigger Task response...\n")

    outcome = await _invoke(hc, agent_card, "hello media test")
    if outcome is None:
        _check("[task] got response", False, "agent returned no events")
        return

    _check("[task] response is Task", isinstance(outcome, Task), f"got {type(outcome).__name__}")
    if not isinstance(outcome, Task):
        return

    result = _result_from_task(outcome)
    _check("[task] result.success", result.success is True, f"error={result.error}")

    step_out = _a2a_result_to_step_output(result)
    _validate_common(step_out, "task")

    if outcome.artifacts:
        print(f"    artifacts: {[a.name for a in outcome.artifacts]}")


async def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    agent_arn = sys.argv[1]
    mode_filter = sys.argv[2].lower() if len(sys.argv) > 2 else "both"
    bearer_token = os.environ.get("BEARER_TOKEN")
    if not bearer_token:
        print("Error: BEARER_TOKEN env var not set")
        return 1

    escaped = quote(agent_arn, safe="")
    base_url = f"https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/{escaped}/invocations"

    print(f"Agent ARN: {agent_arn}")
    print(f"Mode: {mode_filter}\n")

    async with httpx.AsyncClient(timeout=_TIMEOUT, headers={"Authorization": f"Bearer {bearer_token}"}) as hc:
        resolver = A2ACardResolver(httpx_client=hc, base_url=base_url)
        agent_card = await resolver.get_agent_card()
        print(f"Agent: {agent_card.name}")
        print(f"Description: {agent_card.description}")

        if mode_filter in ("message", "both"):
            await _test_message_mode(hc, agent_card)

        if mode_filter in ("task", "both"):
            await _test_task_mode(hc, agent_card)

    print("\n" + "=" * 60)
    if _failures:
        print(f"RESULT: {len(_failures)} check(s) FAILED:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"RESULT: all e2e checks passed ({mode_filter} mode)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
