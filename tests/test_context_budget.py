"""Per-tool output budgets, context notices, and the compact tool."""

from __future__ import annotations

import pytest

from quaso.agent.loop import Agent
from quaso.config import AgentConfig, Config, ContextConfig, PermissionsConfig
from quaso.context import ContextManager
from quaso.messages import Message, ToolCall
from quaso.permissions import PermissionPolicy
from quaso.session import Session
from quaso.tools.base import ToolContext, truncate
from quaso.tools.context import REQUEST_KEY, Compact, CompactParams
from quaso.tools.fs import ListDir, ReadFile
from quaso.tools.registry import ToolRegistry

from .conftest import FakeProvider


async def _allow(request):
    return "allow"


def _agent(tmp_path, provider, context=None, tool_context=None):
    return Agent(
        provider=provider,
        tools=ToolRegistry.default(Config()),
        permissions=PermissionPolicy(PermissionsConfig(), _allow),
        session=Session("sys", root=tmp_path, persist=False),
        tool_context=tool_context or ToolContext(cwd=tmp_path),
        config=AgentConfig(),
        context=context or ContextManager(ContextConfig(), max_tokens=1000),
    )


async def _drain(agent, prompt):
    return [event async for event in agent.run(prompt)]


# Phase 1: per-tool budgets


def test_tool_budget_prefers_class_default_over_global(tmp_path):
    ctx = ToolContext(cwd=tmp_path, max_output_chars=8_000)
    assert ReadFile().output_limit(ctx) == 12_000
    assert ListDir().output_limit(ctx) == 4_000


def test_tool_budget_config_override_wins(tmp_path):
    ctx = ToolContext(
        cwd=tmp_path,
        max_output_chars=8_000,
        tool_output_chars={"read_file": 500},
    )
    assert ReadFile().output_limit(ctx) == 500


def test_tool_without_own_budget_takes_the_global(tmp_path):
    ctx = ToolContext(cwd=tmp_path, max_output_chars=8_000)
    assert Compact().output_limit(ctx) == 8_000


@pytest.mark.asyncio
async def test_the_budget_is_applied_to_what_a_tool_returns(tmp_path):
    """Tools shape their output; the loop applies the budget to it.

    Bounding inside the tool would discard the full text before anything
    could decide whether to keep a copy of it.
    """
    (tmp_path / "big.txt").write_text(
        "\n".join(f"line {i}" for i in range(500))
    )
    ctx = ToolContext(cwd=tmp_path, tool_output_chars={"read_file": 200})
    tool = ReadFile()

    raw = await tool.run(ReadFile.Params(path="big.txt"), ctx)
    assert len(raw) > 200, "the tool returns its full shaped output"

    bounded = truncate(raw, tool.output_limit(ctx))
    assert "truncated" in bounded
    assert len(bounded) <= 200


# Phase 2: context proprioception


def test_no_notice_while_context_is_roomy(tmp_path):
    agent = _agent(tmp_path, FakeProvider([]))
    assert agent._context_notice() is None


def test_notice_appears_once_context_is_tight(tmp_path):
    context = ContextManager(
        ContextConfig(notice_threshold=0.5), max_tokens=1000
    )
    agent = _agent(tmp_path, FakeProvider([]), context=context)
    context.last_prompt_tokens = 700
    notice = agent._context_notice()
    assert notice is not None
    assert notice.role == "system"
    assert "70%" in notice.content
    assert "compact" in notice.content


@pytest.mark.asyncio
async def test_notice_is_sent_but_never_stored(tmp_path):
    """It must reach the provider without polluting the transcript."""
    context = ContextManager(
        ContextConfig(notice_threshold=0.5, auto_compact=False),
        max_tokens=1000,
    )
    provider = FakeProvider([Message(role="assistant", content="ok")])
    agent = _agent(tmp_path, provider, context=context)
    context.last_prompt_tokens = 900

    await _drain(agent, "hello")

    sent = provider.requests[0]
    assert sent[-1].role == "system" and "context:" in sent[-1].content
    assert not any(
        m.role == "system" and "context:" in m.content
        for m in agent.session.messages
    )


@pytest.mark.asyncio
async def test_notice_is_appended_last_so_the_prefix_is_stable(tmp_path):
    """Prefix caching breaks if earlier messages are rewritten."""
    context = ContextManager(
        ContextConfig(notice_threshold=0.5, auto_compact=False),
        max_tokens=1000,
    )
    provider = FakeProvider([Message(role="assistant", content="ok")])
    agent = _agent(tmp_path, provider, context=context)
    context.last_prompt_tokens = 900

    await _drain(agent, "hello")

    sent = provider.requests[0]
    prefix = sent[:-1]
    # Everything before the notice is the stored history, untouched.
    assert prefix == agent.session.messages[: len(prefix)]
    assert sent[0].role == "system" and "context:" not in sent[0].content


# Phase 3: compaction as a tool


@pytest.mark.asyncio
async def test_compact_tool_requests_rather_than_acting(tmp_path):
    """It must not rewrite history while the loop is mid-batch."""
    called = False

    async def compaction():
        nonlocal called
        called = True

    ctx = ToolContext(cwd=tmp_path, compact=compaction)
    out = await Compact().run(CompactParams(reason="done"), ctx)

    assert ctx.extra[REQUEST_KEY] is True
    assert called is False
    assert "once this step finishes" in out


@pytest.mark.asyncio
async def test_compaction_runs_after_the_batch_without_orphaning(tmp_path):
    """Tool results must all land before history is rewritten."""
    call = ToolCall(id="c1", name="compact", arguments={"reason": "done"})
    other = ToolCall(id="c2", name="list_dir", arguments={})
    provider = FakeProvider(
        [
            Message(role="assistant", tool_calls=[call, other]),
            Message(role="assistant", content="SUMMARY"),
            Message(role="assistant", content="finished"),
        ]
    )
    context = ContextManager(
        ContextConfig(auto_compact=False, keep_recent_messages=2),
        max_tokens=1000,
    )
    tool_context = ToolContext(cwd=tmp_path)
    agent = _agent(
        tmp_path, provider, context=context, tool_context=tool_context
    )
    tool_context.compact = agent.compact

    events = await _drain(agent, "do two things")

    assert tool_context.extra.get(REQUEST_KEY) is None
    from quaso.events import CompactionEvent

    assert any(isinstance(e, CompactionEvent) for e in events)
    # Every surviving assistant tool call still has its result.
    answered = {
        m.tool_call_id for m in agent.session.messages if m.role == "tool"
    }
    for message in agent.session.messages:
        for pending in message.tool_calls:
            assert pending.id in answered


@pytest.mark.asyncio
async def test_compact_tool_without_a_hook_reports_clearly(tmp_path):
    ctx = ToolContext(cwd=tmp_path, compact=None)
    from quaso.tools.base import ToolError

    with pytest.raises(ToolError, match="not available"):
        await Compact().run(CompactParams(), ctx)


def test_compact_is_registered_and_needs_no_permission():
    registry = ToolRegistry.default(Config())
    tool = registry.get("compact")
    assert tool is not None
    assert tool.mutates is False


def test_threshold_is_now_a_backstop_not_the_primary_trigger():
    assert ContextConfig().compact_threshold == 0.7
    assert AgentConfig().max_tool_output_chars == 8_000
