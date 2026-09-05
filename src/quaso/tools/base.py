"""Tool interface.

Parameters are pydantic models and the schema shown to the model is
generated from them. `schema`, `describe` and `primary_argument` are
instance methods so tools whose shape is only known at runtime, such as
MCP tools, can implement them per instance.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel


@dataclass
class FileState:
    mtime: float
    size: int


class FileTracker:
    """Tracks what the model has read, so edits cannot clobber changes."""

    def __init__(self) -> None:
        self._seen: dict[Path, FileState] = {}

    def record(self, path: Path) -> None:
        try:
            stat = path.stat()
        except OSError:
            return
        self._seen[path] = FileState(stat.st_mtime, stat.st_size)

    def check(self, path: Path) -> str | None:
        """Return an error message if the path may not be edited."""
        if not path.exists():
            return None
        seen = self._seen.get(path)
        if seen is None:
            return f"You must read {path} before editing it."
        try:
            stat = path.stat()
        except OSError:
            return None
        if stat.st_mtime != seen.mtime or stat.st_size != seen.size:
            return (
                f"{path} changed on disk since you read it; "
                "read it again before editing."
            )
        return None

    def forget(self, path: Path) -> None:
        self._seen.pop(path, None)


@dataclass
class ToolContext:
    cwd: Path
    max_output_chars: int = 8_000
    # Per-tool overrides by name, from agent.tool_output_chars.
    tool_output_chars: dict[str, int] = field(default_factory=dict)
    files: FileTracker = field(default_factory=FileTracker)
    require_read_before_edit: bool = True
    # How bash runs a command. None means unconfined, which is
    # what a bare ToolContext in a test gets.
    executor: Any = None
    subagent: Callable[[str], Awaitable[str]] | None = None
    compact: Callable[[], Awaitable[None]] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class ToolError(Exception):
    """Expected failure; the message goes back to the model."""


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    Params: type[BaseModel]
    mutates: ClassVar[bool] = False
    # Whether this tool may run alongside its neighbours when the model
    # asks for several at once. Opt in, because mutates is not the same
    # question: task, compact, ask and todo_write all mutate nothing on
    # disk yet own something a second copy of them would fight over, the
    # session, the terminal, or the todo list.
    concurrent: ClassVar[bool] = False
    # Whether running this sends anything off the machine. Separate from
    # mutates, which asks whether it changes something here: a search
    # changes nothing and is still the way a private thing gets out.
    network: ClassVar[bool] = False
    # A tool's own output budget. None means take the global default.
    # A whole-file read earns more room than a directory listing.
    max_output_chars: ClassVar[int | None] = None

    def output_limit(self, ctx: ToolContext) -> int:
        """Resolve this tool's budget: config, then class, then global."""
        override = ctx.tool_output_chars.get(self.name)
        if override is not None:
            return override
        return self.max_output_chars or ctx.max_output_chars

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.Params.model_json_schema(),
            },
        }

    def paths(self, params: BaseModel) -> list[str]:
        """Filesystem paths this call will touch.

        Used to spot calls reaching outside the working directory, which
        need approval even when the tool only reads. Tools that touch no
        files return nothing.
        """
        return []

    def primary_argument(self, params: BaseModel) -> str:
        for value in params.model_dump().values():
            if isinstance(value, str) and value:
                return value
        return ""

    def describe(self, params: BaseModel) -> str:
        """What to show the user when asking permission."""
        return f"{self.name}({self.primary_argument(params)})"

    @abstractmethod
    async def run(self, params: BaseModel, ctx: ToolContext) -> str: ...


_ELISION = "\n... [truncated {omitted} chars{note}] ...\n"
# Below this there is no room for two useful halves, so keep the head only.
_MIN_BODY = 40
# Command output leads with what ran and ends with the verdict. The head is
# worth more of the budget, but dropping the tail loses the exit code.
_HEAD_SHARE = 0.7


def _cut_after_line(chunk: str) -> str:
    """Trim back to the last line break, unless one line dominates."""
    end = chunk.rfind("\n")
    if end < len(chunk) // 2:
        return chunk
    return chunk[:end]


def _cut_before_line(chunk: str) -> str:
    """Trim forward to the next line break, unless one line dominates."""
    start = chunk.find("\n")
    if start < 0 or start > len(chunk) // 2:
        return chunk
    return chunk[start + 1 :]


def truncate(text: str, limit: int, note: str = "") -> str:
    """Bound text to `limit` characters, keeping both ends.

    A head-only cut loses whatever a command says last, which for test
    runners and build tools is the summary and the exit code. The middle
    goes instead. The returned string never exceeds `limit`.

    `note` is added to the elision, so a caller that kept the full text
    somewhere can say where, right where the gap appears.
    """
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text

    # An elision reporting the whole input is the longest one possible, so
    # reserving that much keeps the result inside the limit whatever the
    # split turns out to be.
    reserved = len(_ELISION.format(omitted=len(text), note=note))
    body = limit - reserved
    if body < _MIN_BODY:
        # No room for the note; a bounded result beats a pointer to one.
        return truncate(text, limit) if note else text[:limit]

    head_len = int(body * _HEAD_SHARE)
    head = _cut_after_line(text[:head_len])
    tail = _cut_before_line(text[len(text) - (body - head_len) :])
    omitted = len(text) - len(head) - len(tail)
    return head + _ELISION.format(omitted=omitted, note=note) + tail


def contained_by(path: Path, root: Path) -> bool:
    """Whether `path` is really inside `root`, symlinks followed.

    Startup reads project instructions and skills off disk before the
    model has said anything, so no permission check stands between a
    symlink and whatever it points at.
    """
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def resolve_path(raw: str, ctx: ToolContext) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = ctx.cwd / path
    return path.resolve()
