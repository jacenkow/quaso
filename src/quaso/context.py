"""Context-window accounting and history compaction."""

from __future__ import annotations

from quaso.config import ContextConfig
from quaso.events import TurnEnd
from quaso.messages import Message, user
from quaso.providers.base import Provider

CHARS_PER_TOKEN = 4

SUMMARY_PREFIX = "[Summary of earlier conversation]\n"

_TOOL_EXCERPT_CHARS = 600
_SUMMARY_INPUT_FRACTION = 0.5

_COMPACT_INSTRUCTION = """\
Summarise the conversation so far for your own future reference. Be \
specific and preserve, in this order:
1. What the user asked for, including explicit constraints or preferences.
2. Files examined or modified, with paths and what changed.
3. Decisions made and why.
4. What is in progress and what remains to be done.
5. Anything that failed or is unresolved.

Write compact notes, not prose. Do not call any tools."""


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN) if text else 0


def estimate_messages(messages: list[Message]) -> int:
    total = 0
    for message in messages:
        total += estimate_tokens(message.content)
        for call in message.tool_calls:
            total += estimate_tokens(call.name + str(call.arguments))
        total += 4
    return total


def _turn_boundary(messages: list[Message], keep_recent: int) -> int:
    """First index to keep, snapped forward to a safe cut point.

    Tool results always directly follow the assistant message that asked
    for them, so any non-tool message can be cut at without orphaning one.
    """
    start = max(1, len(messages) - keep_recent)
    for i in range(start, len(messages)):
        if messages[i].role != "tool":
            return i
    return len(messages)


def flatten(
    messages: list[Message], tool_excerpt: int = _TOOL_EXCERPT_CHARS
) -> str:
    """Render history as plain text for the summariser.

    Replaying the real messages would resend every full tool result, which
    is the bulk compaction exists to reclaim.
    """
    lines: list[str] = []
    for message in messages:
        if message.role == "tool":
            body = message.content
            if len(body) > tool_excerpt:
                body = f"{body[:tool_excerpt]} …[{len(body)} chars total]"
            lines.append(f"[tool:{message.tool_name}] {body}")
        elif message.role == "assistant":
            if message.tool_calls:
                names = ", ".join(c.name for c in message.tool_calls)
                lines.append(f"[assistant called: {names}]")
            if message.content:
                lines.append(f"[assistant] {message.content}")
        elif message.role == "user":
            lines.append(f"[user] {message.content}")
    return "\n".join(lines)


def _fit(text: str, budget_chars: int) -> str:
    """Trim the middle, keeping the request and the recent work."""
    if len(text) <= budget_chars:
        return text
    head = budget_chars // 5
    tail = budget_chars - head
    omitted = "\n…[middle of transcript omitted]…\n"
    return text[:head] + omitted + text[-tail:]


async def _summarise(
    messages: list[Message], provider: Provider, max_tokens: int
) -> str:
    budget = int(max_tokens * _SUMMARY_INPUT_FRACTION) * CHARS_PER_TOKEN
    transcript = _fit(flatten(messages), budget)
    prompt = f"{_COMPACT_INSTRUCTION}\n\n--- transcript ---\n{transcript}"
    parts: list[str] = []
    async for event in provider.stream([user(prompt)], tools=None):
        if isinstance(event, TurnEnd):
            parts.append(event.message.content)
    return "".join(parts).strip()


class ContextManager:
    def __init__(self, config: ContextConfig, max_tokens: int) -> None:
        self.config = config
        self.max_tokens = max_tokens
        self.last_prompt_tokens = 0
        self._usage_after_compaction: int | None = None

    def reset(self) -> None:
        self.last_prompt_tokens = 0
        self._usage_after_compaction = None

    def usage(self, messages: list[Message]) -> int:
        return max(self.last_prompt_tokens, estimate_messages(messages))

    def fraction(self, messages: list[Message]) -> float:
        if self.max_tokens <= 0:
            return 0.0
        return self.usage(messages) / self.max_tokens

    def should_compact(self, messages: list[Message]) -> bool:
        if not self.config.auto_compact:
            return False
        usage = self.usage(messages)
        if self._usage_after_compaction is not None:
            # Compaction cannot always get under the threshold; without a
            # growth requirement it would re-summarise every iteration.
            growth = max(256, int(self.max_tokens * 0.05))
            if usage <= self._usage_after_compaction + growth:
                return False
        return usage >= self.config.compact_threshold * self.max_tokens

    async def compact(
        self, messages: list[Message], provider: Provider
    ) -> tuple[list[Message], int, int]:
        """Summarise old history, returning (messages, before, after)."""
        before = self.usage(messages)
        cut = _turn_boundary(messages, self.config.keep_recent_messages)
        if cut <= 1:
            return messages, before, before

        summary = await _summarise(messages[1:cut], provider, self.max_tokens)
        if not summary:
            return messages, before, before

        rebuilt = [
            messages[0],
            user(SUMMARY_PREFIX + summary),
            *messages[cut:],
        ]
        self.last_prompt_tokens = 0
        after = estimate_messages(rebuilt)
        self._usage_after_compaction = after
        return rebuilt, before, after
