"""Starting point for a new tool and its tests.

Copy the tool into src/quaso/tools/, the tests into tests/, then delete
whatever does not apply.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from quaso.tools.base import Tool, ToolContext, ToolError, resolve_path


class ExampleParams(BaseModel):
    path: str = Field(description="Path to operate on")


class Example(Tool):
    name = "example"
    description = "One sentence the model will act on."
    Params = ExampleParams
    mutates = False
    max_output_chars = 6_000

    def paths(self, params: ExampleParams) -> list[str]:
        return [params.path]

    async def run(self, params: ExampleParams, ctx: ToolContext) -> str:
        target = resolve_path(params.path, ctx)
        if not target.is_file():
            raise ToolError(f"Not a file: {target}")
        return target.read_text(errors="replace")


# --- tests ---------------------------------------------------------------

TESTS = '''
import pytest

from quaso.config import PermissionsConfig
from quaso.permissions import PermissionPolicy
from quaso.tools.base import ToolContext, ToolError
from quaso.tools.example import Example, ExampleParams


@pytest.mark.asyncio
async def test_reads_a_file(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    ctx = ToolContext(cwd=tmp_path)
    assert "hello" in await Example().run(ExampleParams(path="a.txt"), ctx)


@pytest.mark.asyncio
async def test_missing_file_is_a_tool_error(tmp_path):
    ctx = ToolContext(cwd=tmp_path)
    with pytest.raises(ToolError, match="Not a file"):
        await Example().run(ExampleParams(path="nope.txt"), ctx)


@pytest.mark.asyncio
async def test_reaching_outside_the_workspace_prompts(tmp_path):
    async def deny(request):
        return "deny"

    policy = PermissionPolicy(PermissionsConfig(mode="default"), deny)
    decision = await policy.check(
        Example(), ExampleParams(path="/etc/hosts"), ToolContext(cwd=tmp_path)
    )
    assert not decision.allowed
'''
