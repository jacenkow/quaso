"""Ollama /api/chat provider.

The native API rather than the /v1 compatibility layer, which hides
thinking tokens, num_ctx and keep_alive.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

import httpx

from quaso.events import (
    ErrorEvent,
    Event,
    TextDelta,
    ThinkingDelta,
    TurnEnd,
    Usage,
)
from quaso.messages import Message, ToolCall
from quaso.providers.base import Provider, ProviderCapabilities
from quaso.providers.toolparse import extract_tool_calls

# Options Ollama expects at the top level rather than inside "options".
_TOP_LEVEL_OPTIONS = {"think", "keep_alive"}


async def list_models(base_url: str, timeout: float = 10.0) -> list[dict]:
    """Models available on a server, newest first.

    Raises httpx.HTTPError if the server cannot be reached, which the
    caller is expected to turn into something a person can act on.
    """
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"), timeout=timeout
    ) as client:
        response = await client.get("/api/tags")
        response.raise_for_status()
    models = response.json().get('models', [])
    return sorted(models, key=lambda m: m.get('modified_at', ""), reverse=True)


def supports_tools(model: dict) -> bool:
    """Whether a model from /api/tags can call tools at all."""
    return "tools" in (model.get('capabilities') or [])


def to_wire(message: Message) -> dict[str, Any]:
    if message.role == "tool":
        return {
            "role": "tool",
            "content": message.content,
            "tool_name": message.tool_name or "",
        }
    wire: dict[str, Any] = {
        "role": message.role,
        "content": message.content,
    }
    if message.tool_calls:
        # Thinking is not replayed: it inflates the prompt and models do
        # not need their own previous reasoning back.
        wire["tool_calls"] = [
            {"function": {"name": c.name, "arguments": c.arguments}}
            for c in message.tool_calls
        ]
    return wire


class OllamaProvider(Provider):
    name = "ollama"
    capabilities = ProviderCapabilities(tools=True, thinking=True)

    def __init__(
        self,
        base_url: str,
        model: str,
        options: dict[str, Any] | None = None,
        timeout: float = 300.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.options = dict(options or {})
        self._client = httpx.AsyncClient(
            base_url=self.base_url, timeout=timeout
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def unload(self) -> None:
        with suppress(httpx.HTTPError):
            await self._client.post(
                "/api/chat",
                json={
                    "model": self.model,
                    "messages": [],
                    "keep_alive": 0,
                },
            )

    async def model_info(self) -> dict[str, Any]:
        """Return the model metadata from /api/show."""
        response = await self._client.post(
            "/api/show", json={"model": self.model}
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def context_length_from_info(info: dict[str, Any]) -> int | None:
        """Extract the model's maximum context from /api/show output.

        The key is namespaced by model family ("qwen35moe.context_length",
        "llama.context_length", ...), so match on the suffix.
        """
        model_info = info.get('model_info') or {}
        for key, value in model_info.items():
            if key == "context_length" or key.endswith(".context_length"):
                try:
                    return int(value)
                except (ValueError, TypeError):
                    return None
        return None

    def _body(
        self, messages: list[Message], tools: list[dict[str, Any]] | None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [to_wire(m) for m in messages],
            "stream": True,
        }
        model_options = {
            k: v
            for k, v in self.options.items()
            if k not in _TOP_LEVEL_OPTIONS
        }
        if model_options:
            body["options"] = model_options
        for key in _TOP_LEVEL_OPTIONS:
            if key in self.options:
                body[key] = self.options[key]
        if tools:
            body["tools"] = tools
        return body

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[Event]:
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        usage = Usage()

        try:
            async with self._client.stream(
                "POST", "/api/chat", json=self._body(messages, tools)
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    yield ErrorEvent(
                        f"Ollama HTTP {response.status_code}: "
                        f"{body.decode(errors='replace')[:500]}"
                    )
                    return
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if error := chunk.get('error'):
                        yield ErrorEvent(str(error))
                        return
                    message = chunk.get('message') or {}
                    if thinking := message.get('thinking'):
                        thinking_parts.append(thinking)
                        yield ThinkingDelta(thinking)
                    if content := message.get('content'):
                        content_parts.append(content)
                        yield TextDelta(content)
                    for raw in message.get('tool_calls') or []:
                        tool_calls.append(_parse_call(raw))
                    if chunk.get('done'):
                        usage = Usage(
                            prompt_tokens=chunk.get('prompt_eval_count', 0),
                            completion_tokens=chunk.get('eval_count', 0),
                        )
        except httpx.HTTPError as exc:
            yield ErrorEvent(f"Ollama connection error: {exc}")
            return

        text = "".join(content_parts)
        if not tool_calls and tools:
            known = {t["function"]["name"] for t in tools if "function" in t}
            text, tool_calls = extract_tool_calls(text, known)

        yield TurnEnd(
            message=Message(
                role="assistant",
                content=text,
                thinking="".join(thinking_parts),
                tool_calls=tool_calls,
            ),
            usage=usage,
        )


def _parse_call(raw: dict[str, Any]) -> ToolCall:
    function = raw.get('function') or {}
    arguments = function.get('arguments') or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {"_raw": arguments}
    return ToolCall(
        id=f"call_{uuid.uuid4().hex[:8]}",
        name=function.get('name', ""),
        arguments=arguments,
    )
