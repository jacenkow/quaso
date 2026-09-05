"""The agent loop.

Depends only on interfaces, never on a concrete provider, tool or UI.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from quaso.config import AgentConfig, ContextConfig
from quaso.context import ContextManager
from quaso.events import (
    CompactionEvent,
    ErrorEvent,
    Event,
    NoticeEvent,
    TextDelta,
    ToolCallEvent,
    ToolResultEvent,
    TurnEnd,
)
from quaso.hooks import HookRunner
from quaso.messages import Message, ToolCall, tool_result, user
from quaso.output_store import ToolOutputStore
from quaso.permissions import PermissionPolicy
from quaso.providers.base import Provider
from quaso.session import Session
from quaso.tools.base import Tool, ToolContext, ToolError, truncate
from quaso.tools.context import REQUEST_KEY as COMPACT_REQUEST_KEY
from quaso.tools.registry import ToolRegistry


@dataclass(frozen=True)
class _Step:
    """A call that passed validation and permission, ready to run."""

    tool: Tool
    params: BaseModel
    limit: int


@dataclass(frozen=True)
class _Refused:
    """A call that never ran, carrying the reason for the model."""

    message: str
    limit: int


def concurrent_groups(
    calls: list[ToolCall], tools: ToolRegistry
) -> list[list[ToolCall]]:
    """Split calls into runs that may overlap, in the order given.

    Only consecutive calls group: a write between two reads may be
    precisely what the second read is meant to see, so it ends the run
    before it and starts a new one after.
    """
    groups: list[list[ToolCall]] = []
    for call in calls:
        tool = tools.get(call.name)
        if tool is not None and tool.concurrent:
            if groups and groups[-1] and _safe(groups[-1][-1], tools):
                groups[-1].append(call)
                continue
            groups.append([call])
        else:
            groups.append([call])
    return groups


def _safe(call: ToolCall, tools: ToolRegistry) -> bool:
    tool = tools.get(call.name)
    return tool is not None and tool.concurrent


class Agent:
    def __init__(
        self,
        provider: Provider,
        tools: ToolRegistry,
        permissions: PermissionPolicy,
        session: Session,
        tool_context: ToolContext,
        config: AgentConfig | None = None,
        context: ContextManager | None = None,
        hooks: HookRunner | None = None,
        output_store: ToolOutputStore | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.permissions = permissions
        self.session = session
        self.tool_context = tool_context
        self.config = config or AgentConfig()
        self.context = context or ContextManager(
            ContextConfig(), max_tokens=32768
        )
        self.hooks = hooks
        self.output_store = output_store
        self._partial: list[str] = []

    async def compact(self) -> CompactionEvent | None:
        messages, before, after = await self.context.compact(
            self.session.messages, self.provider
        )
        if messages is self.session.messages:
            return None
        self.session.replace_history(messages)
        return CompactionEvent(before_tokens=before, after_tokens=after)

    def context_fraction(self) -> float:
        return self.context.fraction(self.session.messages)

    @staticmethod
    def _failed(message: str, limit: int) -> tuple[str, bool, str]:
        """A failed call, bounded to the same budget as a successful one."""
        return truncate(message, limit), True, ""

    def _bound(self, output: str, limit: int) -> str:
        if self.output_store is None:
            return truncate(output, limit)
        return self.output_store.bound(output, limit)

    def _context_notice(self) -> Message | None:
        """Tell the model how much room it has left, once room is tight.

        Kept out of the stored history and appended only at the very end of
        the request, so the cached prompt prefix does not change each turn.
        """
        fraction = self.context_fraction()
        if fraction < self.context.config.notice_threshold:
            return None
        used = self.context.usage(self.session.messages)
        left = max(0, self.context.max_tokens - used)
        if fraction >= 0.9:
            advice = (
                "Call compact now, before your next tool call, or earlier "
                "work will be dropped to make room."
            )
        else:
            advice = (
                "Read narrowly, delegate exploration to the task tool, and "
                "call compact once the current sub-task is done."
            )
        return Message(
            role="system",
            content=f"[context: {fraction:.0%} used, ~{left} tokens left. "
            f"{advice}]",
        )

    def flush_partial(self) -> bool:
        """Commit interrupted output so the user turn has a reply."""
        text = "".join(self._partial).strip()
        self._partial = []
        if not text:
            return False
        self.session.append(
            Message(role="assistant", content=f"{text}\n[interrupted by user]")
        )
        return True

    async def run(self, user_input: str) -> AsyncIterator[Event]:
        """Run one user turn, which may take many round-trips."""
        self.session.append(user(user_input))
        schemas = (
            self.tools.schemas() if self.provider.capabilities.tools else None
        )

        for _ in range(self.config.max_iterations):
            if self.context.should_compact(self.session.messages) and (
                event := await self.compact()
            ):
                yield event

            turn_end: TurnEnd | None = None
            self._partial = []
            request = list(self.session.messages)
            if (notice := self._context_notice()) is not None:
                request.append(notice)
            stream = self.provider.stream(request, tools=schemas)
            async for event in stream:
                yield event
                if isinstance(event, TextDelta):
                    self._partial.append(event.text)
                elif isinstance(event, TurnEnd):
                    turn_end = event
                    self._partial = []
                elif isinstance(event, ErrorEvent):
                    return

            if turn_end is None:
                yield ErrorEvent("provider stream ended without a TurnEnd")
                return

            if turn_end.usage.prompt_tokens:
                self.context.last_prompt_tokens = (
                    turn_end.usage.prompt_tokens
                    + turn_end.usage.completion_tokens
                )

            assistant = turn_end.message
            self.session.append(assistant)
            if not assistant.tool_calls:
                return

            for group in concurrent_groups(assistant.tool_calls, self.tools):
                # Announced together, so a batch reads as one wait rather
                # than a stall between each result.
                for call in group:
                    yield ToolCallEvent(call)
                results = await self._execute_group(group)
                for call, (output, is_error, hook_notice) in zip(
                    group, results, strict=True
                ):
                    yield ToolResultEvent(call, output, is_error)
                    if hook_notice:
                        yield NoticeEvent(hook_notice)
                    self.session.append(tool_result(call, output))

            # Deferred until every result is recorded: compacting inline
            # would rewrite the history these appends belong to.
            requested = self.tool_context.extra.pop(COMPACT_REQUEST_KEY, False)
            if requested and (event := await self.compact()):
                yield event

        yield ErrorEvent(
            f"Stopped after {self.config.max_iterations} iterations "
            "without finishing."
        )

    async def _execute(self, call: ToolCall) -> tuple[str, bool, str]:
        """Return (output, is_error, notice)."""
        return (await self._execute_group([call]))[0]

    async def _execute_group(
        self, calls: list[ToolCall]
    ) -> list[tuple[str, bool, str]]:
        """Run a group of calls together, answering in the order asked.

        Preparation stays serial so that a permission prompt never appears
        while another tool is already running, and the post-tool hooks run
        one at a time because they are shell commands. Only the tools
        themselves overlap, which is where the waiting was.
        """
        steps = [await self._prepare(call) for call in calls]
        outcomes = await asyncio.gather(
            *(self._invoke(step) for step in steps)
        )

        results: list[tuple[str, bool, str]] = []
        for call, step, (output, is_error) in zip(
            calls, steps, outcomes, strict=True
        ):
            notice = ""
            if step is not None and not is_error and self.hooks:
                notice = await self.hooks.post_tool_use(
                    call.name, call.arguments, output
                )
            results.append((output, is_error, notice))
        return results

    async def _prepare(self, call: ToolCall) -> _Step | _Refused:
        """Resolve, validate and authorise a call without running it."""
        tool = self.tools.get(call.name)
        if tool is None:
            available = ", ".join(self.tools.names())
            return _Refused(
                f"Error: unknown tool {call.name!r}. Available: {available}",
                self.tool_context.max_output_chars,
            )

        # Errors land in history exactly like results do, and some are far
        # from small: a validation failure quotes the offending argument
        # back in full. Every exit below goes through the same budget.
        limit = tool.output_limit(self.tool_context)
        try:
            params = tool.Params.model_validate(call.arguments)
        except ValidationError as exc:
            # Returned rather than raised so the model can correct itself.
            return _Refused(
                f"Error: invalid arguments for {call.name}: {exc}", limit
            )

        if self.hooks:
            outcome = await self.hooks.pre_tool_use(call.name, call.arguments)
            if outcome.blocked:
                return _Refused(
                    f"Error: blocked by hook: {outcome.reason}", limit
                )

        decision = await self.permissions.check(
            tool, params, self.tool_context, detail=tool.describe(params)
        )
        if not decision.allowed:
            return _Refused(
                f"Error: permission denied ({decision.reason})", limit
            )
        return _Step(tool=tool, params=params, limit=limit)

    async def _invoke(self, step: _Step | _Refused) -> tuple[str, bool]:
        """Run one prepared call. Safe to gather; touches no shared state."""
        if isinstance(step, _Refused):
            return truncate(step.message, step.limit), True
        try:
            output = await step.tool.run(step.params, self.tool_context)
        except ToolError as exc:
            return truncate(f"Error: {exc}", step.limit), True
        except Exception as exc:
            message = f"Error: {type(exc).__name__}: {exc}"
            return truncate(message, step.limit), True
        return self._bound(output, step.limit), False
