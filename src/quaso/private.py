"""Creating the files quaso keeps about you, readable only by you.

Transcripts hold the conversation, spilled tool output holds whatever a
tool was asked to read, and the config holds the address of your server
and any key you put in it. On a shared machine none of that is anyone
else's business, and the default umask usually says otherwise.

Files the model writes into the project are deliberately not covered
here: those belong to the project and follow the usual umask.
"""

from __future__ import annotations

import os
import stat
import sys
from contextlib import suppress
from pathlib import Path

DIR_MODE = 0o700
FILE_MODE = 0o600

# chmod on Windows only toggles the read-only bit, so there is nothing
# useful to say there and nothing to gain from trying.
_POSIX = sys.platform != "win32"


def private_dir(path: Path) -> Path:
    """Create a directory only its owner may enter."""
    path.mkdir(parents=True, exist_ok=True)
    if _POSIX:
        with suppress(OSError):
            os.chmod(path, DIR_MODE)
    return path


def make_private(path: Path) -> Path:
    """Restrict a file to its owner, quietly if the platform disagrees."""
    if _POSIX:
        with suppress(OSError):
            os.chmod(path, FILE_MODE)
    return path


# What is tightened on startup. Skills are deliberately absent: they are
# meant to be shared and committed, unlike anything listed here.
_SENSITIVE = (
    ("sessions", True),
    ("tool-output", True),
    ("config.toml", False),
    ("history", False),
)


def secure_existing(root: Path) -> int:
    """Tighten anything written before this ran. Returns what changed.

    A transcript written under the old behaviour stays world-readable
    for as long as it is kept, and the sessions worth reading are the
    old ones.
    """
    if not _POSIX:
        return 0
    changed = 0
    base = root / ".quaso"
    for name, is_dir in _SENSITIVE:
        path = base / name
        if not path.exists():
            continue
        if is_dir:
            if _tighten(path, DIR_MODE):
                changed += 1
            for child in path.iterdir():
                if child.is_file() and _tighten(child, FILE_MODE):
                    changed += 1
        elif _tighten(path, FILE_MODE):
            changed += 1
    return changed


def _tighten(path: Path, mode: int) -> bool:
    with suppress(OSError):
        if stat.S_IMODE(path.stat().st_mode) != mode:
            os.chmod(path, mode)
            return True
    return False
