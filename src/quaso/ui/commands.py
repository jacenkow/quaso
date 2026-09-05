"""The slash commands, defined once.

Completion, /help and the dispatcher all read this, so a new command
cannot appear in one and be missing from the others.
"""

from __future__ import annotations

COMMANDS: dict[str, str] = {
    "/help": "show these commands",
    "/new": "start a fresh session",
    "/compact": "summarise history now to reclaim context",
    "/context": "token usage and message count",
    "/tools": "list available tools",
    "/mcp": "configured MCP servers and their tools",
    "/sessions": "recent saved sessions",
    "/model": "show or switch model",
    "/quit": "exit",
}

# Accepted but not offered: they duplicate a listed command.
ALIASES: dict[str, str] = {
    "/exit": "/quit",
    "/q": "/quit",
}


def canonical(command: str) -> str:
    return ALIASES.get(command, command)


def help_text() -> str:
    width = max(len(name) for name in COMMANDS)
    lines = [f"  {name:<{width}}  {desc}" for name, desc in COMMANDS.items()]
    return "\n".join(
        [
            *lines,
            "",
            "  Ctrl-C interrupts a running turn, Ctrl-D exits.",
        ]
    )
