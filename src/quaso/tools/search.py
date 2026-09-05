"""Glob and grep, with ripgrep used when it is installed."""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

from quaso.tools.base import (
    Tool,
    ToolContext,
    ToolError,
    resolve_path,
)

_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".quaso"}
_MAX_MATCHES = 500


def _validate_search_pattern(pattern: str) -> None:
    """Require search scope to be expressed through the checked path."""
    candidate = Path(pattern)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ToolError(
            "Search pattern cannot be absolute or contain '..'; put the "
            "directory in path so the permission policy can check it."
        )


def _within_root(path: Path, root: Path) -> bool:
    """Whether a match stays inside the root once symlinks are followed.

    Out-of-tree matches are skipped rather than fatal: one stray symlink
    in a project should not stop the whole search from returning.
    """
    try:
        return path.resolve().is_relative_to(root)
    except OSError:
        return False


def _skipped_note(count: int) -> str:
    if not count:
        return ""
    return f"\n({count} match(es) outside the search root were skipped)"


class GlobParams(BaseModel):
    pattern: str = Field(description="Glob pattern, e.g. '**/*.py'")
    path: str = Field(default=".", description="Directory to search from")


class Glob(Tool):
    name = "glob"
    concurrent = True
    max_output_chars = 4_000
    description = "Find files matching a glob, newest first."
    Params = GlobParams
    mutates = False

    def paths(self, params: GlobParams) -> list[str]:
        return [params.path]

    async def run(self, params: GlobParams, ctx: ToolContext) -> str:
        root = resolve_path(params.path, ctx)
        if not root.is_dir():
            raise ToolError(f"Not a directory: {root}")
        _validate_search_pattern(params.pattern)
        matches: list[Path] = []
        skipped = 0
        for path in root.glob(params.pattern):
            if not path.is_file() or _SKIP_DIRS.intersection(path.parts):
                continue
            if not _within_root(path, root):
                skipped += 1
                continue
            matches.append(path)
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        if not matches:
            return "(no matches)" + _skipped_note(skipped)
        out = "\n".join(str(p) for p in matches[:_MAX_MATCHES])
        if len(matches) > _MAX_MATCHES:
            out += f"\n... ({len(matches) - _MAX_MATCHES} more matches)"
        return out + _skipped_note(skipped)


class GrepParams(BaseModel):
    pattern: str = Field(description="Regular expression to search for")
    path: str = Field(default=".", description="File or directory")
    glob: str = Field(
        default="", description="Only search files matching this glob"
    )


class Grep(Tool):
    name = "grep"
    concurrent = True
    max_output_chars = 6_000
    description = "Search file contents with a regex."
    Params = GrepParams
    mutates = False

    def paths(self, params: GrepParams) -> list[str]:
        return [params.path]

    async def run(self, params: GrepParams, ctx: ToolContext) -> str:
        root = resolve_path(params.path, ctx)
        if not root.exists():
            raise ToolError(f"Path does not exist: {root}")
        if params.glob:
            _validate_search_pattern(params.glob)
        if shutil.which("rg"):
            return await self._ripgrep(params, root, ctx)
        return self._python_grep(params, root, ctx)

    async def _ripgrep(
        self, params: GrepParams, root: Path, ctx: ToolContext
    ) -> str:
        # Confinement here relies on rg not following symlinks, which is its
        # default. Adding --follow would reopen the escape that
        # _within_root closes on the Python path.
        cmd = [
            "rg",
            "--line-number",
            "--no-heading",
            "--max-count",
            "50",
            "--color",
            "never",
        ]
        if params.glob:
            cmd += ["--glob", params.glob]
        cmd += ["--", params.pattern, str(root)]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 1:
            return "(no matches)"
        if proc.returncode not in (0, 1):
            detail = stderr.decode(errors="replace")[:500]
            raise ToolError(f"rg failed: {detail}")
        return stdout.decode(errors="replace")

    def _python_grep(
        self, params: GrepParams, root: Path, ctx: ToolContext
    ) -> str:
        try:
            regex = re.compile(params.pattern)
        except re.error as exc:
            raise ToolError(f"Invalid regex: {exc}") from exc

        if root.is_file():
            files = [root]
        else:
            files = []
            for path in root.rglob(params.glob or "*"):
                if not path.is_file() or _SKIP_DIRS.intersection(path.parts):
                    continue
                if not _within_root(path, root):
                    continue
                files.append(path)

        lines: list[str] = []
        for file in files:
            try:
                content = file.read_text(errors="replace")
            except OSError:
                continue
            for i, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    lines.append(f"{file}:{i}: {line.strip()[:300]}")
                    if len(lines) >= _MAX_MATCHES:
                        return "\n".join(lines)
        if not lines:
            return "(no matches)"
        return "\n".join(lines)
