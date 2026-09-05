"""Putting a question to the user mid-turn.

Every other tool lets the model act. This one lets it stop and check,
which is the cheaper move whenever an ambiguity would otherwise be
resolved by guessing and only surface once the work is finished.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from quaso.tools.base import Tool, ToolContext, ToolError

ASKER_KEY = "asker"


@dataclass
class Question:
    question: str
    options: list[str] = field(default_factory=list)


Asker = Callable[[Question], Awaitable[str]]


class AskParams(BaseModel):
    question: str = Field(description="The question, in one sentence")
    options: list[str] = Field(
        default_factory=list,
        description=(
            "Answers to choose from. Leave empty for an open question. Do "
            "not add an 'other' option: one is offered automatically."
        ),
    )


class Ask(Tool):
    name = "ask"
    description = (
        "Put a question to the user and wait for their answer, which comes "
        "back as the result so you can carry on in the same turn. Use it "
        "whenever you would otherwise end your turn with a question, or "
        "guess at a choice that changes what you build: which approach to "
        "take, which file to change, what something should be called. Do "
        "not use it for anything the project itself answers."
    )
    Params = AskParams
    mutates = False

    def primary_argument(self, params: AskParams) -> str:
        return params.question

    async def run(self, params: AskParams, ctx: ToolContext) -> str:
        question = params.question.strip()
        if not question:
            raise ToolError("A question is required")

        asker = ctx.extra.get(ASKER_KEY)
        if asker is None:
            # Headless, or any frontend that cannot prompt. Saying so is
            # better than hanging or returning a silent empty answer.
            return (
                "No one is available to answer. Make your best assumption, "
                "state which assumption you made, and continue."
            )

        answer = (await asker(Question(question, params.options))).strip()
        if not answer:
            return "The user gave no answer. Continue with a sensible default."
        return f"The user answered: {answer}"
