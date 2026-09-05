"""The places a version number is written down agree.

pyproject reads it from __init__, but the flake states it again because
a Nix derivation cannot import the package it is building. Two copies
drift, and the one that drifts is the one nobody runs locally.
"""

from __future__ import annotations

import pathlib
import re

from quaso import __version__

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_the_flake_states_the_same_version():
    flake = (ROOT / "flake.nix").read_text()
    stated = re.search(r'version = "([^"]+)";', flake)
    assert stated, "no version in flake.nix"
    assert stated.group(1) == __version__


def test_the_changelog_mentions_this_version():
    """A release whose notes were never written is one nobody can read
    the difference from."""
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert f"## [{__version__}]" in changelog


def test_the_installer_is_executable():
    script = ROOT / "install.sh"
    assert script.stat().st_mode & 0o111, "install.sh is not executable"
    assert script.read_text().startswith("#!/bin/sh")
