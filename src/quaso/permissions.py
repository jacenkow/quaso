"""Permission policy for tool calls.

Rules are fnmatch patterns tested against both "tool_name" and
"tool_name(primary_argument)", e.g. "bash(git status*)".

Two things trigger a prompt: a tool that changes something, and a call
reaching outside the working directory. The second matters because
reading is otherwise unrestricted, and an agent that can silently read
any file and silently run a web search can be talked into carrying one
out through the other.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Literal

from pydantic import BaseModel

from quaso.config import PermissionsConfig
from quaso.tools.base import Tool, ToolContext, resolve_path

Answer = Literal["allow", "always", "deny"]

_EDIT_TOOLS = ("write_file", "edit_file")

# Shell grammar is too broad to validate with fnmatch. A configured rule may
# approve only a simple command; anything with control syntax needs an explicit
# decision. This is deliberately conservative, including quoted metacharacters.
_SHELL_CONTROL = re.compile(r"[;&|<>\r\n`]|\$\(")


@dataclass
class PermissionRequest:
    tool_name: str
    primary_arg: str
    detail: str = ""
    outside_workspace: bool = False
    # Running this sends something off the machine, which is the other
    # way out of a workspace and the one a local model was meant to
    # avoid.
    leaves_machine: bool = False


Asker = Callable[[PermissionRequest], Awaitable[Answer]]


@dataclass
class Decision:
    allowed: bool
    reason: str = ""


def _matches(rules: list[str], tool_name: str, primary_arg: str) -> bool:
    subject = f"{tool_name}({primary_arg})"
    return any(
        fnmatch(tool_name, rule) or fnmatch(subject, rule) for rule in rules
    )


def _has_command_rule(rules: list[str], tool_name: str) -> bool:
    """Whether rules contain an argument-scoped rule for this tool."""
    for rule in rules:
        tool_pattern, separator, _ = rule.partition("(")
        if separator and fnmatch(tool_name, tool_pattern):
            return True
    return False


def _has_shell_control(command: str) -> bool:
    return bool(_SHELL_CONTROL.search(command))


def escapes_workspace(
    tool: Tool, params: BaseModel, ctx: ToolContext | None
) -> bool:
    """Whether this call touches anything outside the working directory."""
    if ctx is None:
        return False
    workspace = ctx.cwd.resolve()
    for raw in tool.paths(params):
        if not raw:
            continue
        if not resolve_path(raw, ctx).is_relative_to(workspace):
            return True
    return False


class PermissionPolicy:
    def __init__(self, config: PermissionsConfig, asker: Asker) -> None:
        self._config = config
        self._asker = asker
        self._session_allow: list[str] = []

    async def check(
        self,
        tool: Tool,
        params: BaseModel,
        ctx: ToolContext | None = None,
        detail: str = "",
    ) -> Decision:
        primary = tool.primary_argument(params)
        outside = escapes_workspace(tool, params, ctx)
        network = tool.network

        if _matches(self._config.deny, tool.name, primary):
            return Decision(False, "denied by configured deny rule")
        if (
            tool.name == "bash"
            and _has_shell_control(primary)
            and _has_command_rule(self._config.deny, tool.name)
        ):
            return Decision(
                False,
                "compound shell command cannot be checked safely against "
                "configured deny rules",
            )
        if self._config.mode == "readonly" and tool.mutates:
            return Decision(False, "readonly mode forbids mutating tools")
        if self._config.mode == "yolo" and not outside and not network:
            # yolo means "stop asking about my project", not "anything
            # goes". Leaving the workspace is still a question, because
            # the sandbox cannot answer it: file tools run inside quaso
            # itself, so nothing but this stands between the model and
            # the rest of the disk.
            return Decision(True)
        if not tool.mutates and not outside and not network:
            return Decision(True)
        if (
            self._config.mode == "acceptEdits"
            and tool.name in _EDIT_TOOLS
            and not outside
        ):
            return Decision(True)
        configured_allow = _matches(
            self._config.allow, tool.name, primary
        ) and not (tool.name == "bash" and _has_shell_control(primary))
        session_allow = _matches(self._session_allow, tool.name, primary)
        if configured_allow or session_allow:
            return Decision(True)

        answer = await self._asker(
            PermissionRequest(tool.name, primary, detail, outside, network)
        )
        if answer == "always":
            # Scoped to the tool, so granting one outside-workspace read
            # does not silently bless every later one. A search is
            # allowed to stick: asking before each one would teach
            # people to agree without reading, which is worse than not
            # asking at all.
            if not outside:
                self._session_allow.append(tool.name)
            return Decision(True)
        if answer == "allow":
            return Decision(True)
        return Decision(False, "denied by user")
