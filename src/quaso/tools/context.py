"""Context management exposed to the model as a tool.

Compaction driven by a token threshold fires wherever the counter happens
to trip, often mid-investigation. Letting the model call it at a milestone
it recognises gives a better cut point. The threshold stays as a backstop.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from quaso.tools.base import Tool, ToolContext, ToolError

# Set by this tool, read by the agent loop once the current batch of tool
# calls has drained. Compacting inline would rewrite the history that the
# loop is still appending results to.
REQUEST_KEY = "compact_requested"


class CompactParams(BaseModel):
    reason: str = Field(
        default="",
        description="What you have just finished, in a few words",
    )


class Compact(Tool):
    name = "compact"
    description = (
        "Summarise the conversation so far and drop the detail, freeing "
        "context. Call this when you have finished a sub-task and are about "
        "to start another, not in the middle of one. Recent messages and "
        "your task list are preserved."
    )
    Params = CompactParams
    mutates = False

    def primary_argument(self, params: CompactParams) -> str:
        return params.reason

    async def run(self, params: CompactParams, ctx: ToolContext) -> str:
        if ctx.compact is None:
            raise ToolError("Compaction is not available in this session")
        ctx.extra[REQUEST_KEY] = True
        return (
            "Compaction will run once this step finishes. Continue; the "
            "next turn starts from a summary."
        )
