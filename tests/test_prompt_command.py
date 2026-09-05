"""Showing what was actually sent.

The reason for running your own harness is that nothing about it has to
be taken on trust. That only holds if the prompt is inspectable, and
until now the one thing you could not see was the thing the model
actually read.
"""

from __future__ import annotations

import pytest

from quaso.cli import _build_agent, _handle_command
from quaso.config import Config
from quaso.messages import user
from quaso.ui.commands import COMMANDS
from quaso.ui.headless import HeadlessUI

from .conftest import FakeProvider


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "quaso.cli.create_provider", lambda cfg: FakeProvider([])
    )
    return _build_agent(Config(), HeadlessUI(), persist=False)


def _said(ui):
    ui.lines = []
    ui.info = ui.lines.append
    return ui.lines


class TestPrompt:
    @pytest.mark.asyncio
    async def test_it_shows_the_system_prompt(self, agent):
        ui = HeadlessUI()
        said = _said(ui)
        await _handle_command("/prompt", agent, ui, Config(), [])
        assert "You are quaso" in "\n".join(said)

    @pytest.mark.asyncio
    async def test_it_shows_the_project_instructions(self, agent, tmp_path):
        """AGENTS.md is appended silently, so it is exactly the sort of
        thing worth being able to see."""
        (tmp_path / "AGENTS.md").write_text("never touch the database")
        agent = _build_agent(Config(), HeadlessUI(), persist=False)
        ui = HeadlessUI()
        said = _said(ui)
        await _handle_command("/prompt", agent, ui, Config(), [])
        assert "never touch the database" in "\n".join(said)

    @pytest.mark.asyncio
    async def test_it_reports_what_the_prompt_costs(self, agent):
        ui = HeadlessUI()
        said = _said(ui)
        await _handle_command("/prompt", agent, ui, Config(), [])
        assert "tokens" in "\n".join(said).lower()

    @pytest.mark.asyncio
    async def test_all_shows_the_conversation_too(self, agent):
        agent.session.append(user("something I typed earlier"))
        ui = HeadlessUI()
        said = _said(ui)
        await _handle_command("/prompt all", agent, ui, Config(), [])
        assert "something I typed earlier" in "\n".join(said)

    @pytest.mark.asyncio
    async def test_plain_prompt_leaves_the_conversation_out(self, agent):
        agent.session.append(user("something I typed earlier"))
        ui = HeadlessUI()
        said = _said(ui)
        await _handle_command("/prompt", agent, ui, Config(), [])
        assert "something I typed earlier" not in "\n".join(said)

    def test_it_is_listed_like_every_other_command(self):
        assert "/prompt" in COMMANDS
