"""MCP client over stdio, using newline-delimited JSON-RPC 2.0."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from quaso.config import MCPServerConfig

PROTOCOL_VERSION = "2025-06-18"

_REQUEST_TIMEOUT = 60.0


@dataclass
class MCPServerStatus:
    name: str
    command: str
    connected: bool
    tools: list[str] = field(default_factory=list)
    error: str = ""


class MCPError(Exception):
    pass


class MCPClient:
    def __init__(self, name: str, config: MCPServerConfig) -> None:
        self.name = name
        self.config = config
        self.tools: list[dict[str, Any]] = []
        self.error = ""
        self._process: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None
        self._next_id = 0

    async def start(self) -> bool:
        """Spawn and handshake. Never raises; returns success."""
        try:
            self._process = await asyncio.create_subprocess_exec(
                self.config.command,
                *self.config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={**os.environ, **self.config.env},
            )
            self._reader = asyncio.create_task(self._read_loop())
            await self._request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "quaso", "version": "0.1.0"},
                },
            )
            await self._notify("notifications/initialized", {})
            result = await self._request("tools/list", {})
            self.tools = result.get('tools', [])
            return True
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            await self.stop()
            return False

    async def stop(self) -> None:
        if self._reader:
            self._reader.cancel()
            self._reader = None
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except (TimeoutError, ProcessLookupError):
                with suppress(ProcessLookupError):
                    self._process.kill()
        self._process = None

    def status(self) -> MCPServerStatus:
        return MCPServerStatus(
            name=self.name,
            command=" ".join([self.config.command, *self.config.args]),
            connected=bool(self.tools)
            or (self._process is not None and not self.error),
            tools=[tool.get('name', "?") for tool in self.tools],
            error=self.error,
        )

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> str:
        result = await self._request(
            "tools/call", {"name": tool_name, "arguments": arguments}
        )
        parts = []
        for block in result.get('content', []):
            if block.get('type') == "text":
                parts.append(block.get('text', ""))
            else:
                parts.append(f"[{block.get('type')} content]")
        text = "\n".join(parts).strip()
        if result.get('isError'):
            raise MCPError(text or "tool reported an error")
        return text or "(no output)"

    async def _read_loop(self) -> None:
        assert self._process and self._process.stdout
        while True:
            line = await self._process.stdout.readline()
            if not line:
                break
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            message_id = message.get('id')
            if message_id is None:
                continue
            future = self._pending.pop(message_id, None)
            if future is None or future.done():
                continue
            if "error" in message:
                detail = message['error']
                future.set_exception(
                    MCPError(str(detail.get('message', detail)))
                )
            else:
                future.set_result(message.get('result', {}))

        for future in self._pending.values():
            if not future.done():
                future.set_exception(MCPError("server closed the connection"))
        self._pending.clear()

    async def _send(self, payload: dict[str, Any]) -> None:
        if not self._process or not self._process.stdin:
            raise MCPError("server is not running")
        self._process.stdin.write((json.dumps(payload) + "\n").encode())
        await self._process.stdin.drain()

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._send(
            {"jsonrpc": "2.0", "method": method, "params": params}
        )

    async def _request(
        self, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        try:
            return await asyncio.wait_for(future, timeout=_REQUEST_TIMEOUT)
        except TimeoutError:
            self._pending.pop(request_id, None)
            raise MCPError(f"{method} timed out") from None
