"""First-run setup and the slash-command registry."""

from __future__ import annotations

import io
import tomllib

import httpx
import pytest
import respx

from quaso.config import Config, ProviderConfig, load_config
from quaso.providers.ollama import list_models, supports_tools
from quaso.setup import needs_setup, write_config
from quaso.ui.commands import ALIASES, COMMANDS, canonical, help_text
from quaso.ui.repl import SlashCompleter

BASE = "http://testserver:11434"

MODELS = {
    "models": [
        {
            "name": "qwen3.6:latest",
            "modified_at": "2026-08-02T12:00:00Z",
            "size": 23_938_333_577,
            "details": {"parameter_size": "36.0B"},
            "capabilities": ["completion", "tools", "thinking"],
        },
        {
            "name": "gemma4:31b",
            "modified_at": "2026-04-04T13:00:00Z",
            "size": 19_868_981_791,
            "details": {"parameter_size": "31.3B"},
            "capabilities": ["completion", "tools"],
        },
        {
            "name": "olmocr2:7b",
            "modified_at": "2026-01-01T00:00:00Z",
            "size": 9_452_688_206,
            "details": {"parameter_size": "7.6B"},
            "capabilities": ["completion", "vision"],
        },
    ]
}


# Listing models


@pytest.mark.asyncio
@respx.mock
async def test_list_models_newest_first():
    respx.get(f"{BASE}/api/tags").mock(
        return_value=httpx.Response(200, json=MODELS)
    )
    models = await list_models(BASE)
    assert [m['name'] for m in models] == [
        "qwen3.6:latest",
        "gemma4:31b",
        "olmocr2:7b",
    ]


@pytest.mark.asyncio
@respx.mock
async def test_unreachable_server_raises_for_the_caller_to_explain():
    respx.get(f"{BASE}/api/tags").mock(side_effect=httpx.ConnectError("no"))
    with pytest.raises(httpx.HTTPError):
        await list_models(BASE)


def test_tool_capability_is_what_separates_usable_models():
    usable = [m for m in MODELS['models'] if supports_tools(m)]
    assert [m['name'] for m in usable] == ["qwen3.6:latest", "gemma4:31b"]


# When setup should run


def test_setup_needed_when_nothing_is_configured(tmp_path):
    assert needs_setup(Config(), cwd=tmp_path) is True


def test_setup_skipped_once_a_model_is_known(tmp_path):
    config = Config(provider=ProviderConfig(model="qwen3.6:latest"))
    assert needs_setup(config, cwd=tmp_path) is False


def test_setup_skipped_when_a_config_exists_but_lacks_a_model(tmp_path):
    """A hand-written config is the user's business; do not overwrite it."""
    project = tmp_path / ".quaso"
    project.mkdir()
    (project / "config.toml").write_text('[provider]\nname = "ollama"\n')
    assert needs_setup(Config(), cwd=tmp_path) is False


# Writing the config


def test_written_config_is_valid_and_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "quaso.config.USER_CONFIG_PATH", tmp_path / "absent.toml"
    )
    path = write_config(
        "http://box:11434", "qwen3.6:latest", thinking=True, cwd=tmp_path
    )
    assert path.is_file()

    parsed = tomllib.loads(path.read_text())
    assert parsed['provider']['base_url'] == "http://box:11434"
    assert parsed['provider']['options']['think'] is True

    config = load_config(cwd=tmp_path)
    assert config.provider.model == "qwen3.6:latest"
    assert config.provider.base_url == "http://box:11434"
    # An explicit window, so the server is never left to pick the maximum.
    assert config.provider.options['num_ctx'] > 0
    assert needs_setup(config, cwd=tmp_path) is False


def test_thinking_is_omitted_for_models_that_lack_it(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "quaso.config.USER_CONFIG_PATH", tmp_path / "absent.toml"
    )
    write_config("http://x:11434", "gemma4:31b", thinking=False, cwd=tmp_path)
    parsed = tomllib.loads((tmp_path / ".quaso" / "config.toml").read_text())
    assert "think" not in parsed['provider']['options']


# Slash commands


def test_help_is_generated_from_the_registry():
    text = help_text()
    for name, description in COMMANDS.items():
        assert name in text
        assert description in text


def test_aliases_resolve_and_are_not_offered():
    assert canonical("/q") == "/quit"
    assert canonical("/exit") == "/quit"
    assert canonical("/new") == "/new"
    for alias in ALIASES:
        assert alias not in COMMANDS


def _complete(text: str) -> list[str]:
    from prompt_toolkit.document import Document

    completer = SlashCompleter()
    return [
        c.text
        for c in completer.get_completions(Document(text, len(text)), None)
    ]


def test_slash_offers_every_command():
    assert _complete("/") == list(COMMANDS)


def test_prefix_narrows_the_list():
    assert _complete("/co") == ["/compact", "/context"]
    assert _complete("/ne") == ["/new"]


def test_nothing_offered_for_ordinary_text():
    assert _complete("what does this repo do") == []


def test_nothing_offered_once_an_argument_is_being_typed():
    """After '/model ' the user has chosen; suggestions would be noise."""
    assert _complete("/model ") == []
    assert _complete("/model qwen3.6") == []


def test_unknown_prefix_offers_nothing():
    assert _complete("/zzz") == []


class _Answers:
    """Stands in for the prompt, replying from a script."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.asked: list[str] = []

    async def prompt_async(self, prompt: str) -> str:
        self.asked.append(prompt)
        return self.answers.pop(0) if self.answers else ""


def _wizard(*answers: str):
    from rich.console import Console

    from quaso.setup import Wizard

    session = _Answers(*answers)
    wizard = Wizard(
        console=Console(file=io.StringIO(), force_terminal=False),
        session=session,
    )
    return wizard, session


QWEN = {
    "name": "qwen3.6:latest",
    "capabilities": ["tools", "thinking"],
    "details": {"parameter_size": "36B"},
}
NO_TOOLS = {"name": "embed:latest", "capabilities": [], "details": {}}


class TestChoosingAModel:
    @pytest.mark.asyncio
    async def test_a_number_picks_from_the_list(self):
        wizard, _ = _wizard("1")
        assert await wizard._choose_model([QWEN]) == "qwen3.6:latest"

    @pytest.mark.asyncio
    async def test_a_name_is_accepted_too(self):
        wizard, _ = _wizard("qwen3.6:latest")
        assert await wizard._choose_model([QWEN]) == "qwen3.6:latest"

    @pytest.mark.asyncio
    async def test_models_without_tool_support_are_not_offered(self):
        """quaso cannot work with them, so listing them wastes a choice."""
        wizard, _ = _wizard("1")
        assert await wizard._choose_model([NO_TOOLS, QWEN]) == QWEN['name']

    @pytest.mark.asyncio
    async def test_no_usable_model_says_what_to_pull(self):
        output = io.StringIO()
        from rich.console import Console

        from quaso.setup import Wizard

        wizard = Wizard(
            console=Console(file=output, force_terminal=False),
            session=_Answers(),
        )
        assert await wizard._choose_model([NO_TOOLS]) is None
        assert "ollama pull" in output.getvalue()

    @pytest.mark.asyncio
    async def test_a_bad_answer_asks_again(self):
        wizard, session = _wizard("99", "banana", "1")
        assert await wizard._choose_model([QWEN]) == "qwen3.6:latest"
        assert len(session.asked) == 3


class TestChoosingAServer:
    @pytest.mark.asyncio
    async def test_the_default_is_this_machine(self):
        from quaso.setup import LOCAL_URL

        wizard, _ = _wizard("")
        assert await wizard._choose_server() == LOCAL_URL

    @pytest.mark.asyncio
    async def test_a_bare_host_gets_a_scheme_and_port(self):
        wizard, _ = _wizard("2", "box.local")
        assert await wizard._choose_server() == "http://box.local:11434"

    @pytest.mark.asyncio
    async def test_a_full_url_is_left_alone(self):
        wizard, _ = _wizard("2", "http://box.local:9999")
        assert await wizard._choose_server() == "http://box.local:9999"

    @pytest.mark.asyncio
    async def test_giving_up_returns_nothing(self):
        wizard, _ = _wizard("2", "")
        assert await wizard._choose_server() is None


class TestWritingTheConfig:
    def test_the_written_config_loads_back(self, tmp_path):
        from quaso.config import load_config

        write_config("http://box:11434", "qwen3.6:latest", True, tmp_path)
        config = load_config(tmp_path)
        assert config.provider.model == "qwen3.6:latest"
        assert config.provider.base_url == "http://box:11434"
        assert config.provider.options['think'] is True

    def test_thinking_is_omitted_when_unsupported(self, tmp_path):
        from quaso.config import load_config

        write_config("http://box:11434", "gemma4:31b", False, tmp_path)
        assert 'think' not in load_config(tmp_path).provider.options

    def test_it_pins_the_context_window(self, tmp_path):
        """num_ctx in the written file is what stops Ollama allocating
        the model's maximum."""
        from quaso.config import load_config
        from quaso.setup import DEFAULT_CONTEXT_TOKENS

        write_config("http://box:11434", "qwen3.6:latest", True, tmp_path)
        options = load_config(tmp_path).provider.options
        assert options['num_ctx'] == DEFAULT_CONTEXT_TOKENS


class TestSizeLabel:
    def test_parameter_size_is_preferred(self):
        from quaso.setup import _size

        assert _size(QWEN) == "36B"

    def test_bytes_are_used_when_that_is_all_there_is(self):
        from quaso.setup import _size

        assert _size({"name": "x", "size": 24_000_000_000}) == "24 GB"

    def test_nothing_known_says_nothing(self):
        from quaso.setup import _size

        assert _size({"name": "x"}) == ""
