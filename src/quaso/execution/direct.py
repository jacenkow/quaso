"""Running a command with no confinement at all.

What quaso has always done, kept as an executor so that the escape tests
have something to measure a sandbox against, and so a machine with no
sandbox available still runs rather than refusing to start.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from pathlib import Path

from quaso.execution.base import Completed, Executor


async def read_capped(
    stream: asyncio.StreamReader | None, limit: int
) -> tuple[bytes, int]:
    """Read up to `limit` bytes, counting what was thrown away."""
    if stream is None:
        return b"", 0
    chunks: list[bytes] = []
    kept = dropped = 0
    while True:
        chunk = await stream.read(65_536)
        if not chunk:
            break
        if kept < limit:
            room = limit - kept
            chunks.append(chunk[:room])
            kept += min(len(chunk), room)
            dropped += max(0, len(chunk) - room)
        else:
            dropped += len(chunk)
    return b"".join(chunks), dropped


def terminate(proc: asyncio.subprocess.Process) -> None:
    """Signal the whole group, so nothing the command spawned survives."""
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        return
    with contextlib.suppress(ProcessLookupError):
        proc.kill()


async def spawn(
    argv: list[str] | str, cwd: Path, timeout: int, max_bytes: int
) -> Completed:
    """Run a command, bounded in time and in how much is kept.

    Shared by every executor: a sandbox decides what the command may
    touch, not how its output is read or how it is stopped.
    """
    if isinstance(argv, str):
        proc = await asyncio.create_subprocess_shell(
            argv,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

    reading = asyncio.gather(
        read_capped(proc.stdout, max_bytes),
        read_capped(proc.stderr, max_bytes),
    )
    try:
        (out, out_dropped), (err, err_dropped) = await asyncio.wait_for(
            reading, timeout=timeout
        )
        await proc.wait()
    except TimeoutError:
        terminate(proc)
        await proc.wait()
        raise
    except asyncio.CancelledError:
        terminate(proc)
        await proc.wait()
        raise

    return Completed(
        stdout=out,
        stderr=err,
        exit_code=proc.returncode or 0,
        dropped=out_dropped + err_dropped,
    )


class DirectExecutor(Executor):
    name = "none"

    async def run(
        self, command: str, cwd: Path, timeout: int, max_bytes: int
    ) -> Completed:
        return await spawn(command, cwd, timeout, max_bytes)

    def describe(self) -> str:
        return "shell: no sandbox"
