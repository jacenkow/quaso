"""Pulling one version's notes out of the changelog.

The release workflow runs this on a tag push, which is a bad moment to
discover a regex is wrong: the tag is already public by then.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from changelog_section import section  # noqa: E402

SAMPLE = """# Changelog

Preamble that belongs to nobody.

## [Unreleased]

## [0.5.0]

Two ways in.

### Added

- A flake.

## [0.4.0]

### Fixed

- Something older.
"""


class TestSection:
    def test_it_takes_only_that_version(self):
        got = section(SAMPLE, "0.5.0")
        assert "A flake." in got
        assert "Something older." not in got
        assert "Preamble" not in got

    def test_the_heading_itself_is_not_included(self):
        assert not section(SAMPLE, "0.5.0").startswith("## ")

    def test_an_empty_section_is_empty(self):
        assert section(SAMPLE, "Unreleased") == ""

    def test_the_last_section_runs_to_the_end(self):
        assert "Something older." in section(SAMPLE, "0.4.0")

    def test_an_unknown_version_is_refused(self):
        with pytest.raises(SystemExit):
            section(SAMPLE, "9.9.9")

    def test_the_real_changelog_has_this_release(self):
        from quaso import __version__

        root = Path(__file__).resolve().parent.parent
        text = (root / "CHANGELOG.md").read_text()
        assert section(text, __version__).strip()


class TestAsACommand:
    def _run(self, *args):
        root = Path(__file__).resolve().parent.parent
        return subprocess.run(
            [
                sys.executable,
                str(root / "scripts" / "changelog_section.py"),
                *args,
            ],
            capture_output=True,
            text=True,
        )

    def test_a_leading_v_is_accepted(self):
        """The workflow passes GITHUB_REF_NAME, which carries one."""
        from quaso import __version__

        done = self._run(f"v{__version__}")
        assert done.returncode == 0, done.stderr
        assert done.stdout.strip()

    def test_no_argument_explains_itself(self):
        done = self._run()
        assert done.returncode != 0
        assert "usage" in done.stderr.lower()
