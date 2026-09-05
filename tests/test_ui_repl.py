"""The REPL must not treat file or model content as rich markup.

Square brackets are everywhere in real output (TOML tables, Python type
hints, log prefixes) and rich would silently eat them as style tags.
"""

from __future__ import annotations

import pytest
from rich.console import Console

from quaso.events import (
    ErrorEvent,
    NoticeEvent,
    TextDelta,
    ToolCallEvent,
    ToolResultEvent,
)
from quaso.messages import ToolCall
from quaso.permissions import PermissionRequest
from quaso.ui.repl import ReplUI


@pytest.fixture
def ui():
    """A ReplUI with a recording console and no prompt_toolkit session."""
    repl = ReplUI.__new__(ReplUI)
    repl.console = Console(record=True, width=100, force_terminal=False)
    repl._streaming = None
    repl.context_fraction = 0.0
    return repl


def rendered(ui) -> str:
    """Export the recorded output. Rich clears the buffer, so call once."""
    return ui.console.export_text()


def test_tool_output_keeps_square_brackets(ui):
    call = ToolCall(id="c", name="read_file", arguments={"path": "x.toml"})
    ui.render(ToolResultEvent(call, "[build-system]\nrequires = []", False))
    assert "[build-system]" in rendered(ui)


def test_model_text_keeps_square_brackets(ui):
    ui.render(TextDelta("use list[str] and dict[str, int]"))
    output = rendered(ui)
    assert "list[str]" in output
    assert "dict[str, int]" in output


def test_error_text_keeps_square_brackets(ui):
    ui.render(ErrorEvent("failed on [core] section"))
    assert "[core]" in rendered(ui)


def test_notice_keeps_square_brackets(ui):
    ui.render(NoticeEvent("[hook] reformatted 3 files"))
    assert "[hook]" in rendered(ui)


def test_tool_call_arguments_are_escaped(ui):
    call = ToolCall(id="c", name="grep", arguments={"pattern": "[a-z]+"})
    ui.render(ToolCallEvent(call))
    output = rendered(ui)
    assert "grep" in output
    assert "[a-z]+" in output


def test_permission_detail_keeps_square_brackets(ui):
    request = PermissionRequest(
        tool_name="write_file",
        primary_arg="pyproject.toml",
        detail="write_file(pyproject.toml)\n[project]\nname = 'x'",
    )
    # Only the rendering half is exercised; the answer is read separately.
    ui._break_stream()
    ui.console.print("\n[bold yellow]Permission:[/]", end=" ")
    ui.console.print(request.detail, markup=False, highlight=False)
    assert "[project]" in rendered(ui)


def test_todo_output_is_not_truncated(ui):
    call = ToolCall(id="c", name="todo_write", arguments={})
    todos = "\n".join(f"[ ] task {i}" for i in range(8))
    ui.render(ToolResultEvent(call, todos, False))
    output = rendered(ui)
    assert "task 7" in output
    assert "+" not in output.split("task 7")[-1]


def test_long_tool_output_is_truncated(ui):
    call = ToolCall(id="c", name="read_file", arguments={})
    ui.render(ToolResultEvent(call, "\n".join(str(i) for i in range(50))))
    output = rendered(ui)
    assert "+46 lines" in output
    assert "49" not in output
