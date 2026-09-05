"""Leaving the machine is a boundary, like leaving the directory.

The reason for running a local model is that what you paste stays with
you. That holds until the model decides to look something up, at which
point anything it has read can leave inside a query string. Nothing
about a search is a mutation, so the permission layer waved it through.
"""

from __future__ import annotations

import pytest

from quaso.config import PermissionsConfig, WebConfig
from quaso.permissions import PermissionPolicy
from quaso.tools.base import ToolContext
from quaso.tools.fs import ReadFile, ReadFileParams
from quaso.tools.web import (
    FetchUrl,
    FetchUrlParams,
    WebSearch,
    WebSearchParams,
)

LEAK = "grzegorz +48123456789 medical records"


def _asker(answer="deny"):
    async def ask(request):
        ask.calls.append(request)
        return answer

    ask.calls = []
    return ask


def _policy(asker, **config):
    return PermissionPolicy(PermissionsConfig(**config), asker)


def _search():
    return WebSearch(WebConfig()), WebSearchParams(query=LEAK)


def _fetch():
    return FetchUrl(WebConfig()), FetchUrlParams(
        url="https://example.com/?q=" + LEAK.replace(" ", "+")
    )


class TestGoingOutAsks:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "make", [_search, _fetch], ids=["search", "fetch"]
    )
    async def test_it_asks_before_the_first_one(self, tmp_path, make):
        tool, params = make()
        ask = _asker()
        decision = await _policy(ask).check(
            tool, params, ToolContext(cwd=tmp_path)
        )
        assert not decision.allowed
        assert len(ask.calls) == 1

    @pytest.mark.asyncio
    async def test_the_request_says_what_is_leaving(self, tmp_path):
        tool, params = _search()
        ask = _asker()
        await _policy(ask).check(tool, params, ToolContext(cwd=tmp_path))
        request = ask.calls[0]
        assert request.leaves_machine is True
        assert LEAK in request.primary_arg

    @pytest.mark.asyncio
    async def test_yolo_does_not_excuse_it(self, tmp_path):
        """yolo means stop asking about my project. This is not that."""
        tool, params = _search()
        ask = _asker()
        decision = await _policy(ask, mode="yolo").check(
            tool, params, ToolContext(cwd=tmp_path)
        )
        assert not decision.allowed
        assert ask.calls


class TestItStaysUsable:
    @pytest.mark.asyncio
    async def test_always_holds_for_the_session(self, tmp_path):
        """Asking before every search would teach people to say yes
        without reading, which is worse than not asking."""
        tool, params = _search()
        ask = _asker("always")
        policy = _policy(ask)
        ctx = ToolContext(cwd=tmp_path)

        assert (await policy.check(tool, params, ctx)).allowed
        assert (await policy.check(tool, params, ctx)).allowed
        assert len(ask.calls) == 1

    @pytest.mark.asyncio
    async def test_a_config_rule_settles_it(self, tmp_path):
        """For anyone who does not want the question at all."""
        tool, params = _search()
        ask = _asker()
        decision = await _policy(ask, allow=["web_search"]).check(
            tool, params, ToolContext(cwd=tmp_path)
        )
        assert decision.allowed
        assert not ask.calls

    @pytest.mark.asyncio
    async def test_reading_a_local_file_is_untouched(self, tmp_path):
        """The boundary is the network, not tools in general."""
        (tmp_path / "a.py").write_text("x")
        ask = _asker()
        decision = await _policy(ask).check(
            ReadFile(),
            ReadFileParams(path="a.py"),
            ToolContext(cwd=tmp_path),
        )
        assert decision.allowed
        assert not ask.calls
