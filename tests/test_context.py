from __future__ import annotations

import pytest

from quaso.config import ContextConfig
from quaso.context import (
    ContextManager,
    _fit,
    _turn_boundary,
    estimate_messages,
    estimate_tokens,
    flatten,
)
from quaso.messages import Message, ToolCall, system, tool_result, user

from .conftest import FakeProvider


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 400) == 100


def test_estimate_messages_counts_tool_calls():
    plain = [user("hello")]
    with_call = [
        Message(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="c", name="read_file", arguments={"path": "x" * 200}
                )
            ],
        )
    ]
    assert estimate_messages(with_call) > estimate_messages(plain)


def test_turn_boundary_never_orphans_a_tool_result():
    call = ToolCall(id="c1", name="read_file", arguments={})
    messages = [
        system("sys"),
        user("first"),
        Message(role="assistant", tool_calls=[call]),
        tool_result(call, "output"),
        Message(role="assistant", content="done"),
        user("second"),
        Message(role="assistant", content="ok"),
    ]
    for keep in range(1, len(messages)):
        index = _turn_boundary(messages, keep_recent=keep)
        assert messages[index].role != "tool", (
            f"orphaned a tool result at keep={keep}"
        )


def test_turn_boundary_keeps_recent_work_in_long_tool_turns():
    """A long tool-driven turn has no recent user message to snap to.

    Requiring one discarded the entire recent window, the regression that let
    a 42-message session sit at 100% with nothing recoverable.
    """
    call = ToolCall(id="c", name="read_file", arguments={})
    messages = [system("sys"), user("go")]
    for _ in range(20):
        messages.append(Message(role="assistant", tool_calls=[call]))
        messages.append(tool_result(call, "big output"))

    index = _turn_boundary(messages, keep_recent=8)
    assert index < len(messages), "must not discard the whole recent window"
    assert messages[index].role != "tool"
    assert len(messages) - index >= 4  # a useful amount of recent context kept


def test_summariser_input_is_bounded_by_the_window():
    """Compaction must not need the space it is trying to free."""
    call = ToolCall(id="c", name="read_file", arguments={})
    messages = [user("go")]
    for _ in range(30):
        messages.append(Message(role="assistant", tool_calls=[call]))
        messages.append(tool_result(call, "X" * 20_000))  # huge tool outputs

    raw = estimate_messages(messages)
    flat = flatten(messages)
    assert estimate_tokens(flat) < raw / 10, "tool results must be excerpted"

    budget_chars = 4000
    assert len(_fit(flat, budget_chars)) <= budget_chars + 100
    # The original request and the most recent work both survive the trim.
    trimmed = _fit(flat, budget_chars)
    assert "[user] go" in trimmed and trimmed.rstrip().endswith("]")


def test_flatten_renders_every_role():
    call = ToolCall(id="c", name="bash", arguments={"command": "ls"})
    flat = flatten(
        [
            user("do it"),
            Message(role="assistant", content="sure", tool_calls=[call]),
            tool_result(call, "file.txt"),
        ]
    )
    assert "[user] do it" in flat
    assert "[assistant called: bash]" in flat
    assert "[tool:bash] file.txt" in flat


def test_should_compact_respects_threshold_and_switch():
    config = ContextConfig(compact_threshold=0.5, auto_compact=True)
    manager = ContextManager(config, max_tokens=100)
    small = [user("x" * 40)]  # ~10 tokens
    assert not manager.should_compact(small)
    manager.last_prompt_tokens = 60
    assert manager.should_compact(small)

    manager.config = ContextConfig(compact_threshold=0.5, auto_compact=False)
    assert not manager.should_compact(small)


@pytest.mark.asyncio
async def test_compact_summarises_and_keeps_recent():
    call = ToolCall(id="c1", name="read_file", arguments={})
    messages = [
        system("sys"),
        user("old question"),
        Message(role="assistant", tool_calls=[call]),
        tool_result(call, "lots of output " * 100),
        Message(role="assistant", content="old answer"),
        user("recent question"),
        Message(role="assistant", content="recent answer"),
    ]
    provider = FakeProvider(
        [Message(role="assistant", content="SUMMARY OF EARLIER WORK")]
    )
    manager = ContextManager(
        ContextConfig(keep_recent_messages=2), max_tokens=1000
    )

    rebuilt, before, after = await manager.compact(messages, provider)

    assert rebuilt[0].role == "system"
    assert "SUMMARY OF EARLIER WORK" in rebuilt[1].content
    assert rebuilt[-2].content == "recent question"
    assert rebuilt[-1].content == "recent answer"
    assert after < before
    # No orphaned tool results survived.
    assert not any(m.role == "tool" for m in rebuilt)


@pytest.mark.asyncio
async def test_compaction_does_not_thrash():
    """A compaction that cannot get under the threshold must not repeat.

    With a large system prompt the kept tail alone can exceed the limit;
    without a guard the loop would re-summarise on every iteration.
    """
    messages = [
        system("x" * 4000),  # ~1000 tokens: over the limit on its own
        user("old"),
        Message(role="assistant", content="old answer"),
        user("recent"),
        Message(role="assistant", content="recent answer"),
    ]
    provider = FakeProvider([Message(role="assistant", content="summary")])
    manager = ContextManager(
        ContextConfig(compact_threshold=0.5, keep_recent_messages=2),
        max_tokens=1000,
    )
    assert manager.should_compact(messages)

    rebuilt, _, _ = await manager.compact(messages, provider)
    # Still over the threshold, but must not immediately compact again.
    assert manager.fraction(rebuilt) >= 0.5
    assert not manager.should_compact(rebuilt)

    # Growth is measured in tokens, not messages: a couple of large tool
    # results must re-arm compaction even though the count barely moved.
    call = ToolCall(id="c", name="read_file", arguments={})
    grown = rebuilt + [
        Message(role="assistant", tool_calls=[call]),
        tool_result(call, "Z" * 2000),
    ]
    assert manager.should_compact(grown)


def test_reset_clears_compaction_state():
    manager = ContextManager(
        ContextConfig(compact_threshold=0.1), max_tokens=100
    )
    manager._usage_after_compaction = 5
    manager.last_prompt_tokens = 99
    manager.reset()
    assert manager.last_prompt_tokens == 0
    assert manager.should_compact([user("x" * 400)])


@pytest.mark.asyncio
async def test_compact_noop_when_nothing_old():
    messages = [system("sys"), user("only question")]
    provider = FakeProvider([Message(role="assistant", content="unused")])
    manager = ContextManager(
        ContextConfig(keep_recent_messages=8), max_tokens=1000
    )
    rebuilt, before, after = await manager.compact(messages, provider)
    assert rebuilt is messages and before == after
