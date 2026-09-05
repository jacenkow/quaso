from __future__ import annotations

from pathlib import Path

import pytest

from quaso.agent.loop import Agent
from quaso.config import AgentConfig, PermissionsConfig
from quaso.events import ErrorEvent, TextDelta, ToolResultEvent
from quaso.messages import Message, ToolCall, user
from quaso.permissions import PermissionPolicy
from quaso.session import Session
from quaso.tools.base import ToolContext
from quaso.tools.registry import ToolRegistry

from .conftest import FakeProvider


async def _deny_asker(request):
    return "deny"


async def _allow_asker(request):
    return "allow"


def _agent(
    tmp_path: Path, provider: FakeProvider, asker=_allow_asker, **agent_opts
) -> Agent:
    return Agent(
        provider=provider,
        tools=ToolRegistry.default(),
        permissions=PermissionPolicy(PermissionsConfig(), asker),
        session=Session("test system prompt", root=tmp_path, persist=False),
        tool_context=ToolContext(cwd=tmp_path),
        config=AgentConfig(**agent_opts),
    )


async def _drain(agent: Agent, prompt: str):
    return [event async for event in agent.run(prompt)]


@pytest.mark.asyncio
async def test_plain_answer_no_tools(tmp_path):
    provider = FakeProvider([Message(role="assistant", content="hello!")])
    agent = _agent(tmp_path, provider)
    events = await _drain(agent, "hi")
    assert any(isinstance(e, TextDelta) and e.text == "hello!" for e in events)
    # system + user + assistant
    assert [m.role for m in agent.session.messages] == [
        "system",
        "user",
        "assistant",
    ]


@pytest.mark.asyncio
async def test_tool_call_roundtrip(tmp_path):
    (tmp_path / "hello.txt").write_text("one\ntwo\n")
    call = ToolCall(id="c1", name="read_file", arguments={"path": "hello.txt"})
    provider = FakeProvider(
        [
            Message(role="assistant", tool_calls=[call]),
            Message(role="assistant", content="the file has two lines"),
        ]
    )
    agent = _agent(tmp_path, provider)
    events = await _drain(agent, "read hello.txt")

    results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(results) == 1 and not results[0].is_error
    assert "one" in results[0].output
    # Second round-trip saw the tool result message.
    assert provider.requests[1][-1].role == "tool"
    assert [m.role for m in agent.session.messages] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]


@pytest.mark.asyncio
async def test_invalid_arguments_fed_back(tmp_path):
    call = ToolCall(id="c1", name="read_file", arguments={"wrong_key": 42})
    provider = FakeProvider(
        [
            Message(role="assistant", tool_calls=[call]),
            Message(role="assistant", content="sorry"),
        ]
    )
    agent = _agent(tmp_path, provider)
    events = await _drain(agent, "go")
    results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert results[0].is_error
    assert "invalid arguments" in results[0].output


@pytest.mark.asyncio
async def test_unknown_tool_fed_back(tmp_path):
    call = ToolCall(id="c1", name="teleport", arguments={})
    provider = FakeProvider(
        [
            Message(role="assistant", tool_calls=[call]),
            Message(role="assistant", content="ok"),
        ]
    )
    agent = _agent(tmp_path, provider)
    events = await _drain(agent, "go")
    results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert results[0].is_error and "unknown tool" in results[0].output


@pytest.mark.asyncio
async def test_denied_mutating_tool(tmp_path):
    call = ToolCall(id="c1", name="bash", arguments={"command": "touch x"})
    provider = FakeProvider(
        [
            Message(role="assistant", tool_calls=[call]),
            Message(role="assistant", content="ok"),
        ]
    )
    agent = _agent(tmp_path, provider, asker=_deny_asker)
    events = await _drain(agent, "go")
    results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert results[0].is_error and "permission denied" in results[0].output
    assert not (tmp_path / "x").exists()


@pytest.mark.asyncio
async def test_flush_partial_records_interrupted_output(tmp_path):
    """An interrupted turn must not leave a user message with no reply."""
    provider = FakeProvider(
        [Message(role="assistant", content="partial answer")]
    )
    agent = _agent(tmp_path, provider)
    agent.session.append(user("a question"))
    agent._partial = ["partial ", "answer"]

    assert agent.flush_partial() is True
    last = agent.session.messages[-1]
    assert last.role == "assistant"
    assert "partial answer" in last.content and "interrupted" in last.content
    # Nothing buffered any more, so a second flush is a no-op.
    assert agent.flush_partial() is False


@pytest.mark.asyncio
async def test_partial_is_cleared_on_normal_completion(tmp_path):
    provider = FakeProvider([Message(role="assistant", content="all done")])
    agent = _agent(tmp_path, provider)
    await _drain(agent, "hi")
    assert agent.flush_partial() is False
    assert [m.role for m in agent.session.messages] == [
        "system",
        "user",
        "assistant",
    ]


@pytest.mark.asyncio
async def test_max_iterations_guard(tmp_path):
    call = ToolCall(id="c1", name="list_dir", arguments={})
    provider = FakeProvider(
        [Message(role="assistant", tool_calls=[call]) for _ in range(5)]
    )
    agent = _agent(tmp_path, provider, max_iterations=3)
    events = await _drain(agent, "loop forever")
    assert any(isinstance(e, ErrorEvent) for e in events)
    assert len(provider.requests) == 3
