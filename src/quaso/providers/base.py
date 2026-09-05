"""Provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from quaso.events import Event
from quaso.messages import Message


@dataclass(frozen=True)
class ProviderCapabilities:
    tools: bool = False
    thinking: bool = False


class Provider(ABC):
    name = "base"
    capabilities = ProviderCapabilities()

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[Event]:
        """Stream one round-trip, ending with TurnEnd or ErrorEvent."""

    async def close(self) -> None:
        """Release client resources."""

    async def unload(self) -> None:
        """Release backend resources held for this model."""
