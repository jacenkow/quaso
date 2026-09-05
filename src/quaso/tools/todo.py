"""Todo list tool.

Small models lose the thread on multi-step work; keeping the plan in
context is a cheap fix.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from quaso.tools.base import Tool, ToolContext, ToolError

Status = Literal["pending", "in_progress", "completed"]

_MARKS = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}


class TodoItem(BaseModel):
    content: str = Field(description="What needs to be done")
    status: Status = Field(default="pending", description="Item status")


class TodoWriteParams(BaseModel):
    todos: list[TodoItem] = Field(
        description="The full list, replacing any previous one"
    )


class TodoWrite(Tool):
    name = "todo_write"
    description = (
        "Record or update your task list for multi-step work. Always send "
        "the full list; it replaces the previous one. Keep exactly one item "
        "in_progress and mark items completed as soon as they are done."
    )
    Params = TodoWriteParams
    mutates = False

    def primary_argument(self, params: TodoWriteParams) -> str:
        for item in params.todos:
            if item.status == "in_progress":
                return item.content
        return f"{len(params.todos)} items"

    async def run(self, params: TodoWriteParams, ctx: ToolContext) -> str:
        active = sum(1 for i in params.todos if i.status == "in_progress")
        if active > 1:
            raise ToolError("Only one todo may be in_progress at a time")
        ctx.extra["todos"] = [item.model_dump() for item in params.todos]
        return render(ctx.extra['todos'])


def render(todos: list[dict]) -> str:
    if not todos:
        return "(no todos)"
    done = sum(1 for t in todos if t['status'] == "completed")
    lines = [f"{_MARKS[t['status']]} {t['content']}" for t in todos]
    return "\n".join(lines) + f"\n({done}/{len(todos)} complete)"
