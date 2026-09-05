"""MCP client tests against a real stdio server subprocess."""

from __future__ import annotations

import sys
import textwrap

import pytest

from quaso.config import MCPServerConfig
from quaso.mcp.client import MCPClient
from quaso.mcp.tool import MCPTool, model_from_json_schema
from quaso.tools.base import ToolContext, ToolError

FAKE_SERVER = textwrap.dedent(
    """
    import json, sys

    SCHEMA = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    TOOLS = [
        {
            "name": "echo",
            "description": "Echo text back",
            "inputSchema": SCHEMA,
        }
    ]

    def handle(method, params):
        if method == "initialize":
            return {"protocolVersion": "2025-06-18", "capabilities": {}}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method != "tools/call":
            return {}
        text = params.get("arguments", {}).get("text")
        if text == "boom":
            return {
                "content": [{"type": "text", "text": "exploded"}],
                "isError": True,
            }
        return {"content": [{"type": "text", "text": "echo: " + str(text)}]}

    for line in sys.stdin:
        if not line.strip():
            continue
        message = json.loads(line)
        if message.get("id") is None:
            continue
        reply = {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": handle(
                message.get("method"), message.get("params", {})
            ),
        }
        sys.stdout.write(json.dumps(reply) + "\\n")
        sys.stdout.flush()
    """
)


@pytest.fixture
def server_config(tmp_path):
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(FAKE_SERVER)
    return MCPServerConfig(command=sys.executable, args=[str(script)])


@pytest.mark.asyncio
async def test_client_handshake_and_tool_listing(server_config):
    client = MCPClient("fake", server_config)
    assert await client.start()
    try:
        assert [t['name'] for t in client.tools] == ["echo"]
        status = client.status()
        assert status.connected
        assert status.tools == ["echo"]
        assert not status.error
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_tool_call_roundtrip(server_config, tmp_path):
    client = MCPClient("fake", server_config)
    assert await client.start()
    try:
        tool = MCPTool(client, client.tools[0])
        assert tool.name == "mcp__fake__echo"
        params = tool.Params.model_validate({"text": "hello"})
        output = await tool.run(params, ToolContext(cwd=tmp_path))
        assert output == "echo: hello"
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_server_error_becomes_tool_error(server_config, tmp_path):
    client = MCPClient("fake", server_config)
    assert await client.start()
    try:
        tool = MCPTool(client, client.tools[0])
        params = tool.Params.model_validate({"text": "boom"})
        with pytest.raises(ToolError, match="exploded"):
            await tool.run(params, ToolContext(cwd=tmp_path))
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_missing_server_binary_fails_cleanly():
    client = MCPClient("nope", MCPServerConfig(command="not-a-real-binary"))
    assert await client.start() is False
    assert client.error
    assert client.status().connected is False
    await client.stop()


def test_schema_passthrough_and_model_generation():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
        "required": ["a"],
    }
    model = model_from_json_schema("T", schema)
    assert model.model_validate({"a": "x"}).b is None
    with pytest.raises(ValueError):
        model.model_validate({"b": 1})


def test_model_from_empty_schema():
    model = model_from_json_schema("Empty", {"type": "object"})
    assert model.model_validate({}) is not None
