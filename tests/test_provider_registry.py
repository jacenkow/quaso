"""Provider lookup, including plugins that do not import.

Third-party providers arrive as entry points, so an installed one is
code quaso did not write and cannot vouch for. The tool registry already
treats a broken plugin as one fewer tool; providers should not be the
odd one out and take the session with them.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from quaso.config import ProviderConfig
from quaso.providers.base import Provider
from quaso.providers.ollama import OllamaProvider
from quaso.providers.registry import available_providers, create_provider


class _Entry:
    def __init__(self, name: str, loader) -> None:
        self.name = name
        self._loader = loader

    def load(self):
        return self._loader()


def _broken(message: str = "no module named 'their_dependency'"):
    def load():
        raise ImportError(message)

    return load


class _Working(Provider):
    name = "working"

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def stream(self, messages, tools=None):
        return
        yield  # pragma: no cover


def _entries(*entries):
    return patch(
        "quaso.providers.registry.entry_points", return_value=list(entries)
    )


class TestBuiltins:
    def test_ollama_is_always_there(self):
        assert available_providers()['ollama'] is OllamaProvider

    def test_it_is_built_with_the_config(self):
        provider = create_provider(
            ProviderConfig(name="ollama", model="qwen3.6:latest")
        )
        assert isinstance(provider, OllamaProvider)
        assert provider.model == "qwen3.6:latest"


class TestBrokenPlugins:
    def test_one_does_not_stop_the_builtins(self):
        """The whole point: a plugin you are not using must not matter."""
        with _entries(_Entry("theirs", _broken())):
            provider = create_provider(
                ProviderConfig(name="ollama", model="qwen3.6:latest")
            )
        assert isinstance(provider, OllamaProvider)

    def test_one_does_not_hide_the_working_ones(self):
        with _entries(
            _Entry("theirs", _broken()), _Entry("working", lambda: _Working)
        ):
            names = set(available_providers())
        assert {"ollama", "working"} <= names
        assert "theirs" not in names

    def test_asking_for_the_broken_one_says_why(self):
        """Silently reporting it unknown would send someone hunting for a
        typo in a name they spelled correctly."""
        with (
            _entries(_Entry("theirs", _broken("bad dependency"))),
            pytest.raises(ValueError, match="bad dependency"),
        ):
            create_provider(ProviderConfig(name="theirs", model="m"))

    def test_an_unknown_name_still_lists_what_there_is(self):
        with _entries(), pytest.raises(ValueError, match="ollama"):
            create_provider(ProviderConfig(name="nope", model="m"))


class TestWorkingPlugins:
    def test_a_plugin_can_be_selected(self):
        with _entries(_Entry("working", lambda: _Working)):
            provider = create_provider(
                ProviderConfig(name="working", model="m")
            )
        assert isinstance(provider, _Working)

    def test_a_plugin_may_not_replace_a_builtin(self):
        """Otherwise installing a package silently changes what ollama
        means in a config that has always worked."""
        with _entries(_Entry("ollama", lambda: _Working)):
            assert available_providers()['ollama'] is OllamaProvider
