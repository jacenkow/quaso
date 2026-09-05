"""Subagent tool.

Exploration burns context in tool output while contributing a paragraph of
signal, so it runs in a nested agent with its own window. The parent
supplies the runner through ToolContext, keeping this module free of an
import cycle.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from quaso.tools.base import Tool, ToolContext, ToolError


class TaskParams(BaseModel):
    description: str = Field(description="Short label, 3-5 words")
    prompt: str = Field(
        description=(
            "Full instruction for the subagent. It shares your tools but "
            "starts with no memory of this conversation."
        )
    )


class Task(Tool):
    name = "task"
    description = (
        "Delegate a self-contained search or research task to a subagent "
        "with its own context window. Use it for open-ended exploration "
        "where you only need the conclusion. The subagent cannot ask "
        "questions, so give complete instructions."
    )
    Params = TaskParams
    mutates = False

    def primary_argument(self, params: TaskParams) -> str:
        return params.description

    async def run(self, params: TaskParams, ctx: ToolContext) -> str:
        if ctx.subagent is None:
            raise ToolError("Subagents are not available in this session")
        return await ctx.subagent(params.prompt)
