"""Built-in tools merged with `quaso.tools` entry points."""

from __future__ import annotations

from collections.abc import Iterator
from importlib.metadata import entry_points
from typing import Any

from quaso.config import Config
from quaso.tools.ask import Ask
from quaso.tools.base import Tool
from quaso.tools.context import Compact
from quaso.tools.fs import EditFile, ListDir, ReadFile, WriteFile
from quaso.tools.search import Glob, Grep
from quaso.tools.shell import Bash
from quaso.tools.skill import LoadSkill
from quaso.tools.task import Task
from quaso.tools.todo import TodoWrite
from quaso.tools.web import FetchUrl, WebSearch

_BUILTINS: list[type[Tool]] = [
    ReadFile,
    ListDir,
    Glob,
    Grep,
    Bash,
    WriteFile,
    EditFile,
    TodoWrite,
    Task,
    Compact,
    LoadSkill,
    Ask,
]


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    @classmethod
    def default(cls, config: Config | None = None) -> ToolRegistry:
        config = config or Config()
        tools: list[Tool] = [tool_cls() for tool_cls in _BUILTINS]
        tools += [WebSearch(config.web), FetchUrl(config.web)]
        for entry in entry_points(group="quaso.tools"):
            try:
                tools.append(entry.load()())
            except Exception:
                # A broken third-party plugin must not sink the session.
                continue
        return cls(tools)

    def add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def remove(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)
