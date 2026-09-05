"""Command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from contextlib import aclosing, suppress
from pathlib import Path

import httpx

from quaso import __version__
from quaso.agent.loop import Agent
from quaso.agent.prompts import build_subagent_prompt, build_system_prompt
from quaso.config import (
    Config,
    context_limit,
    load_config,
    resolved_provider,
)
from quaso.context import ContextManager
from quaso.events import ErrorEvent, TurnEnd
from quaso.execution.registry import create_executor
from quaso.hooks import HookRunner
from quaso.mcp.client import MCPClient
from quaso.mcp.tool import MCPTool
from quaso.output_store import ToolOutputStore
from quaso.permissions import PermissionPolicy
from quaso.private import secure_existing
from quaso.providers.ollama import OllamaProvider
from quaso.providers.registry import create_provider
from quaso.session import Session, find_session, list_sessions
from quaso.setup import Wizard, needs_setup
from quaso.skills import SkillStore, default_roots
from quaso.tools.ask import ASKER_KEY
from quaso.tools.base import ToolContext
from quaso.tools.registry import ToolRegistry
from quaso.tools.skill import STORE_KEY as SKILL_STORE_KEY
from quaso.tools.web import describe_search
from quaso.ui import banner
from quaso.ui.base import UI
from quaso.ui.commands import canonical, help_text
from quaso.ui.headless import HeadlessUI


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="quaso", description="An agent harness for open models"
    )
    parser.add_argument(
        "-p", "--prompt", help="Run one prompt non-interactively and exit"
    )
    parser.add_argument("--provider", help="Provider name")
    parser.add_argument("--model", help="Model name, e.g. qwen3.6:latest")
    parser.add_argument("--url", help="Provider base URL")
    parser.add_argument(
        "--mode",
        choices=["default", "acceptEdits", "readonly", "yolo"],
        help="Permission mode",
    )
    parser.add_argument(
        "-c",
        "--continue",
        dest="continue_",
        action="store_true",
        help="Resume the most recent session",
    )
    parser.add_argument(
        "--resume", metavar="ID", help="Resume a session by id or prefix"
    )
    parser.add_argument(
        "--no-mcp", action="store_true", help="Skip starting MCP servers"
    )
    parser.add_argument(
        "--no-banner", action="store_true", help="Skip the startup banner"
    )
    thinking = parser.add_mutually_exclusive_group()
    thinking.add_argument(
        "--think", action="store_true", default=None, help="Enable thinking"
    )
    thinking.add_argument(
        "--no-think",
        dest="think",
        action="store_false",
        help="Disable thinking",
    )
    parser.add_argument(
        "--version", action="version", version=f"quaso {__version__}"
    )
    return parser.parse_args(argv)


def _apply_overrides(config: Config, args: argparse.Namespace) -> Config:
    if args.provider:
        config.provider.name = args.provider
    if args.model:
        config.provider.model = args.model
    if args.url:
        config.provider.base_url = args.url
    if args.think is not None:
        config.provider.options["think"] = args.think
    if args.mode:
        config.permissions.mode = args.mode
    return config


async def _start_mcp(
    config: Config, tools: ToolRegistry, ui: UI
) -> list[MCPClient]:
    clients = []
    for name, server in config.mcp.servers.items():
        if not server.enabled:
            continue
        client = MCPClient(name, server)
        if await client.start():
            for definition in client.tools:
                tools.add(MCPTool(client, definition))
            clients.append(client)
        else:
            ui.info(f"MCP server {name!r} failed to start: {client.error}")
    return clients


def _format_mcp_status(clients: list[MCPClient], config: Config) -> str:
    if not config.mcp.servers:
        return (
            "No MCP servers configured. Add them under [mcp.servers] in "
            ".quaso/config.toml."
        )
    by_name = {client.name: client for client in clients}
    lines = []
    for name, server in config.mcp.servers.items():
        client = by_name.get(name)
        if client is None:
            state = "disabled" if not server.enabled else "not connected"
            lines.append(f"  {name}: {state} ({server.command})")
            continue
        status = client.status()
        lines.append(
            f"  {name}: connected, {len(status.tools)} tools "
            f"({status.command})"
        )
        lines += [f"      mcp__{name}__{t}" for t in status.tools]
    return "Configured MCP servers:\n" + "\n".join(lines)


async def _mcp_command(argv: list[str]) -> int:
    if argv and argv[0] not in ("list", "ls"):
        print(
            f"Unknown mcp subcommand {argv[0]!r} (try: quaso mcp list)",
            file=sys.stderr,
        )
        return 2
    config = load_config()
    ui = HeadlessUI()
    clients = await _start_mcp(config, ToolRegistry.default(config), ui)
    print(_format_mcp_status(clients, config))
    for client in clients:
        await client.stop()
    return 0


# A subagent has no user in front of it: it cannot delegate further, and
# its prompt tells it not to ask, so the tools are withheld to match.
_NO_SUBAGENT = {"task", "ask"}


def _make_subagent(parent: Agent, config: Config, cwd: Path):
    """Nested agent sharing tools and provider, with a fresh context."""

    async def spawn(prompt: str) -> str:
        tools = ToolRegistry(
            [t for t in parent.tools if t.name not in _NO_SUBAGENT]
        )
        child_context = ToolContext(
            cwd=cwd,
            max_output_chars=config.agent.max_tool_output_chars,
            tool_output_chars=config.agent.tool_output_chars,
            require_read_before_edit=config.agent.require_read_before_edit,
            executor=parent.tool_context.executor,
        )
        child_context.extra.update(parent.tool_context.extra)
        child = Agent(
            provider=parent.provider,
            tools=tools,
            permissions=parent.permissions,
            session=Session(
                build_subagent_prompt(cwd), root=cwd, persist=False
            ),
            tool_context=child_context,
            config=config.agent.model_copy(
                update={"max_iterations": config.agent.subagent_max_iterations}
            ),
            context=ContextManager(config.context, context_limit(config)),
            hooks=parent.hooks,
            output_store=parent.output_store,
        )
        child_context.compact = child.compact
        parts: list[str] = []
        async with aclosing(child.run(prompt)) as stream:
            async for event in stream:
                if isinstance(event, TurnEnd) and event.message.content:
                    parts.append(event.message.content)
        return parts[-1] if parts else "(subagent produced no output)"

    return spawn


def _build_agent(config: Config, ui: UI, persist: bool) -> Agent:
    cwd = Path.cwd()
    store = ToolOutputStore(cwd)
    store.sweep()
    secure_existing(cwd)
    skills = SkillStore(default_roots(cwd))
    tool_context = ToolContext(
        cwd=cwd,
        max_output_chars=config.agent.max_tool_output_chars,
        tool_output_chars=config.agent.tool_output_chars,
        require_read_before_edit=config.agent.require_read_before_edit,
        executor=create_executor(config.sandbox),
    )
    tool_context.extra[SKILL_STORE_KEY] = skills
    if ui.can_ask:
        tool_context.extra[ASKER_KEY] = ui.ask_question
    agent = Agent(
        provider=create_provider(resolved_provider(config)),
        tools=ToolRegistry.default(config),
        permissions=PermissionPolicy(config.permissions, ui.ask_permission),
        session=Session(
            build_system_prompt(cwd, skills.index(config.provider.model)),
            root=cwd,
            persist=persist,
        ),
        tool_context=tool_context,
        config=config.agent,
        context=ContextManager(config.context, context_limit(config)),
        hooks=HookRunner(config.hooks, cwd),
        output_store=store,
    )
    tool_context.subagent = _make_subagent(agent, config, cwd)
    tool_context.compact = agent.compact
    return agent


async def _consume(agent: Agent, ui: UI, text: str) -> bool:
    """Run one turn. Returns whether it finished without a fatal error.

    The REPL can ignore the answer, since the user is right there and has
    seen the error. A script cannot: `quaso -p` reports through its exit
    status, and reporting success for a turn that never ran makes it
    useless in anything automated.
    """
    ok = True
    async with aclosing(agent.run(text)) as stream:
        async for event in stream:
            if isinstance(event, ErrorEvent):
                ok = False
            ui.render(event)
    return ok


async def _run_turn(agent: Agent, ui: UI, text: str) -> None:
    """Run one turn, cancellable with Ctrl-C without ending the session."""
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(_consume(agent, ui, text))
    installed = True
    try:
        loop.add_signal_handler(signal.SIGINT, task.cancel)
    except (NotImplementedError, RuntimeError):
        installed = False
    try:
        await task
    except asyncio.CancelledError:
        ui.info("(interrupted)")
        agent.flush_partial()
        agent.session.repair_dangling_tool_calls()
    finally:
        if installed:
            with suppress(NotImplementedError, RuntimeError):
                loop.remove_signal_handler(signal.SIGINT)


def _resume_into(session: Session, args: argparse.Namespace, ui: UI) -> bool:
    """Load a previous session if asked. Returns whether one was loaded."""
    root = Path.cwd()
    path = None
    if args.resume:
        path = find_session(root, args.resume)
        if path is None:
            ui.info(f"No session matching {args.resume!r}; starting new.")
    elif args.continue_:
        sessions = list_sessions(root)
        path = sessions[0] if sessions else None
        if path is None:
            ui.info("No previous session found; starting new.")
    if path is None:
        return False
    try:
        session.load(path)
        ui.info(f"Resumed {path.stem} ({len(session.messages)} messages).")
        return True
    except (OSError, ValueError) as exc:
        ui.info(f"Could not resume {path.stem}: {exc}")
        return False


async def _handle_command(
    text: str,
    agent: Agent,
    ui: UI,
    config: Config,
    clients: list[MCPClient],
) -> bool:
    """Return True if the session should end."""
    raw_command, _, argument = text.partition(" ")
    command = canonical(raw_command)
    argument = argument.strip()
    match command:
        case "/quit":
            return True
        case "/new":
            agent.session.reset()
            agent.context.reset()
            ui.info("Started a new session.")
        case "/compact":
            event = await agent.compact()
            if event is None:
                ui.info("Nothing old enough to compact yet.")
            else:
                ui.render(event)
        case "/context":
            used = agent.context.usage(agent.session.messages)
            ui.info(
                f"~{used} / {agent.context.max_tokens} tokens "
                f"({agent.context_fraction():.0%}) · "
                f"{len(agent.session.messages)} messages"
            )
        case "/prompt":
            # The one thing a closed harness cannot show you, and the
            # reason for running your own. Everything the model was told
            # is here: the base prompt, the skills index, and whatever
            # AGENTS.md added without announcing itself.
            system = agent.session.messages[0].content
            ui.info(system)
            ui.info(
                f"\n[{agent.context.usage([agent.session.messages[0]])} "
                f"tokens of {agent.context.max_tokens}, "
                f"{len(system)} characters]"
            )
            if argument in ("all", "full"):
                for message in agent.session.messages[1:]:
                    body = message.content or ""
                    for call in message.tool_calls or []:
                        body += f"\n  -> {call.name}({call.arguments})"
                    ui.info(f"\n--- {message.role} ---\n{body}")
        case "/tools":
            ui.info("Tools: " + ", ".join(agent.tools.names()))
        case "/mcp":
            ui.info(_format_mcp_status(clients, config))
        case "/sessions":
            sessions = list_sessions(Path.cwd())[:10]
            if not sessions:
                ui.info("No saved sessions.")
            else:
                ui.info("\n".join(f"  {p.stem}" for p in sessions))
        case "/model":
            if not argument:
                ui.info(f"Current model: {config.provider.model}")
            else:
                # Evict the old model so two large ones never share VRAM.
                await agent.provider.unload()
                await agent.provider.close()
                config.provider.model = argument
                # resolved_provider pins num_ctx, which is the single
                # source of truth for the window. Building from the raw
                # config here left the server allocating one size and the
                # agent compacting against another.
                agent.provider = create_provider(resolved_provider(config))
                # Skills can be declared for particular models, so the
                # index in the prompt is wrong the moment the model
                # changes under it.
                skills = agent.tool_context.extra.get(SKILL_STORE_KEY)
                if skills is not None:
                    agent.session.set_system(
                        build_system_prompt(
                            agent.tool_context.cwd, skills.index(argument)
                        )
                    )
                ui.info(f"Switched to {argument}.")
        case "/help":
            ui.info(help_text())
        case _:
            ui.info(f"Unknown command: {raw_command} (try /help)")
    return False


def _fmt_tokens(n: int) -> str:
    return f"{n // 1024}k" if n % 1024 == 0 else str(n)


async def _model_max_context(provider) -> int | None:
    """The model's maximum context window, asked of the server."""
    if not isinstance(provider, OllamaProvider):
        return None
    try:
        info = await asyncio.wait_for(provider.model_info(), timeout=5)
    except (TimeoutError, httpx.HTTPError):
        return None
    return OllamaProvider.context_length_from_info(info)


def _sandbox_label(agent: Agent) -> str:
    """What is actually in force, never what was asked for."""
    executor = agent.tool_context.executor
    return executor.describe() if executor is not None else "no sandbox"


def _banner_status(agent: Agent, config: Config, maximum: int | None) -> str:
    """One-line status for the banner.

    Shows the effective window rather than the model's maximum: with
    num_ctx below what the model supports, the maximum would be a lie.
    """
    effective = agent.context.max_tokens
    ctx = f"ctx {_fmt_tokens(effective)}"
    if maximum is not None and maximum > effective:
        ctx += f" of {_fmt_tokens(maximum)}"
    return (
        f"{config.provider.name}/{config.provider.model} · "
        f"{config.permissions.mode} · {_sandbox_label(agent)} · "
        f"{len(agent.tools)} tools · "
        f"web {describe_search(config.web)} · {ctx}"
    )


async def _run_repl(config: Config, args: argparse.Namespace) -> None:
    from quaso.ui.repl import ReplUI

    ui = ReplUI()
    agent = _build_agent(config, ui, persist=True)
    clients: list[MCPClient] = []
    if not args.no_mcp:
        clients = await _start_mcp(config, agent.tools, ui)
    resumed = _resume_into(agent.session, args, ui)

    ui.model = f"{config.provider.name}/{config.provider.model}"
    maximum = await _model_max_context(agent.provider)
    if maximum is not None and agent.context.max_tokens > maximum:
        ui.info(
            f"warning: asking for {_fmt_tokens(agent.context.max_tokens)} "
            f"of context but {config.provider.model} supports "
            f"{_fmt_tokens(maximum)}; the server will clamp it."
        )
    if not args.no_banner:
        banner.render(
            ui.console,
            version=__version__,
            status=_banner_status(agent, config, maximum),
            compact=resumed,
        )
    try:
        while True:
            ui.context_fraction = agent.context_fraction()
            try:
                text = await ui.get_input()
            except KeyboardInterrupt:
                continue
            except EOFError:
                break
            text = text.strip()
            if not text:
                continue
            if text.startswith("/"):
                if await _handle_command(text, agent, ui, config, clients):
                    break
                continue
            await _run_turn(agent, ui, text)
    finally:
        for client in clients:
            await client.stop()
        await agent.provider.close()


async def _run_headless(
    config: Config, args: argparse.Namespace, prompt: str
) -> int:
    ui = HeadlessUI()
    agent = _build_agent(config, ui, persist=False)
    clients: list[MCPClient] = []
    if not args.no_mcp:
        clients = await _start_mcp(config, agent.tools, ui)
    try:
        ok = await _consume(agent, ui, prompt)
    finally:
        for client in clients:
            await client.stop()
        await agent.provider.close()
    print()
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "mcp":
        return asyncio.run(_mcp_command(raw[1:]))

    args = _parse_args(raw)
    config = _apply_overrides(load_config(), args)

    if not config.provider.model:
        interactive = sys.stdin.isatty() and not args.prompt
        if interactive and needs_setup(config):
            config = asyncio.run(Wizard().run())
            if config is None:
                return 1
            config = _apply_overrides(config, args)
        else:
            print(
                "No model configured. Pass --model, or run quaso "
                "interactively once to set it up.",
                file=sys.stderr,
            )
            return 2

    if args.prompt:
        return asyncio.run(_run_headless(config, args, args.prompt))
    asyncio.run(_run_repl(config, args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
