"""Events yielded by providers and the agent loop.

UIs, transcript logging and any future frontend are consumers of this
stream, so adding one never touches the core.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quaso.messages import Message, ToolCall


@dataclass
class TextDelta:
    text: str


@dataclass
class ThinkingDelta:
    text: str


@dataclass
class ToolCallEvent:
    call: ToolCall


@dataclass
class ToolResultEvent:
    call: ToolCall
    output: str
    is_error: bool = False


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class TurnEnd:
    """One provider round-trip, with the assembled assistant message."""

    message: Message
    usage: Usage = field(default_factory=Usage)


@dataclass
class CompactionEvent:
    before_tokens: int
    after_tokens: int


@dataclass
class NoticeEvent:
    """Out-of-band information, such as hook output."""

    text: str


@dataclass
class ErrorEvent:
    error: str


Event = (
    TextDelta
    | ThinkingDelta
    | ToolCallEvent
    | ToolResultEvent
    | TurnEnd
    | CompactionEvent
    | NoticeEvent
    | ErrorEvent
)
