from __future__ import annotations

import json

import httpx
import pytest
import respx

from quaso.events import ErrorEvent, TextDelta, ThinkingDelta, TurnEnd
from quaso.messages import Message, ToolCall, tool_result, user
from quaso.providers.ollama import OllamaProvider, to_wire

BASE = "http://testserver:11434"


def _ndjson(*chunks: dict) -> str:
    return "\n".join(json.dumps(c) for c in chunks) + "\n"


async def _collect(provider, messages, tools=None):
    return [e async for e in provider.stream(messages, tools=tools)]


@pytest.mark.asyncio
@respx.mock
async def test_streaming_text_and_thinking():
    respx.post(f"{BASE}/api/chat").mock(
        return_value=httpx.Response(
            200,
            text=_ndjson(
                {
                    "message": {"role": "assistant", "thinking": "hmm "},
                    "done": False,
                },
                {
                    "message": {"role": "assistant", "content": "Hello"},
                    "done": False,
                },
                {
                    "message": {"role": "assistant", "content": " world"},
                    "done": False,
                },
                {
                    "message": {"role": "assistant", "content": ""},
                    "done": True,
                    "prompt_eval_count": 10,
                    "eval_count": 5,
                },
            ),
        )
    )
    provider = OllamaProvider(BASE, "m")
    events = await _collect(provider, [user("hi")])
    assert [e.text for e in events if isinstance(e, ThinkingDelta)] == ["hmm "]
    assert (
        "".join(e.text for e in events if isinstance(e, TextDelta))
        == "Hello world"
    )
    end = events[-1]
    assert isinstance(end, TurnEnd)
    assert end.message.content == "Hello world"
    assert end.message.thinking == "hmm "
    assert end.usage.prompt_tokens == 10 and end.usage.completion_tokens == 5
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_tool_calls_parsed():
    respx.post(f"{BASE}/api/chat").mock(
        return_value=httpx.Response(
            200,
            text=_ndjson(
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "read_file",
                                    "arguments": {"path": "x.py"},
                                }
                            }
                        ],
                    },
                    "done": False,
                },
                {
                    "message": {"role": "assistant", "content": ""},
                    "done": True,
                },
            ),
        )
    )
    provider = OllamaProvider(BASE, "m")
    events = await _collect(
        provider, [user("read x")], tools=[{"type": "function"}]
    )
    end = events[-1]
    assert isinstance(end, TurnEnd)
    assert len(end.message.tool_calls) == 1
    call = end.message.tool_calls[0]
    assert call.name == "read_file" and call.arguments == {"path": "x.py"}
    assert call.id
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_string_arguments_are_parsed():
    respx.post(f"{BASE}/api/chat").mock(
        return_value=httpx.Response(
            200,
            text=_ndjson(
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "t",
                                    "arguments": '{"a": 1}',
                                }
                            }
                        ],
                    },
                    "done": True,
                },
            ),
        )
    )
    provider = OllamaProvider(BASE, "m")
    events = await _collect(provider, [user("x")])
    assert events[-1].message.tool_calls[0].arguments == {"a": 1}
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_http_error_yields_error_event():
    respx.post(f"{BASE}/api/chat").mock(
        return_value=httpx.Response(404, text='{"error":"model not found"}')
    )
    provider = OllamaProvider(BASE, "missing")
    events = await _collect(provider, [user("hi")])
    assert isinstance(events[-1], ErrorEvent)
    assert "404" in events[-1].error
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_connection_error_yields_error_event():
    respx.post(f"{BASE}/api/chat").mock(
        side_effect=httpx.ConnectError("refused")
    )
    provider = OllamaProvider(BASE, "m")
    events = await _collect(provider, [user("hi")])
    assert isinstance(events[-1], ErrorEvent)
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_unload_sends_zero_keep_alive():
    route = respx.post(f"{BASE}/api/chat").mock(
        return_value=httpx.Response(
            200, json={"done": True, "done_reason": "unload"}
        )
    )
    provider = OllamaProvider(BASE, "m")
    await provider.unload()
    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body == {"model": "m", "messages": [], "keep_alive": 0}
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_unload_swallows_connection_errors():
    respx.post(f"{BASE}/api/chat").mock(
        side_effect=httpx.ConnectError("refused")
    )
    provider = OllamaProvider(BASE, "m")
    await provider.unload()  # must not raise
    await provider.close()


def test_wire_format():
    assistant = Message(
        role="assistant",
        content="calling",
        thinking="secret reasoning",
        tool_calls=[
            ToolCall(id="c1", name="bash", arguments={"command": "ls"})
        ],
    )
    wire = to_wire(assistant)
    assert "thinking" not in wire  # reasoning is not replayed
    assert wire["tool_calls"][0]["function"]["name"] == "bash"

    result = tool_result(assistant.tool_calls[0], "file.txt")
    wire = to_wire(result)
    assert wire == {"role": "tool", "content": "file.txt", "tool_name": "bash"}


def test_body_splits_options():
    provider = OllamaProvider(
        BASE,
        "m",
        options={"think": True, "keep_alive": "10m", "num_ctx": 32768},
    )
    body = provider._body([user("hi")], tools=None)
    assert body["think"] is True
    assert body["keep_alive"] == "10m"
    assert body["options"] == {"num_ctx": 32768}
    assert "tools" not in body


@pytest.mark.asyncio
@respx.mock
async def test_model_info_queries_api_show():
    route = respx.post(f"{BASE}/api/show").mock(
        return_value=httpx.Response(
            200,
            json={"model_info": {"qwen35moe.context_length": 262144}},
        )
    )
    provider = OllamaProvider(BASE, "qwen3.6:latest")
    info = await provider.model_info()
    body = json.loads(route.calls.last.request.content)
    assert body == {"model": "qwen3.6:latest"}
    assert OllamaProvider.context_length_from_info(info) == 262144
    await provider.close()


def test_context_length_key_is_family_prefixed():
    """Real /api/show namespaces the key by model family."""
    prefixed = {"model_info": {"llama.context_length": 8192}}
    assert OllamaProvider.context_length_from_info(prefixed) == 8192


def test_context_length_plain_key_still_accepted():
    plain = {"model_info": {"context_length": 4096}}
    assert OllamaProvider.context_length_from_info(plain) == 4096


def test_context_length_missing_or_junk_is_none():
    assert OllamaProvider.context_length_from_info({}) is None
    assert (
        OllamaProvider.context_length_from_info(
            {"model_info": {"llama.embedding_length": 4096}}
        )
        is None
    )
    assert (
        OllamaProvider.context_length_from_info(
            {"model_info": {"llama.context_length": "not a number"}}
        )
        is None
    )
