"""The wiring: resuming, switching model, and what an exit code means.

This module holds the parts nothing else exercises. Three of the bugs
found in review lived here, which is what nought percent coverage of the
second-largest module buys.
"""

from __future__ import annotations

import argparse
import time

import pytest

from quaso.cli import (
    _apply_overrides,
    _build_agent,
    _consume,
    _fmt_tokens,
    _format_mcp_status,
    _handle_command,
    _make_subagent,
    _parse_args,
    _resume_into,
    _run_headless,
    _start_mcp,
    main,
)
from quaso.config import Config, MCPServerConfig
from quaso.events import ErrorEvent, TurnEnd
from quaso.messages import Message, user
from quaso.session import Session
from quaso.tools.registry import ToolRegistry
from quaso.ui.headless import HeadlessUI

from .conftest import FakeProvider


class Unreachable(FakeProvider):
    """What OllamaProvider does when the server cannot be reached."""

    async def stream(self, messages, tools=None):
        yield ErrorEvent("Ollama connection error: refused")


def _args(**kwargs) -> argparse.Namespace:
    base = {"resume": None, "continue_": False, "no_mcp": True}
    return argparse.Namespace(**{**base, **kwargs})


class TestResume:
    def test_continue_finds_the_previous_conversation(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        """The session built for this run is the newest transcript on
        disk, so picking the newest picks the empty one."""
        earlier = Session("system", root=tmp_path, persist=True)
        earlier.append(user("the conversation I want back"))
        time.sleep(1.1)

        current = Session("system", root=tmp_path, persist=True)
        assert _resume_into(current, _args(continue_=True), HeadlessUI())
        contents = [m.content for m in current.messages]
        assert "the conversation I want back" in contents

    def test_continue_without_history_starts_new(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        current = Session("system", root=tmp_path, persist=True)
        assert not _resume_into(current, _args(continue_=True), HeadlessUI())
        assert len(current.messages) == 1

    def test_resume_by_name_still_works(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        earlier = Session("system", root=tmp_path, persist=True)
        earlier.append(user("named session"))
        name = earlier.transcript.stem
        time.sleep(1.1)

        current = Session("system", root=tmp_path, persist=True)
        assert _resume_into(current, _args(resume=name), HeadlessUI())
        assert "named session" in [m.content for m in current.messages]


class TestHeadlessExitCode:
    """`quaso -p` is run by scripts, which read the status, not the text."""

    @pytest.mark.asyncio
    async def test_a_clean_run_succeeds(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        provider = FakeProvider([Message(role="assistant", content="done")])
        monkeypatch.setattr("quaso.cli.create_provider", lambda cfg: provider)
        code = await _run_headless(Config(), _args(), "hello")
        assert code == 0

    @pytest.mark.asyncio
    async def test_a_failed_run_reports_failure(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        monkeypatch.setattr(
            "quaso.cli.create_provider", lambda cfg: Unreachable([])
        )
        code = await _run_headless(Config(), _args(), "hello")
        assert code != 0, "a failed turn must not report success"


class TestConsumeReportsFailure:
    @pytest.mark.asyncio
    async def test_an_error_event_is_reported(self, tmp_path, monkeypatch):
        from quaso.agent.loop import Agent
        from quaso.config import PermissionsConfig
        from quaso.permissions import PermissionPolicy
        from quaso.tools.base import ToolContext
        from quaso.tools.registry import ToolRegistry

        agent = Agent(
            provider=Unreachable([]),
            tools=ToolRegistry([]),
            permissions=PermissionPolicy(PermissionsConfig(), None),
            session=Session("s", root=tmp_path, persist=False),
            tool_context=ToolContext(cwd=tmp_path),
        )
        assert await _consume(agent, HeadlessUI(), "hi") is False


class TestModelSwitch:
    @pytest.mark.asyncio
    async def test_switching_keeps_the_context_window_pinned(
        self, tmp_path, monkeypatch
    ):
        """num_ctx is the single source of truth for the window; a switch
        that drops it leaves the server and the agent disagreeing."""
        from quaso.cli import _handle_command

        monkeypatch.chdir(tmp_path)
        config = Config()
        config.context.max_tokens = 16384
        seen: list[dict] = []

        def fake_create(provider_config):
            seen.append(dict(provider_config.options))
            return FakeProvider([])

        monkeypatch.setattr("quaso.cli.create_provider", fake_create)
        from quaso.cli import _build_agent

        agent = _build_agent(config, HeadlessUI(), persist=False)
        await _handle_command(
            "/model gemma4:31b", agent, HeadlessUI(), config, []
        )
        assert seen[0].get("num_ctx") == 16384, "startup pins the window"
        assert seen[-1].get("num_ctx") == 16384, "the switch must too"


class TestArguments:
    def test_flags_reach_the_config(self):
        args = _parse_args(
            [
                "--model",
                "gemma4:31b",
                "--url",
                "http://box:11434",
                "--mode",
                "readonly",
                "--provider",
                "ollama",
            ]
        )
        config = _apply_overrides(Config(), args)
        assert config.provider.model == "gemma4:31b"
        assert config.provider.base_url == "http://box:11434"
        assert config.permissions.mode == "readonly"

    def test_thinking_is_three_valued(self):
        """Unset must mean "leave the config alone", not "off"."""
        assert _parse_args([]).think is None
        assert _parse_args(["--think"]).think is True
        assert _parse_args(["--no-think"]).think is False

        config = Config()
        config.provider.options['think'] = True
        _apply_overrides(config, _parse_args([]))
        assert config.provider.options['think'] is True
        _apply_overrides(config, _parse_args(["--no-think"]))
        assert config.provider.options['think'] is False

    def test_continue_and_resume_are_distinct(self):
        assert _parse_args(["-c"]).continue_ is True
        assert _parse_args(["--resume", "abc"]).resume == "abc"

    def test_think_and_no_think_together_is_refused(self):
        with pytest.raises(SystemExit):
            _parse_args(["--think", "--no-think"])

    def test_an_unknown_mode_is_refused(self):
        with pytest.raises(SystemExit):
            _parse_args(["--mode", "whatever"])


class TestCommands:
    """Slash commands are the whole interactive surface and had no tests."""

    async def _run(self, text, agent, ui, config=None, clients=None):
        return await _handle_command(
            text, agent, ui, config or Config(), clients or []
        )

    @pytest.fixture
    def agent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "quaso.cli.create_provider", lambda cfg: FakeProvider([])
        )
        return _build_agent(Config(), HeadlessUI(), persist=False)

    @pytest.mark.asyncio
    async def test_quit_ends_the_session(self, agent):
        assert await self._run("/quit", agent, HeadlessUI()) is True

    @pytest.mark.asyncio
    async def test_other_commands_do_not(self, agent):
        assert await self._run("/tools", agent, HeadlessUI()) is False

    @pytest.mark.asyncio
    async def test_new_clears_the_conversation(self, agent):
        agent.session.append(user("something"))
        await self._run("/new", agent, HeadlessUI())
        assert len(agent.session.messages) == 1

    @pytest.mark.asyncio
    async def test_an_unknown_command_says_so(self, agent):
        said = []
        ui = HeadlessUI()
        ui.info = said.append
        await self._run("/nonsense", agent, ui)
        assert "Unknown command" in said[0]

    @pytest.mark.asyncio
    async def test_aliases_resolve(self, agent):
        """/q and /exit are accepted without being offered."""
        assert await self._run("/q", agent, HeadlessUI()) is True
        assert await self._run("/exit", agent, HeadlessUI()) is True

    def test_every_listed_command_is_handled(self):
        """The dispatcher and the list are edited separately, so a new
        command can appear in /help and fall through to "unknown"."""
        import inspect

        from quaso.cli import _handle_command
        from quaso.ui.commands import ALIASES, COMMANDS

        source = inspect.getsource(_handle_command)
        for name in [*COMMANDS, *ALIASES]:
            canonical_name = ALIASES.get(name, name)
            assert f'case "{canonical_name}"' in source, name

    @pytest.mark.asyncio
    async def test_model_without_an_argument_reports(self, agent):
        said = []
        ui = HeadlessUI()
        ui.info = said.append
        await self._run("/model", agent, ui)
        assert "Current model" in said[0]

    @pytest.mark.asyncio
    async def test_tools_lists_what_is_registered(self, agent):
        said = []
        ui = HeadlessUI()
        ui.info = said.append
        await self._run("/tools", agent, ui)
        assert "read_file" in said[0] and "ask" in said[0]

    @pytest.mark.asyncio
    async def test_context_reports_usage(self, agent):
        said = []
        ui = HeadlessUI()
        ui.info = said.append
        await self._run("/context", agent, ui)
        assert "tokens" in said[0] and "messages" in said[0]


class TestMcpStatus:
    def test_no_servers_says_where_to_add_them(self):
        assert "config.toml" in _format_mcp_status([], Config())

    def test_a_configured_server_that_did_not_start_is_shown(self):
        config = Config()
        config.mcp.servers['files'] = MCPServerConfig(command="nope")
        status = _format_mcp_status([], config)
        assert "files" in status and "not connected" in status

    def test_a_disabled_server_says_disabled(self):
        config = Config()
        config.mcp.servers['files'] = MCPServerConfig(
            command="nope", enabled=False
        )
        assert "disabled" in _format_mcp_status([], config)


class TestMcpStartup:
    @pytest.mark.asyncio
    async def test_a_server_that_fails_does_not_stop_the_session(
        self, tmp_path, monkeypatch
    ):
        """One broken server must not take the others, or quaso, down."""
        config = Config()
        config.mcp.servers['broken'] = MCPServerConfig(command="false")
        said = []
        ui = HeadlessUI()
        ui.info = said.append
        clients = await _start_mcp(config, ToolRegistry([]), ui)
        assert clients == []
        assert "failed to start" in said[0]

    @pytest.mark.asyncio
    async def test_a_disabled_server_is_not_started(self, tmp_path):
        config = Config()
        config.mcp.servers['off'] = MCPServerConfig(
            command="false", enabled=False
        )
        assert await _start_mcp(config, ToolRegistry([]), HeadlessUI()) == []


class TestSubagents:
    """A subagent has no user in front of it and a context of its own."""

    def _parent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "quaso.cli.create_provider", lambda cfg: FakeProvider([])
        )
        return _build_agent(Config(), HeadlessUI(), persist=False)

    @pytest.mark.asyncio
    async def test_it_cannot_delegate_or_ask(self, tmp_path, monkeypatch):
        """Its prompt says it cannot ask, so the tool is withheld to
        match, and nesting subagents has no user to end the wait."""
        parent = self._parent(tmp_path, monkeypatch)
        captured = {}

        async def fake_run(self, prompt):
            captured['names'] = set(self.tools.names())
            return
            yield  # pragma: no cover

        monkeypatch.setattr("quaso.agent.loop.Agent.run", fake_run)
        spawn = _make_subagent(parent, Config(), tmp_path)
        await spawn("go")
        assert "task" not in captured['names']
        assert "ask" not in captured['names']
        assert "read_file" in captured['names']

    @pytest.mark.asyncio
    async def test_it_gets_a_context_of_its_own(self, tmp_path, monkeypatch):
        parent = self._parent(tmp_path, monkeypatch)
        parent.session.append(user("the parent's conversation"))
        captured = {}

        async def fake_run(self, prompt):
            captured['messages'] = list(self.session.messages)
            return
            yield  # pragma: no cover

        monkeypatch.setattr("quaso.agent.loop.Agent.run", fake_run)
        await _make_subagent(parent, Config(), tmp_path)("go")
        contents = [m.content for m in captured['messages']]
        assert "the parent's conversation" not in contents

    @pytest.mark.asyncio
    async def test_no_output_is_reported_rather_than_empty(
        self, tmp_path, monkeypatch
    ):
        parent = self._parent(tmp_path, monkeypatch)

        async def fake_run(self, prompt):
            return
            yield  # pragma: no cover

        monkeypatch.setattr("quaso.agent.loop.Agent.run", fake_run)
        result = await _make_subagent(parent, Config(), tmp_path)("go")
        assert "no output" in result


class TestMain:
    def test_no_model_and_no_terminal_explains_itself(
        self, tmp_path, monkeypatch, capsys
    ):
        """Piped into a script with nothing configured, it must say what
        to do rather than open a wizard nobody can answer."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("quaso.cli.load_config", lambda *a, **k: Config())
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        assert main(["-p", "hello"]) == 2
        assert "No model configured" in capsys.readouterr().err

    def test_version_exits_cleanly(self):
        with pytest.raises(SystemExit) as exit_info:
            main(["--version"])
        assert exit_info.value.code == 0

    def test_an_unknown_mcp_subcommand_is_refused(self, capsys):
        assert main(["mcp", "wat"]) == 2
        assert "Unknown mcp subcommand" in capsys.readouterr().err


class TestTokenFormatting:
    def test_round_numbers_are_shortened(self):
        assert _fmt_tokens(32768) == "32k"
        assert _fmt_tokens(1024) == "1k"

    def test_anything_else_is_left_alone(self):
        assert _fmt_tokens(1500) == "1500"


class TestInterruptedTurn:
    """Ctrl-C ends the turn, not the session, and the history it leaves
    behind has to be one a provider will still accept."""

    @pytest.mark.asyncio
    async def test_an_interrupt_leaves_no_unanswered_tool_call(
        self, tmp_path, monkeypatch
    ):
        import asyncio as _asyncio

        from quaso.agent.loop import Agent
        from quaso.cli import _run_turn
        from quaso.config import PermissionsConfig
        from quaso.messages import Message, ToolCall
        from quaso.permissions import PermissionPolicy
        from quaso.tools.base import ToolContext

        class Hanging(FakeProvider):
            async def stream(self, messages, tools=None):
                yield TurnEnd(
                    message=Message(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(id="1", name="bash", arguments={})
                        ],
                    )
                )

        agent = Agent(
            provider=Hanging([]),
            tools=ToolRegistry.default(Config()),
            permissions=PermissionPolicy(PermissionsConfig(mode="yolo"), None),
            session=Session("s", root=tmp_path, persist=False),
            tool_context=ToolContext(cwd=tmp_path),
        )

        async def cancel_soon(task):
            await _asyncio.sleep(0.05)
            task.cancel()

        # Stand in for the signal handler, which needs a real terminal.
        original = _asyncio.create_task

        def spy(coro, **kwargs):
            task = original(coro, **kwargs)
            original(cancel_soon(task))
            return task

        monkeypatch.setattr(_asyncio, "create_task", spy)
        await _run_turn(agent, HeadlessUI(), "run something slow")

        calls = [m for m in agent.session.messages if m.tool_calls]
        answered = {
            m.tool_call_id for m in agent.session.messages if m.role == "tool"
        }
        for message in calls:
            for call in message.tool_calls:
                assert call.id in answered, "a call was left unanswered"


class TestContextWindowCheck:
    @pytest.mark.asyncio
    async def test_a_non_ollama_provider_is_not_asked(self):
        """Only Ollama exposes a maximum; a plugin owns its own window."""
        from quaso.cli import _model_max_context

        assert await _model_max_context(FakeProvider([])) is None

    @pytest.mark.asyncio
    async def test_a_server_that_does_not_answer_is_not_fatal(
        self, monkeypatch
    ):
        from quaso.cli import _model_max_context
        from quaso.providers.ollama import OllamaProvider

        provider = OllamaProvider(base_url="http://nowhere:11434", model="m")

        async def never(self):
            raise TimeoutError

        monkeypatch.setattr(OllamaProvider, "model_info", never)
        assert await _model_max_context(provider) is None
        await provider.close()


class TestCompactCommand:
    @pytest.mark.asyncio
    async def test_nothing_to_compact_says_so(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "quaso.cli.create_provider", lambda cfg: FakeProvider([])
        )
        agent = _build_agent(Config(), HeadlessUI(), persist=False)
        said = []
        ui = HeadlessUI()
        ui.info = said.append
        await _handle_command("/compact", agent, ui, Config(), [])
        assert "Nothing old enough" in said[0]
