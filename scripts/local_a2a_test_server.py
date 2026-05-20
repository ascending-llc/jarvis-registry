"""Local A2A REST test server with 4 selectable response shapes.

Purpose:
    Exercise every branch of ``registry_pkgs.workflows.a2a_client.call_a2a``
    (Message reply / non-stream Task / polling Task / streamed Task) without
    AgentCore, JWT, or network noise.

Mode selection — picks the first matching keyword in the user input:
    "message"  → enqueue a ``Message`` (no Task created)         → Message branch
    "task"     → enqueue Task submitted + artifact + completed   → non-stream Task branch
    "polling"  → enqueue Task working; complete it ~3s later via
                 direct TaskStore mutation                       → polling branch
    anything   → stream multiple artifact chunks then completed  → streamed Task branch

Run:
    uv run python scripts/local_a2a_test_server.py

Then test with the existing harness:
    uv run python scripts/test_agent_direct.py --path /local-http-json-agent "message"
    uv run python scripts/test_agent_direct.py --path /local-http-json-agent "task"
    uv run python scripts/test_agent_direct.py --path /local-http-json-agent "polling"
    uv run python scripts/test_agent_direct.py --path /local-http-json-agent "stream"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Final

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps.rest.fastapi_app import A2ARESTFastAPIApplication
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers.default_request_handler import DefaultRequestHandler
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    Artifact,
    Message,
    Part,
    Role,
    TaskState,
    TaskStatus,
    TextPart,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)
logger = logging.getLogger(__name__)


_DEFAULT_HOST: Final[str] = "127.0.0.1"
_DEFAULT_PORT: Final[int] = 9000
_POLLING_COMPLETION_DELAY_SECONDS: Final[float] = 3.0
_STREAM_CHUNK_DELAY_SECONDS: Final[float] = 0.15
_STREAM_CHUNKS: Final[tuple[str, ...]] = (
    "Hello ",
    "from ",
    "the ",
    "local ",
    "A2A ",
    "test ",
    "server.",
)


def _build_agent_card() -> AgentCard:
    """Build the agent card served at /.well-known/agent-card.json."""
    return AgentCard(
        name="Local A2A Test Agent",
        description=(
            "Local fixture for exercising call_a2a's Message / non-stream Task / "
            "polling Task / streamed Task return paths."
        ),
        url=f"http://{_DEFAULT_HOST}:{_DEFAULT_PORT}/",
        version="0.0.1",
        protocol_version="0.3.0",
        preferred_transport="HTTP+JSON",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[],
    )


def _make_text_part(text: str) -> Part:
    return Part(root=TextPart(kind="text", text=text))


def _build_message_reply(context: RequestContext, text: str) -> Message:
    return Message(
        kind="message",
        role=Role.agent,
        parts=[_make_text_part(text)],
        message_id=uuid.uuid4().hex,
        task_id=context.task_id,
        context_id=context.context_id,
    )


def _select_mode(user_input: str) -> str:
    """Map the user input to one of: message | task | polling | stream."""
    lowered = user_input.lower()
    for keyword in ("message", "polling", "task"):
        if keyword in lowered:
            return keyword
    return "stream"


class LocalTestExecutor(AgentExecutor):
    """AgentExecutor that switches response shape based on user input keyword.

    Holds a reference to the shared TaskStore so the polling mode can mutate
    the persisted task after ``execute`` has returned.
    """

    def __init__(self, task_store: InMemoryTaskStore) -> None:
        self._task_store = task_store
        self._background_tasks: set[asyncio.Task] = set()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_input = context.get_user_input().strip()
        mode = _select_mode(user_input)
        logger.info("mode=%s user_input=%r task_id=%s", mode, user_input, context.task_id)

        if mode == "message":
            await self._run_message_mode(context, event_queue)
            return

        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.submit()

        if mode == "task":
            await self._run_nonstream_task_mode(updater)
        elif mode == "polling":
            await self._run_polling_mode(context, updater)
        else:
            await self._run_streaming_mode(updater)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.cancel()

    async def _run_message_mode(self, context: RequestContext, event_queue: EventQueue) -> None:
        reply = _build_message_reply(context, "pong: this is a Message reply (no Task created).")
        await event_queue.enqueue_event(reply)

    async def _run_nonstream_task_mode(self, updater: TaskUpdater) -> None:
        await updater.start_work()
        await updater.add_artifact(
            parts=[_make_text_part("Non-stream task completed in a single shot.")],
            name="result",
            last_chunk=True,
        )
        await updater.complete()

    async def _run_streaming_mode(self, updater: TaskUpdater) -> None:
        await updater.start_work()
        artifact_id = uuid.uuid4().hex
        for index, chunk in enumerate(_STREAM_CHUNKS):
            await updater.add_artifact(
                parts=[_make_text_part(chunk)],
                artifact_id=artifact_id,
                name="result",
                append=index > 0,
                last_chunk=index == len(_STREAM_CHUNKS) - 1,
            )
            await asyncio.sleep(_STREAM_CHUNK_DELAY_SECONDS)
        await updater.complete()

    async def _run_polling_mode(self, context: RequestContext, updater: TaskUpdater) -> None:
        # Leave the SSE stream in `working` state, then mutate the persisted
        # Task to `completed` after a delay. The client's _poll_until_terminal
        # picks up the change on its next tasks/get call.
        await updater.start_work()
        task_id = context.task_id
        bg = asyncio.create_task(self._delayed_complete(task_id))
        self._background_tasks.add(bg)
        bg.add_done_callback(self._background_tasks.discard)

    async def _delayed_complete(self, task_id: str | None) -> None:
        if task_id is None:
            logger.warning("polling: no task_id, skipping delayed completion")
            return
        await asyncio.sleep(_POLLING_COMPLETION_DELAY_SECONDS)
        task = await self._task_store.get(task_id)
        if task is None:
            logger.warning("polling: task %s not found in store at completion time", task_id)
            return
        completion_part = _make_text_part(f"Polled task completed after ~{_POLLING_COMPLETION_DELAY_SECONDS:.0f}s.")
        artifact = Artifact(
            artifact_id=uuid.uuid4().hex,
            name="result",
            parts=[completion_part],
        )
        task.artifacts = (task.artifacts or []) + [artifact]
        task.status = TaskStatus(state=TaskState.completed, timestamp=datetime.now(UTC).isoformat())
        await self._task_store.save(task)
        logger.info("polling: task %s flipped to completed", task_id)


def build_app() -> object:
    """Construct the FastAPI app exposing A2A REST endpoints."""
    task_store = InMemoryTaskStore()
    executor = LocalTestExecutor(task_store)
    handler = DefaultRequestHandler(agent_executor=executor, task_store=task_store)
    return A2ARESTFastAPIApplication(agent_card=_build_agent_card(), http_handler=handler).build()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=_DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    uvicorn.run(build_app(), host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
