"""Real-subprocess tests for the allowlisted runner. The env-equality test is the guard against leaking
pod secrets to untrusted tool code (Risk)."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from skill_scan_worker.process import PASSTHROUGH_ENV, Invocation, run_tool_process

pytestmark = pytest.mark.asyncio

# macOS injects this into every spawned process regardless of the env we pass; it carries no pod secret.
_OS_INJECTED_KEYS = ("__CF_USER_TEXT_ENCODING",)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def test_child_env_is_exactly_the_allowlist(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AWS_REGION", "us-east-1")  # passthrough
    monkeypatch.setenv("MONGO_URI", "mongodb://secret")  # must NOT leak
    monkeypatch.setenv("CREDS_KEY", "00" * 16)  # must NOT leak
    monkeypatch.setenv("SECRET_KEY", "shh")  # must NOT leak
    monkeypatch.setenv("JWT_PRIVATE_KEY", "pem")  # must NOT leak

    out = tmp_path / "env.out"
    argv = [
        sys.executable,
        "-c",
        "import os,json,sys;open(sys.argv[1],'w').write(json.dumps(dict(os.environ)))",
        str(out),
    ]
    invocation = Invocation(argv=argv, output_file=out, env={"TOOL_FLAG": "1"})
    result = await run_tool_process(invocation, cwd=tmp_path, timeout=10, max_output_bytes=1 << 20)

    assert result.exit_code == 0
    child_env = json.loads(out.read_text())
    for key in _OS_INJECTED_KEYS:
        child_env.pop(key, None)

    # Exactly: base vars + HOME/TMPDIR + invocation.env + whichever PASSTHROUGH_ENV vars are actually set.
    expected = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "TOOL_FLAG": "1",
    }
    for name in PASSTHROUGH_ENV:
        if name in os.environ:
            expected[name] = os.environ[name]
    assert child_env == expected
    # The security guarantee: no pod secret reaches the tool.
    assert {"MONGO_URI", "CREDS_KEY", "SECRET_KEY", "JWT_PRIVATE_KEY"}.isdisjoint(child_env)


async def test_timeout_kills_process_group_including_grandchild(tmp_path: Path) -> None:
    out = tmp_path / "pid.out"
    # Spawn a grandchild that sleeps, record its pid, then sleep ourselves.
    argv = [
        sys.executable,
        "-c",
        (
            "import subprocess,sys,time;"
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
            "open(sys.argv[1],'w').write(str(p.pid));"
            "time.sleep(60)"
        ),
        str(out),
    ]
    result = await run_tool_process(
        Invocation(argv=argv, output_file=out), cwd=tmp_path, timeout=1, max_output_bytes=1 << 20
    )
    assert result.timed_out is True

    grandchild_pid = int(out.read_text())
    for _ in range(50):
        if not _pid_alive(grandchild_pid):
            break
        await asyncio.sleep(0.1)
    assert not _pid_alive(grandchild_pid)


async def test_cancelling_the_task_kills_the_group(tmp_path: Path) -> None:
    out = tmp_path / "pid.out"
    argv = [
        sys.executable,
        "-c",
        "import os,sys,time;open(sys.argv[1],'w').write(str(os.getpid()));time.sleep(60)",
        str(out),
    ]
    task = asyncio.ensure_future(
        run_tool_process(Invocation(argv=argv, output_file=out), cwd=tmp_path, timeout=60, max_output_bytes=1 << 20)
    )
    # Wait until the child has written its pid.
    for _ in range(50):
        if out.exists() and out.read_text():
            break
        await asyncio.sleep(0.1)
    child_pid = int(out.read_text())

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    for _ in range(50):
        if not _pid_alive(child_pid):
            break
        await asyncio.sleep(0.1)
    assert not _pid_alive(child_pid)


async def test_large_stderr_does_not_deadlock_and_tail_is_bounded(tmp_path: Path) -> None:
    out = tmp_path / "x.out"
    argv = [
        sys.executable,
        "-c",
        "import sys;sys.stderr.write('x'*10_000_000);sys.stderr.flush()",
        str(out),
    ]
    result = await run_tool_process(
        Invocation(argv=argv, output_file=out), cwd=tmp_path, timeout=30, max_output_bytes=1 << 20
    )
    assert result.exit_code == 0
    assert len(result.stderr_tail.encode("utf-8")) <= 4096


async def test_non_zero_exit_returns_result_without_raising(tmp_path: Path) -> None:
    out = tmp_path / "x.out"
    argv = ["sh", "-c", "exit 3"]
    result = await run_tool_process(
        Invocation(argv=argv, output_file=out), cwd=tmp_path, timeout=10, max_output_bytes=1 << 20
    )
    assert result.exit_code == 3
    assert result.timed_out is False
