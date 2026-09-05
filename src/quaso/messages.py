"""Provider-agnostic chat messages."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    role: Role
    content: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None


def system(content: str) -> Message:
    return Message(role="system", content=content)


def user(content: str) -> Message:
    return Message(role="user", content=content)


def tool_result(call: ToolCall, content: str) -> Message:
    return Message(
        role="tool",
        content=content,
        tool_call_id=call.id,
        tool_name=call.name,
    )
