from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from a2a.types import FileWithBytes, FileWithUri, Message, Task
from a2a.utils.parts import get_data_parts, get_file_parts
from agno.agent import Agent
from agno.media import Audio, File, Image, Video
from agno.models.base import Model
from agno.workflow import StepInput, StepOutput
from agno.workflow.step import StepExecutor

from registry_pkgs.core.config import JwtSigningConfig
from registry_pkgs.models.a2a_agent import A2AAgent
from registry_pkgs.workflows.a2a_client import (
    A2ACallResult,
    HeadersProvider,
    call_a2a,
    raise_if_iam_unsupported,
)
from registry_pkgs.workflows.helpers import build_prompt

logger = logging.getLogger(__name__)


def _safe_file_mime_type(mime_type: str | None) -> str | None:
    """Return the MIME type if agno File accepts it, else None (original is kept in file_type)."""
    media_type = (mime_type or "").split(";", 1)[0].strip().lower()
    return media_type if media_type in File.valid_mime_types() else None


def _file_payload_to_media(payload: FileWithBytes | FileWithUri) -> Audio | File | Image | Video | None:
    """Convert one A2A file payload into Image/Video/Audio/File by MIME prefix (bytes→from_base64, uri→url)."""
    mime_type = payload.mime_type
    media_type = (mime_type or "").split(";", 1)[0].strip().lower()
    safe_mime_type = _safe_file_mime_type(mime_type)
    original_file_type = media_type if media_type and safe_mime_type is None else None

    if isinstance(payload, FileWithBytes):
        if media_type.startswith("image/"):
            return Image.from_base64(payload.bytes, id=payload.name, mime_type=mime_type)
        if media_type.startswith("video/"):
            return Video.from_base64(payload.bytes, id=payload.name, mime_type=mime_type)
        if media_type.startswith("audio/"):
            return Audio.from_base64(payload.bytes, id=payload.name, mime_type=mime_type)
        file = File.from_base64(
            payload.bytes,
            id=payload.name,
            mime_type=safe_mime_type,
            filename=payload.name,
            name=payload.name,
        )
        file.file_type = original_file_type
        return file

    if isinstance(payload, FileWithUri):
        if media_type.startswith("image/"):
            return Image(url=str(payload.uri), id=payload.name, mime_type=mime_type)
        if media_type.startswith("video/"):
            return Video(url=str(payload.uri), id=payload.name, mime_type=mime_type)
        if media_type.startswith("audio/"):
            return Audio(url=str(payload.uri), id=payload.name, mime_type=mime_type)
        return File(
            url=str(payload.uri),
            id=payload.name,
            mime_type=safe_mime_type,
            file_type=original_file_type,
            filename=payload.name,
            name=payload.name,
        )

    logger.warning("Skipping unsupported A2A file payload type: %s", type(payload).__name__)
    return None


def _append_parts_media(
    parts: list[Any],
    *,
    files: list[File],
    images: list[Image],
    videos: list[Video],
    audio: list[Audio],
    data_prefix: str,
) -> None:
    """Sort a parts list into the media buckets; DataParts become '{data_prefix}-data-{n}.json' JSON Files."""
    for payload in get_file_parts(parts):
        media = _file_payload_to_media(payload)
        if isinstance(media, Image):
            images.append(media)
        elif isinstance(media, Video):
            videos.append(media)
        elif isinstance(media, Audio):
            audio.append(media)
        elif isinstance(media, File):
            files.append(media)

    for data_index, data in enumerate(get_data_parts(parts), start=1):
        filename = f"{data_prefix}-data-{data_index}.json"
        files.append(
            File(
                content=json.dumps(data, ensure_ascii=False, default=str),
                mime_type="application/json",
                filename=filename,
                name=filename,
            )
        )


def _append_message_media(
    message: Message,
    *,
    files: list[File],
    images: list[Image],
    videos: list[Video],
    audio: list[Audio],
    data_prefix: str,
) -> None:
    """Collect media from a Message's parts into the shared buckets."""
    _append_parts_media(
        message.parts or [],
        files=files,
        images=images,
        videos=videos,
        audio=audio,
        data_prefix=data_prefix,
    )


def _append_task_media(
    task: Task,
    *,
    files: list[File],
    images: list[Image],
    videos: list[Video],
    audio: list[Audio],
) -> None:
    """Collect media from a Task's status message and artifacts into the shared buckets."""
    if task.status.message is not None:
        _append_message_media(
            task.status.message,
            files=files,
            images=images,
            videos=videos,
            audio=audio,
            data_prefix="status-message",
        )
    for artifact_index, artifact in enumerate(task.artifacts or [], start=1):
        data_prefix = artifact.name or artifact.artifact_id or f"artifact-{artifact_index}"
        _append_parts_media(
            artifact.parts or [],
            files=files,
            images=images,
            videos=videos,
            audio=audio,
            data_prefix=data_prefix,
        )


def _a2a_result_to_step_output(result: A2ACallResult) -> StepOutput:
    """Convert A2A text and artifacts into Agno's StepOutput media model."""
    files: list[File] = []
    images: list[Image] = []
    videos: list[Video] = []
    audio: list[Audio] = []

    if result.message is not None:
        _append_message_media(
            result.message,
            files=files,
            images=images,
            videos=videos,
            audio=audio,
            data_prefix="message",
        )
    elif result.task is not None:
        _append_task_media(result.task, files=files, images=images, videos=videos, audio=audio)

    text = result.render_text()
    return StepOutput(
        content=text if text else None,
        images=images or None,
        videos=videos or None,
        audio=audio or None,
        files=files or None,
        success=result.success,
        error=result.error,
    )


def make_a2a_executor(
    agent: A2AAgent,
    *,
    jwt_config: JwtSigningConfig,
    httpx_client: httpx.AsyncClient | None = None,
    headers_provider: HeadersProvider | None = None,
) -> StepExecutor:
    """Wrap an A2A agent as a workflow StepExecutor via a direct A2A call.

    Args:
        agent:        Beanie A2AAgent document.
        jwt_config:   JWT signing config for service-to-agent auth.
        httpx_client: Optional shared httpx pool. When None, call_a2a creates
                      one per call (suitable for tests / one-off scripts).
        headers_provider: Optional shared headers provider.
    """

    async def executor(step_input: StepInput, session_state: dict[str, Any] | None = None) -> StepOutput:
        raise_if_iam_unsupported(agent)
        result = await call_a2a(
            agent,
            build_prompt(step_input),
            jwt_config=jwt_config,
            httpx_client=httpx_client,
            headers_provider=headers_provider,
        )
        return _a2a_result_to_step_output(result)

    executor.__name__ = f"{agent.path}_a2a_executor"
    return executor


def make_a2a_pool_executor(
    node_name: str,
    pool_keys: list[str],
    *,
    selector_llm: Model,
    jwt_config: JwtSigningConfig,
    accessible_agent_ids: set[str] | None,
    httpx_client: httpx.AsyncClient | None = None,
    headers_provider: HeadersProvider | None = None,
) -> StepExecutor:
    """Build a StepExecutor that picks the best A2A agent from a pool at runtime.

    Selection is performed by an LLM on first call, then cached in
    ``session_state`` so retries reuse the same agent without re-running the
    LLM.  Each call generates a fresh short-lived JWT for the selected agent
    using the supplied ``jwt_config``.

    Args:
        node_name:            Workflow node name — used for logging and cache keys.
        pool_keys:            Agent path segments (without leading ``/``) that form the pool.
        selector_llm:         Model used for LLM-based agent selection.
        jwt_config:           JWT signing config (private key, issuer, kid, audience).
        accessible_agent_ids: ACL filter — set of A2AAgent ID strings the caller
                              is authorized to invoke. ``None`` = unrestricted.
                              Pool members outside this set are excluded BEFORE
                              LLM selection runs.
        httpx_client:         Optional shared httpx pool forwarded to ``call_a2a``.
                              When ``None``, ``call_a2a`` builds a fresh pool per
                              invocation (slower but isolated; fine for tests).
        headers_provider:     Optional shared headers provider.

    Returns:
        An async callable that accepts ``(StepInput, session_state)`` and
        returns a ``StepOutput``.
    """
    selector_agent = Agent(
        name=f"A2A Pool Selector [{node_name}]",
        model=selector_llm,
        instructions=[
            "You are given a task and a list of agents with their capabilities.",
            "Pick the single best agent for the task.",
            "Respond with ONLY the agent path slug (e.g. 'deep-intel'), nothing else.",
        ],
    )

    async def executor(step_input: StepInput, session_state: dict[str, Any] | None = None) -> StepOutput:
        task = build_prompt(step_input)
        state = session_state if session_state is not None else {}
        cache_key = f"a2a_target_{node_name}"

        selected_path: str | None = state.get(cache_key)
        selected_agent: A2AAgent | None = None

        if selected_path is None:
            paths = [k.lstrip("/") for k in pool_keys]
            agents = await A2AAgent.find(
                {"path": {"$in": paths}, "config.enabled": True},
            ).to_list()

            if accessible_agent_ids is not None:
                agents = [a for a in agents if str(a.id) in accessible_agent_ids]

            if not agents:
                return StepOutput(
                    content=f"No accessible enabled A2A agents for pool {pool_keys!r}",
                    success=False,
                    error="pool resolution failed: no accessible enabled agents",
                )

            selected_agent = await _select_agent_with_llm(agents, task, selector_agent)
            selected_path = selected_agent.path
            # Single key serves two purposes:
            # 1. Retry guard — skip LLM selection on retry, reuse the same agent.
            # 2. Persistence — WorkflowRunSyncer reads this to populate NodeRun.selected_a2a_key.
            state[cache_key] = selected_path
            logger.info("pool %r → selected agent %r", node_name, selected_path)
        else:
            selected_agent = await A2AAgent.find_one({"path": selected_path, "config.enabled": True})
            if selected_agent is None:
                return StepOutput(
                    content=f"Selected agent {selected_path!r} is no longer enabled",
                    success=False,
                    error=f"pool retry failed: agent {selected_path!r} not found or disabled",
                )
            if accessible_agent_ids is not None and str(selected_agent.id) not in accessible_agent_ids:
                return StepOutput(
                    content=f"Selected agent {selected_path!r} no longer accessible",
                    success=False,
                    error=f"pool retry failed: agent {selected_path!r} not in accessible set",
                )

        raise_if_iam_unsupported(selected_agent)
        result = await call_a2a(
            selected_agent,
            task,
            jwt_config=jwt_config,
            httpx_client=httpx_client,
            headers_provider=headers_provider,
        )
        return _a2a_result_to_step_output(result)

    executor.__name__ = f"{node_name}_pool_executor"
    return executor


async def _select_agent_with_llm(
    agents: list[A2AAgent],
    task_description: str,
    selector_agent: Agent,
) -> A2AAgent:
    """Use an LLM to pick the best-fit agent from the pool."""
    summaries = [
        f"Path: {agent.path}\n"
        f"Name: {agent.card.name}\n"
        f"Description: {agent.card.description or ''}\n"
        f"Skills: {', '.join(f'{s.name}: {s.description}' for s in (agent.card.skills or []) if s.name) or '(none)'}"
        for agent in agents
    ]

    prompt = (
        f"Task: {task_description}\n\n"
        f"Available agents:\n\n" + "\n---\n".join(summaries) + "\n\nWhich agent path is the best fit for this task? "
        "Reply with ONLY the agent path slug (e.g. agent-name), nothing else."
    )

    result = await selector_agent.arun(prompt)
    chosen_path = (result.content or "").strip().lstrip("/")

    agent_by_path = {a.path: a for a in agents}
    selected = agent_by_path.get(chosen_path)
    if selected is None:
        raise ValueError(f"LLM selector returned unknown path {chosen_path!r}; pool: {list(agent_by_path)}")
    logger.info("pool selector chose %r", selected.path)
    return selected
