"""Letting the model put a question to the user.

Guessing at an ambiguity is worse than stopping, because the mistake only
surfaces once the work is done. This is the way out, and it has to fail
cleanly where there is nobody to answer.
"""

from __future__ import annotations

import pytest

from quaso.tools.ask import ASKER_KEY, Ask, AskParams, Question
from quaso.tools.base import ToolContext, ToolError
from quaso.ui.headless import HeadlessUI
from quaso.ui.repl import ReplUI


def _asker(reply):
    asked = []

    async def ask(question: Question) -> str:
        asked.append(question)
        return reply

    ask.asked = asked
    return ask


def _ctx(tmp_path, asker=None):
    ctx = ToolContext(cwd=tmp_path)
    if asker is not None:
        ctx.extra[ASKER_KEY] = asker
    return ctx


class TestAsking:
    @pytest.mark.asyncio
    async def test_the_answer_comes_back_as_the_result(self, tmp_path):
        ask = _asker("Postgres")
        out = await Ask().run(
            AskParams(
                question="Which database?",
                options=["Postgres", "SQLite"],
            ),
            _ctx(tmp_path, ask),
        )
        assert "Postgres" in out

    @pytest.mark.asyncio
    async def test_the_question_and_options_reach_the_user(self, tmp_path):
        ask = _asker("SQLite")
        await Ask().run(
            AskParams(
                question="Which database?",
                options=["Postgres", "SQLite"],
            ),
            _ctx(tmp_path, ask),
        )
        assert ask.asked[0].question == "Which database?"
        assert ask.asked[0].options == ["Postgres", "SQLite"]

    @pytest.mark.asyncio
    async def test_a_question_needs_no_options(self, tmp_path):
        """Open questions are legitimate; not everything is a menu."""
        ask = _asker("call it quaso")
        out = await Ask().run(
            AskParams(question="What should I name it?"), _ctx(tmp_path, ask)
        )
        assert "quaso" in out

    @pytest.mark.asyncio
    async def test_an_empty_answer_is_reported_not_swallowed(self, tmp_path):
        ask = _asker("")
        out = await Ask().run(
            AskParams(question="Which?", options=["a", "b"]),
            _ctx(tmp_path, ask),
        )
        assert "no answer" in out.lower()


class TestNobodyToAsk:
    @pytest.mark.asyncio
    async def test_without_an_asker_it_tells_the_model_to_assume(
        self, tmp_path
    ):
        """Headless must not hang, and must not silently return nothing."""
        out = await Ask().run(
            AskParams(question="Which database?", options=["a", "b"]),
            _ctx(tmp_path),
        )
        assert "no one" in out.lower()
        assert "assum" in out.lower()

    @pytest.mark.asyncio
    async def test_headless_ui_declines_rather_than_blocking(self, tmp_path):
        answer = await HeadlessUI().ask_question(
            Question(question="Which database?", options=["a", "b"])
        )
        assert answer == ""

    def test_headless_does_not_advertise_itself_as_an_asker(self):
        """So the tool gives the clearer 'nobody to ask' answer."""
        assert HeadlessUI().can_ask is False
        assert ReplUI().can_ask is True


class TestGuards:
    @pytest.mark.asyncio
    async def test_an_empty_question_is_refused(self, tmp_path):
        with pytest.raises(ToolError, match="question"):
            await Ask().run(
                AskParams(question="   "), _ctx(tmp_path, _asker("x"))
            )

    def test_it_does_not_mutate_anything(self):
        assert Ask().mutates is False

    def test_the_question_is_the_primary_argument(self):
        params = AskParams(question="Which database?", options=["a"])
        assert Ask().primary_argument(params) == "Which database?"

    def test_it_touches_no_paths(self):
        assert Ask().paths(AskParams(question="q")) == []
