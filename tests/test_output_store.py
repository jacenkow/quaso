"""Spilling oversized tool output to a file the model can go back to."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest

from quaso.output_store import STORE_DIR, ToolOutputStore
from quaso.tools.base import ToolContext
from quaso.tools.fs import ReadFile, ReadFileParams
from quaso.tools.search import Grep, GrepParams

BIG = "\n".join(f"line {i:04d} NEEDLE-{i}" for i in range(2_000))


def _spilled(root):
    return sorted((root / STORE_DIR).glob("tool_*"))


class TestFits:
    def test_small_output_is_returned_untouched(self, tmp_path):
        store = ToolOutputStore(tmp_path)
        assert store.bound("short", 1_000) == "short"

    def test_nothing_is_written_when_it_fits(self, tmp_path):
        ToolOutputStore(tmp_path).bound("short", 1_000)
        assert _spilled(tmp_path) == []


class TestSpills:
    def test_output_is_bounded_to_the_budget(self, tmp_path):
        out = ToolOutputStore(tmp_path).bound(BIG, 1_000)
        assert len(out) <= 1_000

    def test_the_full_text_is_kept_on_disk(self, tmp_path):
        ToolOutputStore(tmp_path).bound(BIG, 1_000)
        files = _spilled(tmp_path)
        assert len(files) == 1
        assert files[0].read_text() == BIG

    def test_the_elision_says_where_the_rest_went(self, tmp_path):
        out = ToolOutputStore(tmp_path).bound(BIG, 1_000)
        assert "full output in" in out
        assert "grep" in out

    def test_the_path_is_absolute_and_delimited(self, tmp_path):
        """Handed a relative ".quaso/..." the model drops the leading dot
        and asks for "/quaso/...", so the pointer is absolute."""
        out = ToolOutputStore(tmp_path).bound(BIG, 1_000)
        match = re.search(r"`([^`]+)`", out)
        assert match, out
        assert match.group(1).startswith("/")
        assert Path(match.group(1)).is_file()

    def test_the_path_in_the_elision_actually_exists(self, tmp_path):
        """A pointer the model cannot follow is worse than none."""
        out = ToolOutputStore(tmp_path).bound(BIG, 1_000)
        match = re.search(r"full output in `([^`]+)`", out)
        assert match, out
        assert Path(match.group(1)).is_file()

    def test_both_ends_still_survive(self, tmp_path):
        out = ToolOutputStore(tmp_path).bound(BIG, 1_000)
        assert "line 0000" in out
        assert "line 1999" in out

    def test_each_spill_gets_its_own_file(self, tmp_path):
        store = ToolOutputStore(tmp_path)
        store.bound(BIG, 1_000)
        store.bound(BIG, 1_000)
        assert len(_spilled(tmp_path)) == 2


class TestDegradesGracefully:
    def test_an_unwritable_store_still_bounds(self, tmp_path, monkeypatch):
        """Losing the spare copy must not fail the tool call."""
        store = ToolOutputStore(tmp_path)
        monkeypatch.setattr(
            "pathlib.Path.mkdir",
            lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")),
        )
        out = store.bound(BIG, 1_000)
        assert len(out) <= 1_000
        assert "truncated" in out
        assert "full output in" not in out

    def test_a_budget_too_small_for_a_pointer_still_bounds(self, tmp_path):
        out = ToolOutputStore(tmp_path).bound(BIG, 60)
        assert len(out) <= 60


class TestSweep:
    def test_old_output_is_removed(self, tmp_path):
        store = ToolOutputStore(tmp_path, retention_days=7)
        store.bound(BIG, 1_000)
        stale = _spilled(tmp_path)[0]
        old = time.time() - 8 * 86_400
        os.utime(stale, (old, old))

        assert store.sweep() == 1
        assert _spilled(tmp_path) == []

    def test_recent_output_is_kept(self, tmp_path):
        store = ToolOutputStore(tmp_path)
        store.bound(BIG, 1_000)
        assert store.sweep() == 0
        assert len(_spilled(tmp_path)) == 1

    def test_sweeping_an_absent_directory_is_fine(self, tmp_path):
        assert ToolOutputStore(tmp_path).sweep() == 0


class TestReachableByTools:
    """The spill is useless if the tools cannot open it."""

    @pytest.mark.asyncio
    async def test_the_model_can_read_the_spilled_file(self, tmp_path):
        store = ToolOutputStore(tmp_path)
        store.bound(BIG, 1_000)
        spill = _spilled(tmp_path)[0]
        relative = str(spill.relative_to(tmp_path))

        ctx = ToolContext(cwd=tmp_path)
        out = await ReadFile().run(ReadFileParams(path=relative), ctx)
        assert "NEEDLE-0" in out

    @pytest.mark.asyncio
    async def test_the_model_can_grep_the_spilled_file(self, tmp_path):
        store = ToolOutputStore(tmp_path)
        store.bound(BIG, 1_000)
        spill = _spilled(tmp_path)[0]

        ctx = ToolContext(cwd=tmp_path)
        out = await Grep().run(
            GrepParams(
                pattern="NEEDLE-1234", path=str(spill.relative_to(tmp_path))
            ),
            ctx,
        )
        assert "NEEDLE-1234" in out

    @pytest.mark.asyncio
    async def test_a_project_wide_search_does_not_trip_over_old_output(
        self, tmp_path
    ):
        (tmp_path / "real.py").write_text("x = 1\n")
        ToolOutputStore(tmp_path).bound(BIG, 1_000)

        ctx = ToolContext(cwd=tmp_path)
        out = await Grep().run(GrepParams(pattern="NEEDLE", path="."), ctx)
        assert "NEEDLE" not in out


class TestPointerSurvives:
    def test_an_unreachable_spill_is_not_left_behind(self, tmp_path):
        """A budget too small for the pointer makes the file litter."""
        store = ToolOutputStore(tmp_path)
        bounded = store.bound("x" * 5_000, 60)
        assert len(bounded) <= 60
        assert list(store.directory.glob("*.txt")) == []

    def test_a_reachable_spill_is_kept(self, tmp_path):
        store = ToolOutputStore(tmp_path)
        bounded = store.bound("x" * 5_000, 2_000)
        kept = list(store.directory.glob("*.txt"))
        assert len(kept) == 1
        assert str(kept[0]) in bounded
        assert kept[0].read_text() == "x" * 5_000
