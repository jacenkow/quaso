"""Shell hooks around tool execution.

A pre-tool-use hook exiting non-zero blocks the call and its stderr becomes
the reason shown to the model. The tool name and arguments arrive as JSON
on stdin.

Every hook matching a call runs, in the order configured, until one
blocks. An audit hook that prints and exits zero therefore cannot mask a
policy hook configured after it.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from quaso.config import HookConfig, HooksConfig


@dataclass
class HookOutcome:
    blocked: bool = False
    reason: str = ""
    output: str = ""


def _matches(hook: HookConfig, tool_name: str) -> bool:
    return any(
        fnmatch(tool_name, part.strip()) for part in hook.matcher.split("|")
    )


async def _run(
    hook: HookConfig, payload: dict[str, Any], cwd: Path
) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_shell(
        hook.command,
        cwd=cwd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(json.dumps(payload).encode()),
            timeout=hook.timeout,
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 1, "", f"hook timed out after {hook.timeout}s"
    return (
        proc.returncode or 0,
        stdout.decode(errors="replace").strip(),
        stderr.decode(errors="replace").strip(),
    )


class HookRunner:
    def __init__(self, config: HooksConfig, cwd: Path) -> None:
        self.config = config
        self.cwd = cwd

    async def pre_tool_use(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> HookOutcome:
        payload = {
            "event": "pre_tool_use",
            "tool": tool_name,
            "arguments": arguments,
        }
        # Every matching hook runs, because a hook that only reports must
        # not stand in for an enforcement hook behind it. Only a block
        # ends the chain, since the call is refused from that point on.
        notes: list[str] = []
        for hook in self.config.pre_tool_use:
            if not _matches(hook, tool_name):
                continue
            code, stdout, stderr = await _run(hook, payload, self.cwd)
            if code != 0:
                reason = stderr or stdout or f"hook exited {code}"
                return HookOutcome(blocked=True, reason=reason)
            if stdout:
                notes.append(stdout)
        return HookOutcome(output="\n".join(notes))

    async def post_tool_use(
        self, tool_name: str, arguments: dict[str, Any], result: str
    ) -> str:
        payload = {
            "event": "post_tool_use",
            "tool": tool_name,
            "arguments": arguments,
            "result": result[:10_000],
        }
        notices: list[str] = []
        for hook in self.config.post_tool_use:
            if not _matches(hook, tool_name):
                continue
            _, stdout, stderr = await _run(hook, payload, self.cwd)
            if stdout or stderr:
                notices.append(stdout or stderr)
        return "\n".join(notices)
