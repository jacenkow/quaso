"""Failed tool calls cost context exactly like successful ones.

A validation error quotes the offending argument back in full, so an
unbounded error path is a context leak that the output budget was meant
to have closed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from quaso.agent.loop import Agent
from quaso.config import PermissionsConfig
from quaso.messages import ToolCall
from quaso.permissions import PermissionPolicy
from quaso.session import Session
from quaso.tools.base import Tool, ToolContext, ToolError
from quaso.tools.registry import ToolRegistry

LIMIT = 300


class Params(BaseModel):
    text: str = ""


class Boom(Tool):
    name = "boom"
    description = "raises"
    Params = Params
    mutates = False
    max_output_chars = LIMIT

    async def run(self, params: Params, ctx: ToolContext) -> str:
        raise ToolError("X" * 50_000)


def _agent(tmp_path: Path) -> Agent:
    return Agent(
        provider=None,
        tools=ToolRegistry([Boom()]),
        permissions=PermissionPolicy(
            PermissionsConfig(mode="yolo"), lambda request: None
        ),
        session=Session("system", root=tmp_path, persist=False),
        tool_context=ToolContext(cwd=tmp_path, max_output_chars=LIMIT),
    )


class TestErrorsAreBounded:
    @pytest.mark.asyncio
    async def test_a_raised_error_is_cut_to_the_budget(self, tmp_path):
        output, is_error, _ = await _agent(tmp_path)._execute(
            ToolCall(id="1", name="boom", arguments={})
        )
        assert is_error
        assert len(output) <= LIMIT

    @pytest.mark.asyncio
    async def test_invalid_arguments_are_cut_to_the_budget(self, tmp_path):
        """Pydantic echoes the value it rejected, however large."""
        output, is_error, _ = await _agent(tmp_path)._execute(
            ToolCall(id="1", name="boom", arguments={"text": ["x" * 50_000]})
        )
        assert is_error
        assert len(output) <= LIMIT

    @pytest.mark.asyncio
    async def test_an_unknown_tool_is_cut_to_the_budget(self, tmp_path):
        output, is_error, _ = await _agent(tmp_path)._execute(
            ToolCall(id="1", name="nope", arguments={})
        )
        assert is_error
        assert len(output) <= LIMIT
