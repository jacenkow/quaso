"""Calls reaching outside the working directory must be approved.

Reading is otherwise unrestricted and web_search is also non-mutating, so
without this an agent could be talked into reading a private key and
posting it as a search query without a single prompt.
"""

from __future__ import annotations

import pytest

from quaso.config import PermissionsConfig
from quaso.permissions import PermissionPolicy, escapes_workspace
from quaso.tools.base import ToolContext, ToolError
from quaso.tools.fs import (
    EditFile,
    EditFileParams,
    ReadFile,
    ReadFileParams,
    WriteFile,
    WriteFileParams,
)
from quaso.tools.search import Glob, GlobParams, Grep, GrepParams
from quaso.tools.shell import Bash, BashParams
from quaso.tools.todo import TodoWrite, TodoWriteParams


def _asker(answer="deny"):
    calls = []

    async def ask(request):
        calls.append(request)
        return answer

    ask.calls = calls
    return ask


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(cwd=tmp_path)


def test_paths_inside_the_workspace_do_not_escape(ctx):
    assert not escapes_workspace(ReadFile(), ReadFileParams(path="a.py"), ctx)
    assert not escapes_workspace(
        ReadFile(), ReadFileParams(path="./sub/a.py"), ctx
    )


def test_absolute_paths_elsewhere_escape(ctx):
    assert escapes_workspace(
        ReadFile(), ReadFileParams(path="/etc/hosts"), ctx
    )


def test_traversal_escapes(ctx):
    assert escapes_workspace(
        ReadFile(), ReadFileParams(path="../../secrets.txt"), ctx
    )


def test_outside_glob_root_is_visible_to_permission_policy(ctx):
    assert escapes_workspace(
        Glob(), GlobParams(pattern="*", path="../outside"), ctx
    )


@pytest.mark.asyncio
async def test_glob_pattern_cannot_traverse_outside_workspace(ctx, tmp_path):
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("secret")

    with pytest.raises(ToolError, match="pattern"):
        await Glob().run(GlobParams(pattern="../outside/*", path="."), ctx)


@pytest.mark.asyncio
async def test_python_grep_glob_cannot_traverse_outside_workspace(
    ctx, tmp_path, monkeypatch
):
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("TOP SECRET")
    monkeypatch.setattr("quaso.tools.search.shutil.which", lambda _: None)

    with pytest.raises(ToolError, match="pattern"):
        await Grep().run(
            GrepParams(
                pattern="TOP SECRET",
                path=".",
                glob="../outside/*",
            ),
            ctx,
        )


@pytest.mark.asyncio
async def test_glob_cannot_follow_symlink_outside_search_root(ctx, tmp_path):
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("secret")
    link = tmp_path / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    # Skipped rather than fatal, but the content must not come back.
    output = await Glob().run(GlobParams(pattern="linked/*", path="."), ctx)
    assert "secret.txt" not in output
    assert str(outside) not in output


@pytest.mark.asyncio
async def test_python_grep_cannot_follow_symlink_outside_search_root(
    ctx, tmp_path, monkeypatch
):
    outside = tmp_path.parent / "grep-outside"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("TOP SECRET")
    link = tmp_path / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    monkeypatch.setattr("quaso.tools.search.shutil.which", lambda _: None)

    output = await Grep().run(
        GrepParams(pattern="TOP SECRET", path=".", glob="linked/*"), ctx
    )
    assert "TOP SECRET" not in output
    assert str(outside) not in output


@pytest.mark.asyncio
async def test_normal_recursive_glob_stays_available(ctx, tmp_path):
    source = tmp_path / "src" / "package"
    source.mkdir(parents=True)
    target = source / "module.py"
    target.write_text("pass\n")

    output = await Glob().run(GlobParams(pattern="**/*.py", path="."), ctx)

    assert str(target) in output


@pytest.mark.asyncio
async def test_approved_outside_glob_root_stays_available(ctx, tmp_path):
    outside = tmp_path.parent / "approved-outside"
    outside.mkdir(exist_ok=True)
    target = outside / "visible.txt"
    target.write_text("visible")
    ask = _asker("allow")
    policy = PermissionPolicy(PermissionsConfig(mode="default"), ask)
    tool = Glob()
    params = GlobParams(pattern="*", path=str(outside))

    decision = await policy.check(tool, params, ctx)
    output = await tool.run(params, ctx) if decision.allowed else ""

    assert decision.allowed
    assert len(ask.calls) == 1
    assert ask.calls[0].outside_workspace is True
    assert str(target) in output


def test_tools_touching_no_files_never_escape(ctx):
    params = TodoWriteParams.model_validate({"todos": []})
    assert not escapes_workspace(TodoWrite(), params, ctx)


@pytest.mark.asyncio
async def test_reading_outside_the_workspace_now_prompts(ctx):
    """The regression this file exists for."""
    ask = _asker("deny")
    policy = PermissionPolicy(PermissionsConfig(mode="default"), ask)
    decision = await policy.check(
        ReadFile(), ReadFileParams(path="/etc/hosts"), ctx
    )
    assert not decision.allowed
    assert len(ask.calls) == 1
    assert ask.calls[0].outside_workspace is True


@pytest.mark.asyncio
async def test_reading_inside_the_workspace_stays_silent(ctx):
    ask = _asker("deny")
    policy = PermissionPolicy(PermissionsConfig(mode="default"), ask)
    decision = await policy.check(
        ReadFile(), ReadFileParams(path="src/main.py"), ctx
    )
    assert decision.allowed
    assert ask.calls == []


@pytest.mark.asyncio
async def test_grep_outside_the_workspace_prompts(ctx):
    ask = _asker("deny")
    policy = PermissionPolicy(PermissionsConfig(mode="default"), ask)
    decision = await policy.check(
        Grep(), GrepParams(pattern="BEGIN.*PRIVATE KEY", path="/home"), ctx
    )
    assert not decision.allowed


@pytest.mark.asyncio
async def test_accept_edits_does_not_extend_outside_the_workspace(ctx):
    ask = _asker("deny")
    policy = PermissionPolicy(PermissionsConfig(mode="acceptEdits"), ask)

    inside = await policy.check(
        WriteFile(), WriteFileParams(path="a.txt", content="x"), ctx
    )
    assert inside.allowed and not ask.calls

    outside = await policy.check(
        EditFile(),
        EditFileParams(path="/etc/hosts", old_string="a", new_string="b"),
        ctx,
    )
    assert not outside.allowed
    assert len(ask.calls) == 1


@pytest.mark.asyncio
async def test_always_does_not_bless_every_later_escape(ctx):
    """Approving one outside read must not open the door permanently."""
    ask = _asker("always")
    policy = PermissionPolicy(PermissionsConfig(mode="default"), ask)

    first = await policy.check(
        ReadFile(), ReadFileParams(path="/etc/hosts"), ctx
    )
    second = await policy.check(
        ReadFile(), ReadFileParams(path="/etc/shadow"), ctx
    )
    assert first.allowed and second.allowed
    assert len(ask.calls) == 2, "the second escape must ask again"


@pytest.mark.asyncio
async def test_always_still_works_for_ordinary_tools(ctx):
    ask = _asker("always")
    policy = PermissionPolicy(PermissionsConfig(mode="default"), ask)
    await policy.check(Bash(), BashParams(command="ls"), ctx)
    await policy.check(Bash(), BashParams(command="pwd"), ctx)
    assert len(ask.calls) == 1


@pytest.mark.asyncio
async def test_deny_rules_still_beat_everything(ctx):
    ask = _asker("allow")
    config = PermissionsConfig(mode="yolo", deny=["read_file"])
    policy = PermissionPolicy(config, ask)
    decision = await policy.check(
        ReadFile(), ReadFileParams(path="/etc/hosts"), ctx
    )
    assert not decision.allowed


@pytest.mark.asyncio
async def test_yolo_asks_nothing_inside_the_workspace(ctx):
    ask = _asker("deny")
    policy = PermissionPolicy(PermissionsConfig(mode="yolo"), ask)
    decision = await policy.check(
        ReadFile(), ReadFileParams(path="README.md"), ctx
    )
    assert decision.allowed and not ask.calls


@pytest.mark.asyncio
async def test_yolo_still_asks_outside_it(ctx):
    """Changed deliberately: yolo used to allow this. The sandbox cannot
    cover the file tools, which run inside quaso rather than as a child
    process, so this question is the only boundary they have."""
    ask = _asker("deny")
    policy = PermissionPolicy(PermissionsConfig(mode="yolo"), ask)
    decision = await policy.check(
        ReadFile(), ReadFileParams(path="/etc/hosts"), ctx
    )
    assert not decision.allowed
    assert ask.calls


@pytest.mark.asyncio
async def test_missing_context_degrades_to_the_old_behaviour(ctx):
    """Callers without a context still get mutation-based checks."""
    ask = _asker("deny")
    policy = PermissionPolicy(PermissionsConfig(mode="default"), ask)
    decision = await policy.check(
        ReadFile(), ReadFileParams(path="/etc/hosts")
    )
    assert decision.allowed


class TestSymlinkedMatches:
    """A stray symlink must be skipped, not fatal to the whole search."""

    @staticmethod
    def _project(tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.py").write_text("KEY = 'leaked'\n")
        root = tmp_path / "project"
        root.mkdir()
        (root / "real.py").write_text("value = 2\n")
        return root, outside

    @pytest.mark.asyncio
    async def test_glob_still_returns_real_matches(self, tmp_path):
        import os

        from quaso.tools.search import Glob, GlobParams

        root, outside = self._project(tmp_path)
        os.symlink(outside / "secret.py", root / "shortcut.py")

        out = await Glob().run(
            GlobParams(pattern="*.py", path="."), ToolContext(cwd=root)
        )
        assert "real.py" in out, "one symlink must not abort the search"
        assert "secret.py" not in out
        assert "skipped" in out

    @pytest.mark.asyncio
    async def test_glob_reports_nothing_extra_without_symlinks(self, tmp_path):
        from quaso.tools.search import Glob, GlobParams

        root, _ = self._project(tmp_path)
        out = await Glob().run(
            GlobParams(pattern="*.py", path="."), ToolContext(cwd=root)
        )
        assert "real.py" in out
        assert "skipped" not in out

    @pytest.mark.asyncio
    async def test_python_grep_skips_symlinked_files(
        self, tmp_path, monkeypatch
    ):
        import os

        from quaso.tools import search as search_module
        from quaso.tools.search import Grep, GrepParams

        # Force the pure-Python path; the rg path relies on rg not
        # following symlinks by default.
        monkeypatch.setattr(search_module.shutil, "which", lambda _: None)

        root, outside = self._project(tmp_path)
        os.symlink(outside / "secret.py", root / "shortcut.py")

        out = await Grep().run(
            GrepParams(pattern="KEY|value", path="."), ToolContext(cwd=root)
        )
        assert "value" in out
        assert "leaked" not in out
