"""Allowlisted, bounded subprocess runner for scanner tools (D10).

The pod env holds the Mongo URI, encryption key and IdP secrets; tools read untrusted content and call external
endpoints. A tool subprocess gets an explicit allowlisted environment: the base variables, the cloud-credential
passthroughs the LLM call needs, and the adapter's own additions — nothing else from ``os.environ``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Base environment every tool gets. HOME and TMPDIR are set per-run to the run dir so tool temp files land there.
_BASE_ENV: dict[str, str] = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
}

# The ONLY variables copied from the pod environment, when present: the cloud credentials the LLM call needs.
# Nothing else (MongoDB URI/credentials, CREDS_KEY, SECRET_KEY, JWT keys, IdP secrets) reaches tool code.
PASSTHROUGH_ENV: tuple[str, ...] = (
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_ROLE_ARN",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_STS_REGIONAL_ENDPOINTS",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AZURE_CLIENT_ID",
    "AZURE_TENANT_ID",
    "AZURE_FEDERATED_TOKEN_FILE",
    "AZURE_AUTHORITY_HOST",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
)

_STDERR_TAIL_BYTES = 4096
_KILL_GRACE_SECONDS = 5.0


@dataclass(frozen=True)
class Invocation:
    """One tool invocation. ``output_file`` is where the tool writes its report (argv references it)."""

    argv: list[str]
    output_file: Path
    env: dict[str, str] = field(default_factory=dict)  # adapter additions only
    secrets: tuple[str, ...] = ()  # values to redact from error text
    llm_used: bool = False  # set by the adapter: whether this invocation enables the tool's LLM pass


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int | None
    timed_out: bool
    output_file: Path
    output_too_large: bool
    stderr_tail: str  # last 4096 bytes, decoded with errors="replace"
    duration_ms: int


def _build_env(cwd: Path, additions: dict[str, str]) -> dict[str, str]:
    env = dict(_BASE_ENV)
    env["HOME"] = str(cwd)
    env["TMPDIR"] = str(cwd)
    for name in PASSTHROUGH_ENV:
        value = os.environ.get(name)
        if value is not None:
            env[name] = value
    env.update(additions)
    return env


async def _kill_group(proc: asyncio.subprocess.Process) -> None:
    """SIGTERM the process group, wait up to the grace period, then SIGKILL. Never outlives its job."""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    with suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE_SECONDS)
    except TimeoutError:
        with suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGKILL)
        await proc.wait()


async def _drain_stderr(stream: asyncio.StreamReader, tail: bytearray) -> None:
    """Drain stderr continuously into a 4 KiB ring buffer so a chatty tool can't block on a full pipe."""
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            return
        tail.extend(chunk)
        if len(tail) > _STDERR_TAIL_BYTES:
            del tail[:-_STDERR_TAIL_BYTES]


async def run_tool_process(
    invocation: Invocation, *, cwd: Path, timeout: float, max_output_bytes: int
) -> ProcessResult:
    """Run one tool subprocess. Never raises on a non-zero exit; interpreting exit codes is the adapter's job."""
    env = _build_env(cwd, invocation.env)
    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *invocation.argv,
        env=env,
        cwd=str(cwd),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    tail = bytearray()
    assert proc.stderr is not None  # noqa: S101 — PIPE guarantees it; narrows the type
    drain_task = asyncio.create_task(_drain_stderr(proc.stderr, tail))

    timed_out = False
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except TimeoutError:
        timed_out = True
        await _kill_group(proc)
    except asyncio.CancelledError:
        # Shutdown: kill the group so none survives pod shutdown, then propagate.
        await _kill_group(proc)
        drain_task.cancel()
        with suppress(asyncio.CancelledError):
            await drain_task
        raise

    # The process has exited (normally or killed); stderr reaches EOF, so the drain completes.
    with suppress(Exception):
        await drain_task

    duration_ms = int((time.monotonic() - start) * 1000)
    try:
        size = invocation.output_file.stat().st_size
    except OSError:
        size = 0
    return ProcessResult(
        exit_code=proc.returncode,
        timed_out=timed_out,
        output_file=invocation.output_file,
        output_too_large=size > max_output_bytes,  # the file is never read in that case
        stderr_tail=bytes(tail).decode("utf-8", errors="replace"),
        duration_ms=duration_ms,
    )
