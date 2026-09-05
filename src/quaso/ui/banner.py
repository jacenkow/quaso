"""Startup banner.

Skipped when stdout is not a terminal, so piped output stays clean, and
replaced by a single line when the terminal is too narrow for the wordmark.
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

_GLYPHS = {
    "q": [" ████ ", "█    █", "█    █", " █████", "     █"],
    "u": ["█    █", "█    █", "█    █", "█    █", " ████ "],
    "a": [" ████ ", "█    █", "██████", "█    █", "█    █"],
    "s": [" █████", "█     ", " ████ ", "     █", "█████ "],
    "o": [" ████ ", "█    █", "█    █", "█    █", " ████ "],
}

# Top to bottom: butter through to crust.
_GRADIENT = ["#fde68a", "#fcd34d", "#fbbf24", "#f59e0b", "#d97706"]

_ACCENT = "#fbbf24"
_WORDMARK = "quaso"
_TAGLINE = "a terminal coding agent for self-hosted models"

# The tagline line is wider than the wordmark, so it sets the floor below
# which the full banner wraps into a mess.
MIN_WIDTH = len(_TAGLINE) + len(_WORDMARK) + 20


def wordmark(word: str = _WORDMARK) -> Text:
    text = Text()
    for row in range(len(_GRADIENT)):
        for letter in word:
            text.append(_GLYPHS[letter][row] + " ", style=_GRADIENT[row])
        text.append("\n")
    return text


def render(
    console: Console,
    version: str,
    status: str,
    compact: bool = False,
) -> None:
    if not console.is_terminal:
        return
    # highlight=False throughout: rich would otherwise colour the digits
    # inside a model name like qwen3.6:latest.
    if compact or console.width < MIN_WIDTH:
        console.print(
            f"[bold {_ACCENT}]quaso[/] [dim]v{version} · {status}[/]",
            highlight=False,
            no_wrap=True,
            overflow="ellipsis",
        )
        return

    console.print()
    console.print(wordmark())
    console.print(
        f"[bold {_ACCENT}]🥐 quaso[/] [dim]v{version}[/]  [dim]·[/]  "
        f"[dim]{_TAGLINE}[/]",
        highlight=False,
    )
    console.print(f"[dim]{status}[/]", highlight=False)
    console.print(
        f"[dim]type[/] [{_ACCENT}]/help[/] [dim]for commands[/]",
        highlight=False,
    )
