from __future__ import annotations

from quaso.providers.toolparse import extract_tool_calls

KNOWN = {"read_file", "bash"}

READ_CALL = '{"name": "read_file", "arguments": {"path": "a.py"}}'
BASH_CALL = '{"name": "bash", "arguments": {"command": "ls"}}'


def test_tagged_tool_call():
    text = f"Sure.\n<tool_call>{READ_CALL}</tool_call>"
    cleaned, calls = extract_tool_calls(text, KNOWN)
    assert len(calls) == 1
    assert calls[0].name == "read_file"
    assert calls[0].arguments == {"path": "a.py"}
    assert "tool_call" not in cleaned
    assert cleaned.strip() == "Sure."


def test_fenced_json_block():
    text = f"Let me look.\n```json\n{BASH_CALL}\n```"
    _, calls = extract_tool_calls(text, KNOWN)
    assert len(calls) == 1
    assert calls[0].name == "bash"


def test_stringified_arguments():
    payload = '{"name": "bash", "arguments": "{\\"command\\": \\"pwd\\"}"}'
    _, calls = extract_tool_calls(f"<tool_call>{payload}</tool_call>", KNOWN)
    assert calls[0].arguments == {"command": "pwd"}


def test_nested_function_payload():
    payload = '{"function": {"name": "bash", "arguments": {"command": "id"}}}'
    _, calls = extract_tool_calls(f"<tool_call>{payload}</tool_call>", KNOWN)
    assert calls[0].name == "bash"
    assert calls[0].arguments == {"command": "id"}


def test_unknown_tool_names_are_ignored():
    """A model discussing a tool must not trigger execution."""
    payload = '{"name": "launch_missiles", "arguments": {}}'
    text = f"<tool_call>{payload}</tool_call>"
    cleaned, calls = extract_tool_calls(text, KNOWN)
    assert calls == []
    assert cleaned == text


def test_prose_about_tools_is_untouched():
    text = "You could call read_file with a path argument to see it."
    cleaned, calls = extract_tool_calls(text, KNOWN)
    assert calls == []
    assert cleaned == text


def test_multiple_calls():
    text = (
        f"<tool_call>{READ_CALL}</tool_call><tool_call>{READ_CALL}</tool_call>"
    )
    cleaned, calls = extract_tool_calls(text, KNOWN)
    assert len(calls) == 2
    assert cleaned == ""


def test_no_known_tools_disables_parsing():
    text = f"<tool_call>{READ_CALL}</tool_call>"
    cleaned, calls = extract_tool_calls(text, set())
    assert calls == []
    assert cleaned == text


def test_malformed_json_is_left_alone():
    text = "<tool_call>{not json at all}</tool_call>"
    cleaned, calls = extract_tool_calls(text, KNOWN)
    assert calls == []
    assert cleaned == text
