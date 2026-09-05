"""Keeping tool output that does not fit in the context window.

Bounding a result to a budget throws away the middle, which is often
where the one interesting line was. The full text is written to a file
under the project instead, and the elision says where, so the model can
read or grep the part it actually needs at the cost of one round trip.

Files live inside the project rather than a shared directory so that the
tools can reach them without an exemption from the workspace boundary,
and so a project-wide search does not trip over old output.
"""

from __future__ import annotations

import datetime as dt
import time
import uuid
from pathlib import Path

from quaso.private import make_private, private_dir
from quaso.tools.base import truncate

STORE_DIR = Path(".quaso") / "tool-output"
RETENTION_DAYS = 7

_PREFIX = "tool_"


class ToolOutputStore:
    def __init__(
        self, root: Path, retention_days: int = RETENTION_DAYS
    ) -> None:
        self.root = root
        self.directory = root / STORE_DIR
        self.retention_days = retention_days

    def bound(self, output: str, limit: int) -> str:
        """Bound output to the budget, keeping the whole of it on disk."""
        if len(output) <= limit:
            return output
        path = self._write(output)
        if path is None:
            # Losing the spare copy is not worth failing a tool call over.
            return truncate(output, limit)
        note = f"; full output in `{path}`, read or grep that file"
        bounded = truncate(output, limit, note=note)
        if str(path) not in bounded:
            # The budget was too small to carry the pointer, so the file
            # is unreachable. Leaving it behind would be litter.
            path.unlink(missing_ok=True)
        return bounded

    def sweep(self) -> int:
        """Delete output older than the retention window."""
        cutoff = time.time() - self.retention_days * 86_400
        removed = 0
        for path in self.directory.glob(f"{_PREFIX}*"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed

    def _write(self, output: str) -> Path | None:
        """Returns a project-relative path, or None if it could not be kept."""
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"{_PREFIX}{stamp}-{uuid.uuid4().hex[:6]}.txt"
        path = self.directory / name
        try:
            private_dir(self.directory)
            # Exclusive create: a name collision should surface, not clobber.
            with path.open("x") as handle:
                handle.write(output)
            make_private(path)
        except OSError:
            return None
        # Absolute, though it costs tokens. Handed a relative ".quaso/..."
        # the model reliably drops the leading dot and asks for "/quaso/...",
        # then pays for a full re-run instead. Measured, not assumed.
        return path.resolve()
