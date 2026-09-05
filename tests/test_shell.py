"""Running a command, and being able to stop it.

Killing the shell is not killing what the shell started, and a command
that talks forever should not be held in memory until it stops.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from quaso.tools.base import ToolContext, ToolError
from quaso.tools.shell import MAX_STREAM_BYTES, Bash, BashParams


async def _run(command: str, tmp_path: Path, **kwargs) -> str:
    return await Bash().run(
        BashParams(command=command, **kwargs), ToolContext(cwd=tmp_path)
    )


class TestOrdinaryUse:
    @pytest.mark.asyncio
    async def test_output_and_exit_code_come_back(self, tmp_path):
        out = await _run("echo hello", tmp_path)
        assert "hello" in out
        assert "[exit code: 0]" in out

    @pytest.mark.asyncio
    async def test_stderr_is_labelled(self, tmp_path):
        out = await _run("echo oops >&2; exit 3", tmp_path)
        assert "[stderr]" in out and "oops" in out
        assert "[exit code: 3]" in out

    @pytest.mark.asyncio
    async def test_it_runs_in_the_working_directory(self, tmp_path):
        (tmp_path / "marker.txt").write_text("x")
        assert "marker.txt" in await _run("ls", tmp_path)


class TestTimeoutTakesTheWholeTree:
    @pytest.mark.asyncio
    async def test_the_command_is_reported_as_timed_out(self, tmp_path):
        with pytest.raises(ToolError, match="timed out"):
            await _run("sleep 5", tmp_path, timeout=1)

    @pytest.mark.asyncio
    async def test_children_do_not_outlive_the_timeout(self, tmp_path):
        """Killing the shell leaves whatever it started still running:
        a dev server keeps its port, a build keeps writing."""
        marker = tmp_path / "written-by-an-orphan"
        with pytest.raises(ToolError, match="timed out"):
            await _run(
                f"(sleep 1 && touch {marker}) & sleep 5",
                tmp_path,
                timeout=1,
            )
        await asyncio.sleep(2.5)
        assert not marker.exists(), "a child survived the timeout"


class TestOutputIsBounded:
    @pytest.mark.asyncio
    async def test_a_noisy_command_does_not_buffer_without_limit(
        self, tmp_path
    ):
        """The budget trims the result afterwards; that is too late to
        stop the memory being spent."""
        produced = MAX_STREAM_BYTES * 2
        out = await _run(
            f"head -c {produced} /dev/zero | tr '\\0' 'x'", tmp_path
        )
        assert len(out) < produced, "the whole stream was kept"
        assert len(out) <= MAX_STREAM_BYTES + 200
        assert "further bytes were not read" in out

    @pytest.mark.asyncio
    async def test_output_within_the_ceiling_is_untouched(self, tmp_path):
        out = await _run("printf 'abc'", tmp_path)
        assert "abc" in out
        assert "truncated" not in out
