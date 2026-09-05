"""Shell command execution.

The tool decides what to report; an executor decides what the command
may touch while producing it. Everything about stopping a runaway
command and bounding its output lives in the executor, because those
are the same whether or not a sandbox is in force.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from quaso.execution import DirectExecutor
from quaso.tools.base import Tool, ToolContext, ToolError

# What is read from a stream before the rest is discarded. Generous next
# to any tool budget, so the budget still decides what the model sees;
# this only stops a runaway command from being held in memory in full.
MAX_STREAM_BYTES = 2_000_000

_FALLBACK = DirectExecutor()


class BashParams(BaseModel):
    command: str = Field(description="Shell command to execute")
    timeout: int = Field(
        default=60, gt=0, le=600, description="Timeout in seconds"
    )


class Bash(Tool):
    name = "bash"
    description = (
        "Run a shell command in the project directory. Prefer the file "
        "tools for reading and searching."
    )
    Params = BashParams
    mutates = True

    async def run(self, params: BashParams, ctx: ToolContext) -> str:
        executor = ctx.executor or _FALLBACK
        try:
            done = await executor.run(
                params.command, ctx.cwd, params.timeout, MAX_STREAM_BYTES
            )
        except TimeoutError:
            raise ToolError(
                f"Command timed out after {params.timeout}s"
            ) from None

        parts = []
        if done.stdout:
            parts.append(done.stdout.decode(errors="replace"))
        if done.stderr:
            parts.append(f"[stderr]\n{done.stderr.decode(errors='replace')}")
        if done.dropped:
            parts.append(
                f"[truncated: {done.dropped} further bytes were not read]"
            )
        parts.append(f"[exit code: {done.exit_code}]")
        return "\n".join(parts)
