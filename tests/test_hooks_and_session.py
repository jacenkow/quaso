from __future__ import annotations

import pytest

from quaso.config import HookConfig, HooksConfig
from quaso.hooks import HookRunner
from quaso.messages import Message, ToolCall, tool_result, user
from quaso.session import Session, find_session, list_sessions


@pytest.mark.asyncio
async def test_pre_hook_blocks_on_nonzero_exit(tmp_path):
    config = HooksConfig(
        pre_tool_use=[
            HookConfig(matcher="write_file", command="echo nope >&2; exit 1")
        ]
    )
    outcome = await HookRunner(config, tmp_path).pre_tool_use(
        "write_file", {"path": "x"}
    )
    assert outcome.blocked and "nope" in outcome.reason


@pytest.mark.asyncio
async def test_pre_hook_allows_on_zero_exit(tmp_path):
    config = HooksConfig(
        pre_tool_use=[HookConfig(matcher="*", command="exit 0")]
    )
    outcome = await HookRunner(config, tmp_path).pre_tool_use("bash", {})
    assert not outcome.blocked


@pytest.mark.asyncio
async def test_hook_matcher_scopes_to_tool(tmp_path):
    config = HooksConfig(
        pre_tool_use=[
            HookConfig(matcher="write_file|edit_file", command="exit 1")
        ]
    )
    runner = HookRunner(config, tmp_path)
    assert (await runner.pre_tool_use("edit_file", {})).blocked
    assert not (await runner.pre_tool_use("read_file", {})).blocked


@pytest.mark.asyncio
async def test_hook_receives_payload_on_stdin(tmp_path):
    config = HooksConfig(
        pre_tool_use=[HookConfig(matcher="*", command="cat >&2; exit 1")]
    )
    outcome = await HookRunner(config, tmp_path).pre_tool_use(
        "bash", {"command": "ls -la"}
    )
    assert "ls -la" in outcome.reason and "pre_tool_use" in outcome.reason


@pytest.mark.asyncio
async def test_post_hook_output_becomes_notice(tmp_path):
    config = HooksConfig(
        post_tool_use=[HookConfig(matcher="*", command="echo formatted")]
    )
    notice = await HookRunner(config, tmp_path).post_tool_use(
        "edit_file", {}, "result"
    )
    assert notice == "formatted"


@pytest.mark.asyncio
async def test_hook_timeout_blocks(tmp_path):
    config = HooksConfig(
        pre_tool_use=[HookConfig(matcher="*", command="sleep 5", timeout=1)]
    )
    outcome = await HookRunner(config, tmp_path).pre_tool_use("bash", {})
    assert outcome.blocked and "timed out" in outcome.reason


def test_transcript_roundtrip(tmp_path):
    session = Session("system prompt", root=tmp_path, persist=True)
    session.append(user("hello"))
    session.append(Message(role="assistant", content="hi"))
    path = session.transcript
    assert path is not None and path.is_file()

    resumed = Session("ignored prompt", root=tmp_path, persist=True)
    resumed.load(path)
    assert [m.role for m in resumed.messages] == [
        "system",
        "user",
        "assistant",
    ]
    assert resumed.messages[1].content == "hello"


def test_list_and_find_sessions(tmp_path):
    session = Session("sys", root=tmp_path, persist=True)
    session.append(user("x"))
    found = list_sessions(tmp_path)
    assert len(found) == 1
    assert find_session(tmp_path, session.id) == found[0]
    assert find_session(tmp_path, session.id[:8]) == found[0]
    assert find_session(tmp_path, "nonexistent") is None


def test_repair_dangling_tool_calls(tmp_path):
    session = Session("sys", root=tmp_path, persist=False)
    call = ToolCall(id="c1", name="bash", arguments={})
    session.append(user("do it"))
    session.append(Message(role="assistant", tool_calls=[call]))
    # Interrupted here: the tool never ran.
    assert session.repair_dangling_tool_calls() == 1
    assert session.messages[-1].role == "tool"
    assert "interrupted" in session.messages[-1].content
    # Idempotent.
    assert session.repair_dangling_tool_calls() == 0


def test_repair_leaves_answered_calls_alone(tmp_path):
    session = Session("sys", root=tmp_path, persist=False)
    call = ToolCall(id="c1", name="bash", arguments={})
    session.append(user("do it"))
    session.append(Message(role="assistant", tool_calls=[call]))
    session.append(tool_result(call, "output"))
    assert session.repair_dangling_tool_calls() == 0


def test_replace_history_rewrites_transcript(tmp_path):
    session = Session("sys", root=tmp_path, persist=True)
    session.append(user("first"))
    session.append(Message(role="assistant", content="second"))
    session.replace_history([session.messages[0], user("compacted")])
    reloaded = Session("sys", root=tmp_path, persist=True)
    reloaded.load(session.transcript)
    assert len(reloaded.messages) == 2
    assert reloaded.messages[1].content == "compacted"


@pytest.mark.asyncio
async def test_every_matching_pre_hook_runs(tmp_path):
    """A hook that merely reports must not stand in for the ones after it.

    Returning on the first hook that printed anything meant a benign
    logging hook silently disabled every enforcement hook behind it.
    """
    config = HooksConfig(
        pre_tool_use=[
            HookConfig(matcher="bash", command="echo 'audit: logged'"),
            HookConfig(matcher="bash", command="echo DENIED >&2; exit 1"),
        ]
    )
    outcome = await HookRunner(config, tmp_path).pre_tool_use("bash", {})
    assert outcome.blocked
    assert "DENIED" in outcome.reason


@pytest.mark.asyncio
async def test_output_from_several_hooks_is_kept(tmp_path):
    config = HooksConfig(
        pre_tool_use=[
            HookConfig(matcher="bash", command="echo first"),
            HookConfig(matcher="bash", command="echo second"),
        ]
    )
    outcome = await HookRunner(config, tmp_path).pre_tool_use("bash", {})
    assert not outcome.blocked
    assert "first" in outcome.output
    assert "second" in outcome.output


@pytest.mark.asyncio
async def test_a_block_stops_the_hooks_behind_it(tmp_path):
    """No point running the rest once the call is refused."""
    marker = tmp_path / "ran"
    config = HooksConfig(
        pre_tool_use=[
            HookConfig(matcher="bash", command="exit 1"),
            HookConfig(matcher="bash", command=f"touch {marker}"),
        ]
    )
    outcome = await HookRunner(config, tmp_path).pre_tool_use("bash", {})
    assert outcome.blocked
    assert not marker.exists()
