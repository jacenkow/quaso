"""Filesystem tools."""

from __future__ import annotations

import difflib

from pydantic import BaseModel, Field

from quaso.tools.base import (
    Tool,
    ToolContext,
    ToolError,
    resolve_path,
)

_MAX_LINE_CHARS = 2000


class ReadFileParams(BaseModel):
    path: str = Field(description="Path, absolute or relative to cwd")
    offset: int = Field(
        default=0, ge=0, description="Line to start from (0-based)"
    )
    limit: int = Field(
        default=2000, gt=0, description="Maximum lines to return"
    )


class ReadFile(Tool):
    name = "read_file"
    concurrent = True
    max_output_chars = 12_000
    description = (
        "Read a text file. Returns numbered lines. Use offset and limit "
        "for large files."
    )
    Params = ReadFileParams
    mutates = False

    def paths(self, params: ReadFileParams) -> list[str]:
        return [params.path]

    async def run(self, params: ReadFileParams, ctx: ToolContext) -> str:
        path = resolve_path(params.path, ctx)
        if not path.is_file():
            raise ToolError(f"Not a file: {path}")
        try:
            text = path.read_text(errors="replace")
        except OSError as exc:
            raise ToolError(f"Cannot read {path}: {exc}") from exc
        ctx.files.record(path)

        lines = text.splitlines()
        window = lines[params.offset : params.offset + params.limit]
        if not window:
            return (
                f"(file has {len(lines)} lines; "
                f"offset {params.offset} is past the end)"
            )
        numbered = "\n".join(
            f"{i:6d}\t{line[:_MAX_LINE_CHARS]}"
            for i, line in enumerate(window, start=params.offset + 1)
        )
        remaining = len(lines) - params.offset - params.limit
        if remaining > 0:
            numbered += f"\n... ({remaining} more lines)"
        return numbered


class ListDirParams(BaseModel):
    path: str = Field(default=".", description="Directory to list")


class ListDir(Tool):
    name = "list_dir"
    concurrent = True
    max_output_chars = 4_000
    description = "List a directory. Directories end with '/'."
    Params = ListDirParams
    mutates = False

    def paths(self, params: ListDirParams) -> list[str]:
        return [params.path]

    async def run(self, params: ListDirParams, ctx: ToolContext) -> str:
        path = resolve_path(params.path, ctx)
        if not path.is_dir():
            raise ToolError(f"Not a directory: {path}")
        entries = sorted(
            path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
        )
        if not entries:
            return "(empty directory)"
        rows = [
            f"{e.name}/" if e.is_dir() else f"{e.name} ({e.stat().st_size} B)"
            for e in entries
        ]
        return "\n".join(rows)


class WriteFileParams(BaseModel):
    path: str = Field(description="Path to create or overwrite")
    content: str = Field(description="Full file content")


class WriteFile(Tool):
    name = "write_file"
    description = "Create or overwrite a file with the given content."
    Params = WriteFileParams
    mutates = True

    def paths(self, params: WriteFileParams) -> list[str]:
        return [params.path]

    def describe(self, params: WriteFileParams) -> str:
        preview = "\n".join(params.content.splitlines()[:15])
        return f"write_file({params.path})\n---\n{preview}\n---"

    async def run(self, params: WriteFileParams, ctx: ToolContext) -> str:
        path = resolve_path(params.path, ctx)
        if ctx.require_read_before_edit and (problem := ctx.files.check(path)):
            raise ToolError(problem)
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        path.write_text(params.content)
        ctx.files.record(path)
        verb = "Overwrote" if existed else "Created"
        return f"{verb} {path} ({len(params.content)} chars)"


class EditFileParams(BaseModel):
    path: str = Field(description="Path of the file to edit")
    old_string: str = Field(description="Exact text to replace")
    new_string: str = Field(description="Replacement text")
    replace_all: bool = Field(
        default=False, description="Replace every occurrence"
    )


class EditFile(Tool):
    name = "edit_file"
    description = (
        "Replace an exact string in a file. old_string must appear exactly "
        "once unless replace_all is true. Include surrounding context to "
        "make it unique."
    )
    Params = EditFileParams
    mutates = True

    def paths(self, params: EditFileParams) -> list[str]:
        return [params.path]

    def describe(self, params: EditFileParams) -> str:
        diff = difflib.unified_diff(
            params.old_string.splitlines(),
            params.new_string.splitlines(),
            lineterm="",
            n=2,
        )
        body = "\n".join(list(diff)[2:][:40])
        return f"edit_file({params.path})\n{body}"

    async def run(self, params: EditFileParams, ctx: ToolContext) -> str:
        path = resolve_path(params.path, ctx)
        if not path.is_file():
            raise ToolError(f"Not a file: {path}")
        if ctx.require_read_before_edit and (problem := ctx.files.check(path)):
            raise ToolError(problem)
        if params.old_string == params.new_string:
            raise ToolError("old_string and new_string are identical")

        text = path.read_text()
        count = text.count(params.old_string)
        if count == 0:
            raise ToolError(
                "old_string not found in file (must match exactly, "
                "including whitespace)"
            )
        if count > 1 and not params.replace_all:
            raise ToolError(
                f"old_string appears {count} times; add surrounding "
                "context to make it unique, or set replace_all=true"
            )
        limit = -1 if params.replace_all else 1
        path.write_text(
            text.replace(params.old_string, params.new_string, limit)
        )
        ctx.files.record(path)
        replaced = count if params.replace_all else 1
        return f"Edited {path}: {replaced} replacement(s)"
