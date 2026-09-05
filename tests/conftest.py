from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from quaso.events import Event, TextDelta, ThinkingDelta, TurnEnd
from quaso.messages import Message
from quaso.providers.base import Provider, ProviderCapabilities


class FakeProvider(Provider):
    """Plays back a scripted list of assistant messages, one per round-trip."""

    name = "fake"
    capabilities = ProviderCapabilities(tools=True, thinking=True)

    def __init__(self, turns: list[Message]) -> None:
        self.turns = list(turns)
        self.requests: list[list[Message]] = []

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[Event]:
        self.requests.append(list(messages))
        message = self.turns.pop(0)
        if message.thinking:
            yield ThinkingDelta(message.thinking)
        if message.content:
            yield TextDelta(message.content)
        yield TurnEnd(message=message)


@pytest.fixture
def make_provider():
    return FakeProvider
