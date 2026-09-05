"""Non-interactive frontend for `quaso -p`."""

from __future__ import annotations

import sys

from quaso.events import (
    CompactionEvent,
    ErrorEvent,
    Event,
    NoticeEvent,
    TextDelta,
    ToolCallEvent,
    ToolResultEvent,
)
from quaso.permissions import Answer, PermissionRequest
from quaso.tools.ask import Question
from quaso.ui.base import UI


class HeadlessUI(UI):
    async def get_input(self) -> str:
        raise EOFError

    def render(self, event: Event) -> None:
        match event:
            case TextDelta(text):
                sys.stdout.write(text)
                sys.stdout.flush()
            case ToolCallEvent(call):
                print(f"[tool] {call.name} {call.arguments}", file=sys.stderr)
            case ToolResultEvent(_, output, is_error) if is_error:
                print(f"[tool error] {output}", file=sys.stderr)
            case CompactionEvent(before, after):
                print(
                    f"[compacted ~{before} -> ~{after} tokens]",
                    file=sys.stderr,
                )
            case NoticeEvent(text):
                print(f"[notice] {text}", file=sys.stderr)
            case ErrorEvent(error):
                print(f"[error] {error}", file=sys.stderr)

    async def ask_permission(self, request: PermissionRequest) -> Answer:
        print(
            f"[denied: non-interactive] {request.tool_name}", file=sys.stderr
        )
        return "deny"

    async def ask_question(self, question: Question) -> str:
        print(f"[unanswered] {question.question}", file=sys.stderr)
        return ""

    def info(self, text: str) -> None:
        print(text, file=sys.stderr)
