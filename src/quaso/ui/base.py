"""Frontend interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from quaso.events import Event
from quaso.permissions import Answer, PermissionRequest
from quaso.tools.ask import Question


class UI(ABC):
    # A frontend opts in by overriding both. Left off, the ask tool is
    # never wired up and tells the model to assume instead of waiting on
    # an answer that is not coming.
    can_ask = False

    @abstractmethod
    async def get_input(self) -> str:
        """Read the next prompt. Raise EOFError to end the session."""

    @abstractmethod
    def render(self, event: Event) -> None: ...

    @abstractmethod
    async def ask_permission(self, request: PermissionRequest) -> Answer: ...

    def info(self, text: str) -> None:
        """Show a message that is not part of the event stream."""

    async def ask_question(self, question: Question) -> str:
        """Put a question to the user. Empty means nobody answered."""
        return ""
