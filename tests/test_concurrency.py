"""Running the tool calls a model issues together, together.

The model batches calls in about a third of its turns, and those batches
are almost entirely reads and fetches: work that waits on disk or the
network rather than the GPU. Running them one after another spends the
sum of their latencies for no reason.

What must not change is the order the results come back in, and what must
not happen is two tools that touch shared state running at once.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from pydantic import BaseModel

from quaso.agent.loop import Agent, concurrent_groups
from quaso.config import PermissionsConfig
from quaso.messages import ToolCall
from quaso.permissions import PermissionPolicy
from quaso.session import Session
from quaso.tools.base import Tool, ToolContext, ToolError
from quaso.tools.registry import ToolRegistry

DELAY = 0.15


class Params(BaseModel):
    tag: str = ""


class Slow(Tool):
    """A read that waits on something other than the processor."""

    name = "slow"
    description = "waits"
    Params = Params
    concurrent = True

    async def run(self, params: Params, ctx: ToolContext) -> str:
        await asyncio.sleep(DELAY)
        return f"slow:{params.tag}"


class SlowWrite(Slow):
    name = "slow_write"
    mutates = True
    concurrent = False

    async def run(self, params: Params, ctx: ToolContext) -> str:
        await asyncio.sleep(DELAY)
        return f"wrote:{params.tag}"


class Boom(Tool):
    name = "boom"
    description = "raises"
    Params = Params
    concurrent = True

    async def run(self, params: Params, ctx: ToolContext) -> str:
        raise ToolError("no")


def _agent(tmp_path: Path, *tools: Tool, asker=None) -> Agent:
    return Agent(
        provider=None,
        tools=ToolRegistry(list(tools)),
        permissions=PermissionPolicy(
            PermissionsConfig(mode="yolo"), asker or (lambda request: None)
        ),
        session=Session("system", root=tmp_path, persist=False),
        tool_context=ToolContext(cwd=tmp_path),
    )


def _calls(*names: str) -> list[ToolCall]:
    return [
        ToolCall(id=str(i), name=name, arguments={"tag": str(i)})
        for i, name in enumerate(names)
    ]


class TestGrouping:
    def test_consecutive_safe_calls_form_one_group(self):
        tools = ToolRegistry([Slow()])
        groups = concurrent_groups(_calls("slow", "slow", "slow"), tools)
        assert [len(g) for g in groups] == [3]

    def test_a_mutating_call_splits_the_run(self):
        """A write between two reads may be what the second read sees."""
        tools = ToolRegistry([Slow(), SlowWrite()])
        calls = _calls("slow", "slow_write", "slow")
        groups = concurrent_groups(calls, tools)
        assert [len(g) for g in groups] == [1, 1, 1]

    def test_reads_before_a_write_still_group(self):
        tools = ToolRegistry([Slow(), SlowWrite()])
        calls = _calls("slow", "slow", "slow_write")
        assert [len(g) for g in concurrent_groups(calls, tools)] == [2, 1]

    def test_an_unknown_tool_stands_alone(self):
        tools = ToolRegistry([Slow()])
        calls = _calls("slow", "nope", "slow")
        assert [len(g) for g in concurrent_groups(calls, tools)] == [1, 1, 1]

    def test_order_is_never_rearranged(self):
        tools = ToolRegistry([Slow(), SlowWrite()])
        calls = _calls("slow", "slow_write", "slow", "slow")
        flat = [c for group in concurrent_groups(calls, tools) for c in group]
        assert [c.id for c in flat] == [c.id for c in calls]


class TestItActuallyOverlaps:
    @pytest.mark.asyncio
    async def test_safe_calls_take_the_longest_not_the_sum(self, tmp_path):
        agent = _agent(tmp_path, Slow())
        calls = _calls("slow", "slow", "slow")
        started = time.monotonic()
        results = await agent._execute_group(calls)
        elapsed = time.monotonic() - started
        assert len(results) == 3
        assert elapsed < DELAY * 2, f"{elapsed:.2f}s suggests serial"

    @pytest.mark.asyncio
    async def test_mutating_calls_are_not_overlapped(self, tmp_path):
        """Two writes at once is exactly what must not happen."""
        agent = _agent(tmp_path, SlowWrite())
        groups = concurrent_groups(
            _calls("slow_write", "slow_write"), agent.tools
        )
        started = time.monotonic()
        for group in groups:
            await agent._execute_group(group)
        elapsed = time.monotonic() - started
        assert elapsed >= DELAY * 2, f"{elapsed:.2f}s suggests parallel"


class TestResultsStayInOrder:
    @pytest.mark.asyncio
    async def test_results_follow_the_calls_not_the_finishing_order(
        self, tmp_path
    ):
        class Fast(Slow):
            name = "fast"

            async def run(self, params: Params, ctx: ToolContext) -> str:
                return f"fast:{params.tag}"

        agent = _agent(tmp_path, Slow(), Fast())
        calls = _calls("slow", "fast")
        results = await agent._execute_group(calls)
        assert results[0][0] == "slow:0"
        assert results[1][0] == "fast:1"

    @pytest.mark.asyncio
    async def test_one_failure_does_not_cancel_the_others(self, tmp_path):
        agent = _agent(tmp_path, Slow(), Boom())
        results = await agent._execute_group(_calls("boom", "slow"))
        assert results[0][1] is True
        assert results[1] == ("slow:1", False, "")


class TestSharedStateIsNotParallelised:
    def test_the_tools_that_must_not_overlap_say_so(self):
        """mutates is the wrong signal: these are all read-only by it."""
        from quaso.config import Config

        unsafe = {"task", "compact", "ask", "todo_write"}
        for tool in ToolRegistry.default(Config()):
            if tool.name in unsafe:
                assert not tool.concurrent, tool.name

    def test_the_tools_the_model_batches_are_safe(self):
        """Every tool seen in a real batch, from the session transcripts."""
        from quaso.config import Config

        batched = {
            "read_file",
            "list_dir",
            "glob",
            "grep",
            "web_search",
            "fetch_url",
        }
        found = {
            t.name for t in ToolRegistry.default(Config()) if t.concurrent
        }
        assert batched <= found


class TestThroughTheLoop:
    """The grouping is only worth anything if agent.run uses it."""

    @pytest.mark.asyncio
    async def test_a_batched_turn_overlaps_end_to_end(self, tmp_path):
        from quaso.events import ToolCallEvent, ToolResultEvent
        from quaso.messages import Message

        from .conftest import FakeProvider

        calls = _calls("slow", "slow", "slow")
        provider = FakeProvider(
            [
                Message(role="assistant", content="", tool_calls=calls),
                Message(role="assistant", content="done"),
            ]
        )
        agent = Agent(
            provider=provider,
            tools=ToolRegistry([Slow()]),
            permissions=PermissionPolicy(
                PermissionsConfig(mode="yolo"), lambda request: None
            ),
            session=Session("system", root=tmp_path, persist=False),
            tool_context=ToolContext(cwd=tmp_path),
        )

        started = time.monotonic()
        events = [event async for event in agent.run("go")]
        elapsed = time.monotonic() - started

        assert elapsed < DELAY * 2, f"{elapsed:.2f}s suggests serial"

        # Announced together, then answered in the order the model asked.
        kinds = [
            type(e).__name__
            for e in events
            if isinstance(e, ToolCallEvent | ToolResultEvent)
        ]
        assert kinds == ["ToolCallEvent"] * 3 + ["ToolResultEvent"] * 3
        answers = [
            m.content for m in agent.session.messages if m.role == "tool"
        ]
        assert answers == ["slow:0", "slow:1", "slow:2"]
