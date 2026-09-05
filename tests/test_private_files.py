"""What quaso writes about you stays readable only by you.

Transcripts hold the conversation, spilled tool output holds whatever a
tool read, prompt history holds what you typed, and the config holds the
address of your server and any key you put in it. None of that wants to
be world-readable on a shared machine.

Files the model writes into the project are not covered: those belong to
the project and follow the usual umask.
"""

from __future__ import annotations

import os
import stat
import sys

import pytest

from quaso.messages import user
from quaso.output_store import ToolOutputStore
from quaso.session import Session, sessions_dir
from quaso.setup import write_config

private = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permission bits"
)


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@private
class TestTranscripts:
    def test_the_file_is_private(self, tmp_path):
        session = Session("system", root=tmp_path, persist=True)
        session.append(user("something I typed"))
        assert _mode(session.transcript) == 0o600

    def test_the_directory_is_private(self, tmp_path):
        session = Session("system", root=tmp_path, persist=True)
        session.append(user("something I typed"))
        assert _mode(sessions_dir(tmp_path)) == 0o700

    def test_a_rewrite_keeps_it_private(self, tmp_path):
        """replace_history rewrites the whole file, which is the easy
        place to lose the mode."""
        session = Session("system", root=tmp_path, persist=True)
        session.append(user("first"))
        session.replace_history([user("second")])
        assert _mode(session.transcript) == 0o600


@private
class TestSpilledOutput:
    def test_the_file_is_private(self, tmp_path):
        """It can hold any file a tool was asked to read."""
        store = ToolOutputStore(tmp_path)
        store.bound("x" * 5_000, 2_000)
        spilled = next(store.directory.glob("*.txt"))
        assert _mode(spilled) == 0o600

    def test_the_directory_is_private(self, tmp_path):
        store = ToolOutputStore(tmp_path)
        store.bound("x" * 5_000, 2_000)
        assert _mode(store.directory) == 0o700


@private
class TestWrittenConfig:
    def test_the_file_is_private(self, tmp_path):
        """It carries the server address, and may carry a key."""
        path = write_config("http://box:11434", "qwen3.6", True, tmp_path)
        assert _mode(path) == 0o600


@private
class TestPromptHistory:
    def test_the_history_file_is_private(self, tmp_path, monkeypatch):
        """It holds every prompt typed at the terminal."""
        monkeypatch.chdir(tmp_path)
        from quaso.ui.repl import _HISTORY_PATH, ReplUI

        ReplUI()
        assert _mode(tmp_path / _HISTORY_PATH) == 0o600


@private
class TestExistingFiles:
    """A transcript written before this existed stays readable for as
    long as it is kept, and the old sessions are the interesting ones."""

    def _loose(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
        os.chmod(path, 0o644)
        return path

    def test_old_transcripts_are_tightened(self, tmp_path):
        from quaso.private import secure_existing

        old = self._loose(tmp_path / ".quaso" / "sessions" / "old.jsonl")
        os.chmod(old.parent, 0o755)
        secure_existing(tmp_path)
        assert _mode(old) == 0o600
        assert _mode(old.parent) == 0o700

    def test_config_and_history_are_tightened(self, tmp_path):
        from quaso.private import secure_existing

        config = self._loose(tmp_path / ".quaso" / "config.toml")
        history = self._loose(tmp_path / ".quaso" / "history")
        secure_existing(tmp_path)
        assert _mode(config) == 0o600
        assert _mode(history) == 0o600

    def test_skills_are_left_alone(self, tmp_path):
        """They are meant to be shared and committed."""
        from quaso.private import secure_existing

        skill = self._loose(tmp_path / ".quaso" / "skills" / "a.md")
        secure_existing(tmp_path)
        assert _mode(skill) == 0o644

    def test_nothing_to_do_is_not_an_error(self, tmp_path):
        from quaso.private import secure_existing

        assert secure_existing(tmp_path) == 0
