"""Recover tool calls a model wrote as text instead of structured data.

Quantised and tool-untrained models often emit <tool_call>{...}</tool_call>
or a fenced JSON block in the content. A parsed call is only accepted when
its name matches a tool that was actually offered, so a model merely
discussing a call cannot trigger one.
"""

from __future__ import annotations

import json
import re
import uuid

from quaso.messages import ToolCall

_TAGGED = re.compile(
    r"<(tool_call|function_call|tool)>\s*(\{.*?\})\s*</\1>", re.DOTALL
)
_FENCED = re.compile(r"```(?:json|tool_code)?\s*(\{.*?\})\s*```", re.DOTALL)

_NAME_KEYS = ("name", "tool", "tool_name", "function")
_ARG_KEYS = ("arguments", "parameters", "args", "input")


def _as_call(payload: dict, known: set[str]) -> ToolCall | None:
    name = None
    for key in _NAME_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            name = value
            break
        if isinstance(value, dict) and isinstance(value.get('name'), str):
            payload = {**payload, **value}
            name = value['name']
            break
    if not name or name not in known:
        return None

    arguments: dict = {}
    for key in _ARG_KEYS:
        value = payload.get(key)
        if isinstance(value, dict):
            arguments = value
            break
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                arguments = parsed
                break
    return ToolCall(
        id=f"call_{uuid.uuid4().hex[:8]}", name=name, arguments=arguments
    )


def extract_tool_calls(
    text: str, known_tools: set[str]
) -> tuple[str, list[ToolCall]]:
    """Return the text with call blocks removed, plus recovered calls."""
    if not text or not known_tools:
        return text, []

    calls: list[ToolCall] = []
    spans: list[tuple[int, int]] = []
    for pattern, group in ((_TAGGED, 2), (_FENCED, 1)):
        for match in pattern.finditer(text):
            try:
                payload = json.loads(match.group(group))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            call = _as_call(payload, known_tools)
            if call is not None:
                calls.append(call)
                spans.append(match.span())

    if not calls:
        return text, []

    cleaned = text
    for start, end in sorted(spans, reverse=True):
        cleaned = cleaned[:start] + cleaned[end:]
    return cleaned.strip(), calls
