from __future__ import annotations

import pytest

from quaso.config import Config
from quaso.tools.base import ToolContext, ToolError
from quaso.tools.registry import ToolRegistry
from quaso.tools.task import Task, TaskParams
from quaso.tools.todo import TodoWrite, TodoWriteParams, render


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(cwd=tmp_path)


@pytest.mark.asyncio
async def test_todo_write_renders_and_stores(ctx):
    params = TodoWriteParams.model_validate(
        {
            "todos": [
                {"content": "Read the config", "status": "completed"},
                {"content": "Add the tool", "status": "in_progress"},
                {"content": "Write tests", "status": "pending"},
            ]
        }
    )
    out = await TodoWrite().run(params, ctx)
    assert "[x] Read the config" in out
    assert "[~] Add the tool" in out
    assert "[ ] Write tests" in out
    assert "(1/3 complete)" in out
    assert len(ctx.extra["todos"]) == 3


@pytest.mark.asyncio
async def test_todo_rejects_multiple_in_progress(ctx):
    params = TodoWriteParams.model_validate(
        {
            "todos": [
                {"content": "A", "status": "in_progress"},
                {"content": "B", "status": "in_progress"},
            ]
        }
    )
    with pytest.raises(ToolError, match="one todo"):
        await TodoWrite().run(params, ctx)


def test_todo_primary_argument_is_current_item():
    params = TodoWriteParams.model_validate(
        {
            "todos": [
                {"content": "A", "status": "completed"},
                {"content": "B", "status": "in_progress"},
            ]
        }
    )
    assert TodoWrite().primary_argument(params) == "B"


def test_render_empty():
    assert render([]) == "(no todos)"


@pytest.mark.asyncio
async def test_task_requires_a_subagent(ctx):
    with pytest.raises(ToolError, match="not available"):
        await Task().run(TaskParams(description="d", prompt="p"), ctx)


@pytest.mark.asyncio
async def test_task_delegates_to_subagent(tmp_path):
    seen = []

    async def fake_subagent(prompt: str) -> str:
        seen.append(prompt)
        return "the subagent report"

    ctx = ToolContext(cwd=tmp_path, subagent=fake_subagent)
    out = await Task().run(
        TaskParams(description="find x", prompt="where is x?"), ctx
    )
    assert out == "the subagent report"
    assert seen == ["where is x?"]


def test_registry_includes_new_tools():
    registry = ToolRegistry.default(Config())
    names = registry.names()
    for expected in (
        "read_file",
        "bash",
        "todo_write",
        "task",
        "web_search",
        "fetch_url",
    ):
        assert expected in names


def test_registry_add_and_remove():
    registry = ToolRegistry.default(Config())
    before = len(registry)
    registry.remove("task")
    assert len(registry) == before - 1 and registry.get("task") is None


def test_registry_schemas_are_well_formed():
    registry = ToolRegistry.default(Config())
    for schema in registry.schemas():
        assert schema["type"] == "function"
        function = schema["function"]
        assert function["name"] and function["description"]
        assert function["parameters"]["type"] == "object"
