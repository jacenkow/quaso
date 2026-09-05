"""Adapts a remote MCP tool to the local Tool interface.

Names are prefixed mcp__<server>__<tool> so a server cannot shadow a
built-in.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, create_model

from quaso.mcp.client import MCPClient, MCPError
from quaso.tools.base import Tool, ToolContext, ToolError

_JSON_TYPES: dict[str, Any] = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def model_from_json_schema(
    name: str, schema: dict[str, Any]
) -> type[BaseModel]:
    """Build a model from a server schema, mapping the common subset.

    Unknown constructs fall back to Any so an exotic schema never makes a
    server uncallable.
    """
    properties = schema.get('properties') or {}
    required = set(schema.get('required') or [])
    fields: dict[str, Any] = {}
    for key, spec in properties.items():
        if not isinstance(spec, dict):
            continue
        python_type = _JSON_TYPES.get(spec.get('type'), Any)
        description = spec.get('description', "")
        if key in required:
            fields[key] = (python_type, Field(description=description))
        elif python_type is Any:
            fields[key] = (Any, Field(default=None, description=description))
        else:
            fields[key] = (
                python_type | None,
                Field(default=None, description=description),
            )
    if not fields:
        return create_model(name)
    return create_model(name, **fields)


class MCPTool(Tool):
    # Servers rarely declare read-only tools, so assume side effects.
    mutates = True

    def __init__(self, client: MCPClient, definition: dict[str, Any]) -> None:
        self.client = client
        self.remote_name = definition.get('name', "unknown")
        self.name = f"mcp__{client.name}__{self.remote_name}"
        self.description = (
            definition.get('description') or f"MCP tool {self.remote_name}"
        )
        self._input_schema = definition.get('inputSchema') or {
            "type": "object",
            "properties": {},
        }
        self.Params = model_from_json_schema(
            f"MCP_{client.name}_{self.remote_name}", self._input_schema
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self._input_schema,
            },
        }

    async def run(self, params: BaseModel, ctx: ToolContext) -> str:
        arguments = {
            k: v for k, v in params.model_dump().items() if v is not None
        }
        try:
            output = await self.client.call_tool(self.remote_name, arguments)
        except MCPError as exc:
            raise ToolError(str(exc)) from exc
        return output
