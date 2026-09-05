"""Interactive terminal frontend."""

from __future__ import annotations

from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import ANSI, HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.markup import escape

from quaso.events import (
    CompactionEvent,
    ErrorEvent,
    Event,
    NoticeEvent,
    TextDelta,
    ThinkingDelta,
    ToolCallEvent,
    ToolResultEvent,
    TurnEnd,
)
from quaso.permissions import Answer, PermissionRequest
from quaso.private import make_private
from quaso.tools.ask import Question
from quaso.ui.base import UI
from quaso.ui.commands import COMMANDS

_HISTORY_PATH = Path(".quaso") / "history"
_PREVIEW_LINES = 4

_TOOLBAR_STYLE = Style.from_dict(
    {
        "bottom-toolbar": "bg:#292524 #a8a29e",
        "key": "bg:#292524 #fbbf24 bold",
        "warn": "bg:#292524 #f59e0b",
        "alert": "bg:#292524 #ef4444 bold",
    }
)


class SlashCompleter(Completer):
    """Offer commands while a line that starts with '/' is being typed."""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        # Only the command word itself: once there is an argument, the
        # user has chosen and suggestions are just noise.
        if not text.startswith("/") or " " in text:
            return
        for name, description in COMMANDS.items():
            if name.startswith(text):
                yield Completion(
                    name,
                    start_position=-len(text),
                    display=name,
                    display_meta=description,
                )


def _primary(arguments: dict) -> str:
    for value in arguments.values():
        if isinstance(value, str) and value:
            return value if len(value) <= 80 else value[:77] + "..."
    return ""


class ReplUI(UI):
    can_ask = True

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()
        _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        # prompt_toolkit creates this on the first entry, under the
        # ordinary umask. It holds everything typed at the prompt, so it
        # is claimed here before anything is written to it.
        _HISTORY_PATH.touch(exist_ok=True)
        make_private(_HISTORY_PATH)
        self._input = PromptSession(
            history=FileHistory(str(_HISTORY_PATH)),
            bottom_toolbar=self._toolbar,
            style=_TOOLBAR_STYLE,
            completer=SlashCompleter(),
            complete_while_typing=True,
            reserve_space_for_menu=6,
        )
        self._streaming: str | None = None
        self.context_fraction = 0.0
        self.model = ""

    def _toolbar(self) -> HTML:
        parts = [
            "<key>enter</key> send",
            "<key>/</key> commands",
            "<key>↑↓</key> history",
            "<key>ctrl-c</key> interrupt",
            "<key>ctrl-d</key> exit",
        ]
        if self.model:
            parts.append(self.model)
        if self.context_fraction >= 0.5:
            percent = int(self.context_fraction * 100)
            tag = "alert" if percent >= 80 else "warn"
            parts.append(f"<{tag}>{percent}% ctx</{tag}>")
        return HTML("  " + "   ".join(parts))

    async def get_input(self) -> str:
        self._streaming = None
        return await self._input.prompt_async(self._prompt())

    def _prompt(self) -> ANSI:
        with self.console.capture() as capture:
            self.console.print("\n[bold #fbbf24]❯[/] ", end="")
        return ANSI(capture.get())

    def _break_stream(self) -> None:
        if self._streaming is not None:
            self.console.print()
            self._streaming = None

    def _stream(self, kind: str, text: str, style: str | None = None) -> None:
        if self._streaming not in (None, kind):
            self.console.print("\n")
        self._streaming = kind
        # markup=False: model output and file contents contain square
        # brackets, which rich would otherwise eat as style tags.
        self.console.print(
            text,
            style=style,
            end="",
            soft_wrap=True,
            highlight=False,
            markup=False,
        )

    def render(self, event: Event) -> None:
        match event:
            case ThinkingDelta(text):
                self._stream("thinking", text, style="grey50 italic")
            case TextDelta(text):
                self._stream("text", text)
            case ToolCallEvent(call):
                self._break_stream()
                arg = escape(_primary(call.arguments))
                self.console.print(
                    f"● [bold cyan]{escape(call.name)}[/]([dim]{arg}[/])"
                )
            case ToolResultEvent(call, output, is_error):
                self._render_result(call.name, output, is_error)
            case CompactionEvent(before, after):
                self._break_stream()
                self.console.print(
                    f"⋯ compacted context: ~{before} → ~{after} tokens",
                    style="yellow",
                )
            case NoticeEvent(text):
                self._break_stream()
                self.console.print(f"⋯ {text}", style="grey50", markup=False)
            case TurnEnd():
                self._break_stream()
            case ErrorEvent(error):
                self._break_stream()
                self.console.print(
                    f"✗ {error}", style="bold red", markup=False
                )

    def _render_result(
        self, tool_name: str, output: str, is_error: bool
    ) -> None:
        lines = output.splitlines() or [""]
        # The todo list is the point of calling todo_write, so show it all.
        if tool_name == "todo_write" and not is_error:
            shown = lines
        else:
            shown = lines[:_PREVIEW_LINES]
        summary = "\n    ".join(shown)
        if len(lines) > len(shown):
            summary += f"\n    … +{len(lines) - len(shown)} lines"
        self.console.print(
            f"  ⎿ {summary}",
            style="red" if is_error else "grey50",
            highlight=False,
            markup=False,
        )

    async def ask_permission(self, request: PermissionRequest) -> Answer:
        self._break_stream()
        detail = request.detail or request.tool_name
        if request.leaves_machine:
            # Named for what it costs rather than which tool it is: the
            # point of a local model is that what you paste stays here,
            # and this is where that stops being true.
            label = "Permission (this leaves your machine)"
        elif request.outside_workspace:
            label = "Permission (outside working directory)"
        else:
            label = "Permission"
        self.console.print(f"\n[bold yellow]{label}:[/]", end=" ")
        self.console.print(detail, markup=False, highlight=False)
        prompt = "Allow? [y]es / [a]lways this session / [n]o: "
        while True:
            answer = await self._input.prompt_async(prompt)
            match answer.strip().lower():
                case "y" | "yes":
                    return "allow"
                case "a" | "always":
                    return "always"
                case "n" | "no" | "":
                    return "deny"

    async def ask_question(self, question: Question) -> str:
        self._break_stream()
        self.console.print(
            f"\n[bold #fbbf24]?[/] {question.question}", highlight=False
        )
        for index, option in enumerate(question.options, 1):
            self.console.print(
                f"  [bold #fbbf24]{index}[/] {option}", highlight=False
            )
        hint = "answer, or a number: " if question.options else "answer: "
        answer = (await self._input.prompt_async(f"  {hint}")).strip()
        if answer.isdigit() and 1 <= int(answer) <= len(question.options):
            return question.options[int(answer) - 1]
        return answer

    def info(self, text: str) -> None:
        self._break_stream()
        self.console.print(text, style="grey50")
