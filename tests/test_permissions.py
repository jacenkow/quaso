from __future__ import annotations

import pytest

from quaso.config import PermissionsConfig
from quaso.permissions import PermissionPolicy
from quaso.tools.base import ToolContext
from quaso.tools.fs import ReadFile, ReadFileParams, WriteFile, WriteFileParams
from quaso.tools.shell import Bash, BashParams


def _asker(answer):
    calls = []

    async def ask(request):
        calls.append(request)
        return answer

    ask.calls = calls
    return ask


@pytest.mark.asyncio
async def test_read_only_auto_allowed_in_default_mode():
    ask = _asker("deny")
    policy = PermissionPolicy(PermissionsConfig(mode="default"), ask)
    decision = await policy.check(ReadFile(), ReadFileParams(path="x"))
    assert decision.allowed and not ask.calls


@pytest.mark.asyncio
async def test_mutating_tool_asks_in_default_mode():
    ask = _asker("allow")
    policy = PermissionPolicy(PermissionsConfig(mode="default"), ask)
    decision = await policy.check(Bash(), BashParams(command="ls"))
    assert decision.allowed and len(ask.calls) == 1


@pytest.mark.asyncio
async def test_always_grants_for_session():
    ask = _asker("always")
    policy = PermissionPolicy(PermissionsConfig(mode="default"), ask)
    await policy.check(Bash(), BashParams(command="ls"))
    await policy.check(Bash(), BashParams(command="pwd"))
    assert len(ask.calls) == 1  # second call used the session grant


@pytest.mark.asyncio
async def test_deny_rule_beats_everything():
    ask = _asker("allow")
    config = PermissionsConfig(mode="yolo", deny=["bash(rm*)"])
    policy = PermissionPolicy(config, ask)
    decision = await policy.check(Bash(), BashParams(command="rm -rf /"))
    assert not decision.allowed and not ask.calls


@pytest.mark.asyncio
async def test_allow_rule_skips_ask():
    ask = _asker("deny")
    config = PermissionsConfig(mode="default", allow=["bash(git status*)"])
    policy = PermissionPolicy(config, ask)
    decision = await policy.check(
        Bash(), BashParams(command="git status --short")
    )
    assert decision.allowed and not ask.calls


@pytest.mark.parametrize(
    "command",
    [
        "git status; cat /etc/passwd",
        "git status && cat /etc/passwd",
        "git status || cat /etc/passwd",
        "git status | cat",
        "git status\ncat /etc/passwd",
        "git status > status.txt",
        "git status `cat /etc/passwd`",
        "git status $(cat /etc/passwd)",
    ],
)
@pytest.mark.asyncio
async def test_allow_rule_does_not_approve_shell_syntax(command):
    ask = _asker("deny")
    config = PermissionsConfig(mode="default", allow=["bash(git status*)"])
    policy = PermissionPolicy(config, ask)

    decision = await policy.check(Bash(), BashParams(command=command))

    assert not decision.allowed
    assert len(ask.calls) == 1


@pytest.mark.asyncio
async def test_command_deny_rule_rejects_compound_shell_command():
    ask = _asker("allow")
    config = PermissionsConfig(mode="yolo", deny=["bash(rm -rf*)"])
    policy = PermissionPolicy(config, ask)

    decision = await policy.check(
        Bash(), BashParams(command="echo ready; rm -rf ./target")
    )

    assert not decision.allowed
    assert not ask.calls


@pytest.mark.asyncio
async def test_readonly_mode_blocks_mutations():
    ask = _asker("allow")
    policy = PermissionPolicy(PermissionsConfig(mode="readonly"), ask)
    decision = await policy.check(
        WriteFile(), WriteFileParams(path="x", content="y")
    )
    assert not decision.allowed and not ask.calls


@pytest.mark.asyncio
async def test_accept_edits_allows_file_edits_but_asks_for_bash():
    ask = _asker("deny")
    policy = PermissionPolicy(PermissionsConfig(mode="acceptEdits"), ask)
    edit = await policy.check(
        WriteFile(), WriteFileParams(path="x", content="y")
    )
    assert edit.allowed
    shell = await policy.check(Bash(), BashParams(command="ls"))
    assert not shell.allowed and len(ask.calls) == 1


class TestYoloIsStillBounded:
    """yolo means "stop asking about my project", not "anything goes".

    Without this the two layers disagree: the kernel confines a shell
    command to the workspace while read_file, which runs inside quaso
    itself and cannot be confined, reads whatever it likes.
    """

    @pytest.mark.asyncio
    async def test_it_does_not_ask_inside_the_workspace(self, tmp_path):
        asked = []
        policy = PermissionPolicy(
            PermissionsConfig(mode="yolo"), _recording(asked)
        )
        ctx = ToolContext(cwd=tmp_path)
        decision = await policy.check(
            WriteFile(), WriteFileParams(path="a.py", content="x"), ctx
        )
        assert decision.allowed
        assert asked == []

    @pytest.mark.asyncio
    async def test_it_still_asks_outside_the_workspace(self, tmp_path):
        asked = []
        policy = PermissionPolicy(
            PermissionsConfig(mode="yolo"), _recording(asked, "deny")
        )
        ctx = ToolContext(cwd=tmp_path / "project")
        decision = await policy.check(
            ReadFile(),
            ReadFileParams(path=str(tmp_path / "outside" / "id_rsa")),
            ctx,
        )
        assert not decision.allowed
        assert len(asked) == 1
        assert asked[0].outside_workspace is True

    @pytest.mark.asyncio
    async def test_saying_yes_outside_still_works(self, tmp_path):
        """It is a question, not a refusal."""
        asked = []
        policy = PermissionPolicy(
            PermissionsConfig(mode="yolo"), _recording(asked, "allow")
        )
        ctx = ToolContext(cwd=tmp_path / "project")
        decision = await policy.check(
            ReadFile(),
            ReadFileParams(path=str(tmp_path / "elsewhere.txt")),
            ctx,
        )
        assert decision.allowed

    @pytest.mark.asyncio
    async def test_a_deny_rule_still_wins(self, tmp_path):
        policy = PermissionPolicy(
            PermissionsConfig(mode="yolo", deny=["write_file(*.env)"]),
            _recording([]),
        )
        decision = await policy.check(
            WriteFile(),
            WriteFileParams(path=".env", content="x"),
            ToolContext(cwd=tmp_path),
        )
        assert not decision.allowed


def _recording(sink, answer="allow"):
    async def asker(request):
        sink.append(request)
        return answer

    return asker
