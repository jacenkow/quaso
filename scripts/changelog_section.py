#!/usr/bin/env python3
"""Print one version's section of the changelog.

    scripts/changelog_section.py 0.5.0

Used by the release workflow, so the notes on a release are the notes in
the repository rather than a second copy written by hand and diverging
from the first.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"


def section(text: str, version: str) -> str:
    """Everything under `## [version]` up to the next heading."""
    pattern = re.compile(
        rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    found = pattern.search(text)
    if found is None:
        raise SystemExit(f"No section for {version} in {CHANGELOG.name}")
    return found.group(1).strip()


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: changelog_section.py VERSION")
    print(section(CHANGELOG.read_text(), sys.argv[1].lstrip("v")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
