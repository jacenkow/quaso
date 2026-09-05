from __future__ import annotations

import pytest
from rich.console import Console

from quaso.ui import banner

STATUS = "ollama/qwen3.6:latest · default · 11 tools · web duckduckgo"


def render(width: int = 100, terminal: bool = True, compact: bool = False):
    console = Console(
        record=True, width=width, height=25, force_terminal=terminal
    )
    banner.render(console, version="0.1.0", status=STATUS, compact=compact)
    return console.export_text()


def test_full_banner_on_a_wide_terminal():
    output = render()
    assert "█" in output
    assert "quaso" in output
    assert "0.1.0" in output
    assert STATUS in output


def test_nothing_is_printed_when_not_a_terminal():
    """Piped output must stay clean for -p and shell pipelines."""
    assert render(terminal=False) == ""


def test_narrow_terminal_falls_back_to_one_line():
    output = render(width=40)
    assert "█" not in output
    assert "quaso" in output
    assert len(output.strip().splitlines()) == 1


def test_resumed_session_gets_the_compact_line():
    output = render(compact=True)
    assert "█" not in output
    assert "quaso" in output


@pytest.mark.parametrize("width", [20, 40, 60, 79, 80, 120, 200])
def test_never_wraps_at_any_width(width):
    """Every line must fit, or the banner looks broken instead of pretty."""
    output = render(width=width)
    for line in output.splitlines():
        assert len(line) <= width


def test_model_name_digits_are_not_highlighted():
    """Rich would otherwise recolour the 3 and 6 inside qwen3.6."""
    console = Console(record=True, width=100, height=25, force_terminal=True)
    banner.render(console, version="0.1.0", status=STATUS)
    ansi = console.export_text(styles=True)
    assert "qwen3.6:latest" in ansi.replace("\x1b[2m", "")


def test_wordmark_covers_every_letter():
    text = banner.wordmark().plain
    assert text.count("\n") == 5
    assert "█" in text
