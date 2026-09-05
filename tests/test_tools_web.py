from __future__ import annotations

import socket

import httpx
import pytest
import respx

from quaso.config import WebConfig
from quaso.tools.base import ToolContext, ToolError
from quaso.tools.web import (
    KEYLESS_CHAIN,
    MAX_RESPONSE_BYTES,
    UNTRUSTED_BANNER,
    FetchUrl,
    FetchUrlParams,
    WebSearch,
    WebSearchParams,
    html_to_text,
    resolve_backend,
    search_chain,
)


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    real_getaddrinfo = socket.getaddrinfo

    def resolve(host, *args, **kwargs):
        if host == "example.com":
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("93.184.216.34", 0),
                )
            ]
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr("quaso.tools.web.socket.getaddrinfo", resolve)
    return ToolContext(cwd=tmp_path)


def test_backend_auto_selection():
    assert resolve_backend(WebConfig()) == "duckduckgo"
    assert resolve_backend(WebConfig(searxng_url="http://x:8080")) == "searxng"
    assert (
        resolve_backend(WebConfig(backend="brave", searxng_url="http://x"))
        == "brave"
    )


def test_default_chain_is_keyless_and_needs_no_hosting():
    chain = search_chain(WebConfig())
    assert chain == KEYLESS_CHAIN
    # Nothing in the default path requires a key or a self-hosted service.
    assert (
        "searxng" not in chain
        and "tavily" not in chain
        and "brave" not in chain
    )


def test_explicit_backend_disables_fallback():
    assert search_chain(WebConfig(backend="wikipedia")) == ["wikipedia"]


def test_captcha_serving_engines_are_not_in_the_chain():
    """Mojeek serves a CAPTCHA; we do not work around bot protection."""
    assert "mojeek" not in KEYLESS_CHAIN


def test_configured_services_lead_the_chain_but_keep_fallbacks():
    assert search_chain(WebConfig(searxng_url="http://x")) == [
        "searxng",
        *KEYLESS_CHAIN,
    ]
    assert search_chain(WebConfig(api_key="k")) == ["tavily", *KEYLESS_CHAIN]


def test_html_to_text_strips_scripts_and_nav():
    html = """
    <html><head><title>My Page</title><script>var evil = 1;</script></head>
    <body><nav>skip me</nav><p>First para.</p><p>Second para.</p>
    <style>.x{}</style></body></html>
    """
    title, text = html_to_text(html)
    assert title == "My Page"
    assert "First para." in text and "Second para." in text
    assert "evil" not in text and "skip me" not in text


@pytest.mark.asyncio
@respx.mock
async def test_searxng_search(ctx):
    respx.get("http://searx.local/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Result One",
                        "url": "https://a.example",
                        "content": "snippet one",
                    },
                    {
                        "title": "Result Two",
                        "url": "https://b.example",
                        "content": "snippet two",
                    },
                ]
            },
        )
    )
    tool = WebSearch(
        WebConfig(backend="searxng", searxng_url="http://searx.local")
    )
    out = await tool.run(WebSearchParams(query="python"), ctx)
    assert (
        "Result One" in out and "https://b.example" in out and "searxng" in out
    )


@pytest.mark.asyncio
@respx.mock
async def test_searxng_json_disabled_gives_actionable_error(ctx):
    respx.get("http://searx.local/search").mock(
        return_value=httpx.Response(403)
    )
    tool = WebSearch(
        WebConfig(backend="searxng", searxng_url="http://searx.local")
    )
    with pytest.raises(ToolError, match="settings.yml"):
        await tool.run(WebSearchParams(query="x"), ctx)


@pytest.mark.asyncio
@respx.mock
async def test_duckduckgo_search_parses_html(ctx):
    html = """
    <div class="result results_links">
      <a rel="nofollow" class="result__a"
         href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdoc"
      >Example Doc</a>
      <a class="result__snippet">A useful snippet.</a>
    </div>
    """
    respx.post("https://html.duckduckgo.com/html/").mock(
        return_value=httpx.Response(200, text=html)
    )
    tool = WebSearch(WebConfig(backend="duckduckgo"))
    out = await tool.run(WebSearchParams(query="example"), ctx)
    assert "Example Doc" in out
    assert "https://example.com/doc" in out  # redirect wrapper unwrapped
    assert "A useful snippet." in out


@pytest.mark.asyncio
@respx.mock
async def test_ddg_lite_parses_table_markup(ctx):
    html = """
    <table><tr><td>
      <a class="result-link" href="https://lite.example/doc">Lite Doc</a>
    </td></tr>
    <tr><td class="result-snippet">Snippet from lite.</td></tr></table>
    """
    respx.post("https://lite.duckduckgo.com/lite/").mock(
        return_value=httpx.Response(200, text=html)
    )
    tool = WebSearch(WebConfig(backend="ddg_lite"))
    out = await tool.run(WebSearchParams(query="x"), ctx)
    assert "Lite Doc" in out and "https://lite.example/doc" in out
    assert "Snippet from lite." in out


@pytest.mark.asyncio
@respx.mock
async def test_stackoverflow_backend(ctx):
    respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "title": "How do I use asyncio?",
                        "link": "https://stackoverflow.com/q/1",
                        "score": 42,
                        "answer_count": 3,
                        "is_answered": True,
                        "tags": ["python", "asyncio"],
                    }
                ]
            },
        )
    )
    tool = WebSearch(WebConfig(backend="stackoverflow"))
    out = await tool.run(WebSearchParams(query="asyncio"), ctx)
    assert "How do I use asyncio?" in out
    assert "score 42" in out and "accepted" in out and "python" in out


@pytest.mark.asyncio
@respx.mock
async def test_html_entities_in_api_results_are_unescaped(ctx):
    respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "title": "Python&#39;s GIL &amp; you",
                        "link": "https://stackoverflow.com/q/3",
                        "score": 1,
                        "answer_count": 0,
                        "tags": [],
                    }
                ]
            },
        )
    )
    tool = WebSearch(WebConfig(backend="stackoverflow"))
    out = await tool.run(WebSearchParams(query="gil"), ctx)
    assert "Python's GIL & you" in out
    assert "&#39;" not in out and "&amp;" not in out


@pytest.mark.asyncio
@respx.mock
async def test_hackernews_backend_handles_missing_url(ctx):
    respx.get("https://hn.algolia.com/api/v1/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "title": "Ask HN: Best editor?",
                        "url": None,
                        "objectID": "999",
                        "points": 10,
                        "num_comments": 5,
                    }
                ]
            },
        )
    )
    tool = WebSearch(WebConfig(backend="hackernews"))
    out = await tool.run(WebSearchParams(query="editor"), ctx)
    # Story with no external link falls back to its HN discussion page.
    assert "news.ycombinator.com/item?id=999" in out
    assert "10 points" in out


@pytest.mark.asyncio
@respx.mock
async def test_wikipedia_backend(ctx):
    respx.get("https://en.wikipedia.org/w/api.php").mock(
        return_value=httpx.Response(
            200,
            json={
                "query": {
                    "search": [
                        {
                            "title": "Python (programming language)",
                            "snippet": "A <span>high-level</span> language",
                        }
                    ]
                }
            },
        )
    )
    tool = WebSearch(WebConfig(backend="wikipedia"))
    out = await tool.run(WebSearchParams(query="python"), ctx)
    assert "Python_(programming_language)" in out
    assert "high-level" in out and "<span>" not in out  # markup stripped


@pytest.mark.asyncio
@respx.mock
async def test_chain_falls_back_when_first_engine_is_rate_limited(ctx):
    """A 202 from DuckDuckGo means bot-challenge; the chain must continue."""
    respx.post("https://html.duckduckgo.com/html/").mock(
        return_value=httpx.Response(202)
    )
    lite = """<table><tr><td>
      <a class="result-link" href="https://ok.example">Fallback Hit</a>
    </td></tr></table>"""
    respx.post("https://lite.duckduckgo.com/lite/").mock(
        return_value=httpx.Response(200, text=lite)
    )
    tool = WebSearch(WebConfig())  # auto → full keyless chain
    out = await tool.run(WebSearchParams(query="x"), ctx)
    assert "Fallback Hit" in out
    assert "via ddg_lite" in out


@pytest.mark.asyncio
@respx.mock
async def test_chain_falls_back_when_markup_yields_nothing(ctx):
    """A layout change is treated as the engine being unavailable."""
    respx.post("https://html.duckduckgo.com/html/").mock(
        return_value=httpx.Response(
            200, text="<html><body>redesigned</body></html>"
        )
    )
    respx.post("https://lite.duckduckgo.com/lite/").mock(
        return_value=httpx.Response(200, text="<html>also redesigned</html>")
    )
    respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "title": "SO Saved Us",
                        "link": "https://stackoverflow.com/q/2",
                        "score": 1,
                        "answer_count": 1,
                        "tags": [],
                    }
                ]
            },
        )
    )
    tool = WebSearch(WebConfig())
    out = await tool.run(WebSearchParams(query="x"), ctx)
    assert "SO Saved Us" in out and "via stackoverflow" in out


@pytest.mark.asyncio
@respx.mock
async def test_all_engines_failing_reports_every_reason(ctx):
    respx.post("https://html.duckduckgo.com/html/").mock(
        return_value=httpx.Response(429)
    )
    respx.post("https://lite.duckduckgo.com/lite/").mock(
        return_value=httpx.Response(429)
    )
    respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
        return_value=httpx.Response(429)
    )
    respx.get("https://en.wikipedia.org/w/api.php").mock(
        return_value=httpx.Response(200, json={"query": {"search": []}})
    )
    tool = WebSearch(WebConfig())
    with pytest.raises(ToolError, match="All search backends failed"):
        await tool.run(WebSearchParams(query="x"), ctx)


@pytest.mark.asyncio
@respx.mock
async def test_redirect_wrappers_are_unwrapped(ctx):
    html = (
        '<a class="result__a" '
        'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Ftarget.example%2Fpage">'
        "Wrapped</a>"
    )
    respx.post("https://html.duckduckgo.com/html/").mock(
        return_value=httpx.Response(200, text=html)
    )
    tool = WebSearch(WebConfig(backend="duckduckgo"))
    out = await tool.run(WebSearchParams(query="x"), ctx)
    assert "https://target.example/page" in out


@pytest.mark.asyncio
@respx.mock
async def test_brave_requires_key(ctx):
    tool = WebSearch(WebConfig(backend="brave"))
    with pytest.raises(ToolError, match="api_key"):
        await tool.run(WebSearchParams(query="x"), ctx)


@pytest.mark.asyncio
@respx.mock
async def test_tavily_search(ctx):
    respx.post("https://api.tavily.com/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"title": "T", "url": "https://t.example", "content": "c"}
                ]
            },
        )
    )
    tool = WebSearch(WebConfig(backend="tavily", api_key="secret"))
    out = await tool.run(WebSearchParams(query="x"), ctx)
    assert "https://t.example" in out


@pytest.mark.asyncio
@respx.mock
async def test_search_empty_results(ctx):
    respx.get("http://searx.local/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    tool = WebSearch(
        WebConfig(backend="searxng", searxng_url="http://searx.local")
    )
    out = await tool.run(WebSearchParams(query="nothing"), ctx)
    assert "no results" in out


@pytest.mark.asyncio
@respx.mock
async def test_fetch_url_extracts_text_and_marks_untrusted(ctx):
    respx.get("https://example.com/page").mock(
        return_value=httpx.Response(
            200,
            html=(
                "<html><head><title>Doc</title></head>"
                "<body><p>Body text here.</p></body></html>"
            ),
        )
    )
    tool = FetchUrl(WebConfig())
    out = await tool.run(FetchUrlParams(url="https://example.com/page"), ctx)
    assert UNTRUSTED_BANNER in out
    assert "Body text here." in out
    assert "https://example.com/page" in out


@pytest.mark.asyncio
async def test_fetch_url_blocks_private_addresses_by_default(ctx):
    tool = FetchUrl(WebConfig())
    with pytest.raises(ToolError, match="private"):
        await tool.run(FetchUrlParams(url="http://127.0.0.1:8080/admin"), ctx)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_url_blocks_redirect_to_private_address(ctx):
    respx.get("https://example.com/start").mock(
        return_value=httpx.Response(
            302, headers={"location": "http://127.0.0.1/admin"}
        )
    )
    respx.get("http://127.0.0.1/admin").mock(
        return_value=httpx.Response(200, text="internal secret")
    )

    tool = FetchUrl(WebConfig())
    with pytest.raises(ToolError, match="private"):
        await tool.run(FetchUrlParams(url="https://example.com/start"), ctx)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_url_limits_redirects(ctx):
    for index in range(6):
        respx.get(f"https://example.com/{index}").mock(
            return_value=httpx.Response(
                302,
                headers={"location": f"https://example.com/{index + 1}"},
            )
        )
    respx.get("https://example.com/6").mock(
        return_value=httpx.Response(200, text="too far")
    )

    tool = FetchUrl(WebConfig())
    with pytest.raises(ToolError, match="redirect"):
        await tool.run(FetchUrlParams(url="https://example.com/0"), ctx)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_url_follows_public_relative_redirect(ctx):
    respx.get("https://example.com/start").mock(
        return_value=httpx.Response(302, headers={"location": "/final"})
    )
    respx.get("https://example.com/final").mock(
        return_value=httpx.Response(
            200,
            text="public result",
            headers={"content-type": "text/plain"},
        )
    )

    output = await FetchUrl(WebConfig()).run(
        FetchUrlParams(url="https://example.com/start"), ctx
    )

    assert "public result" in output


@pytest.mark.asyncio
@respx.mock
async def test_fetch_url_allows_private_redirect_when_configured(ctx):
    respx.get("https://example.com/start").mock(
        return_value=httpx.Response(
            302, headers={"location": "http://127.0.0.1/final"}
        )
    )
    respx.get("http://127.0.0.1/final").mock(
        return_value=httpx.Response(
            200,
            text="private result",
            headers={"content-type": "text/plain"},
        )
    )

    output = await FetchUrl(WebConfig(allow_private_addresses=True)).run(
        FetchUrlParams(url="https://example.com/start"), ctx
    )

    assert "private result" in output


@pytest.mark.asyncio
@respx.mock
async def test_fetch_url_fails_closed_when_dns_resolution_fails(
    ctx, monkeypatch
):
    def fail_resolution(*args, **kwargs):
        raise socket.gaierror("not found")

    monkeypatch.setattr("quaso.tools.web.socket.getaddrinfo", fail_resolution)
    respx.get("https://unresolved.example/page").mock(
        return_value=httpx.Response(200, text="unexpected")
    )

    with pytest.raises(ToolError, match="resolve"):
        await FetchUrl(WebConfig()).run(
            FetchUrlParams(url="https://unresolved.example/page"), ctx
        )


@pytest.mark.asyncio
@respx.mock
async def test_fetch_url_allows_private_when_configured(ctx):
    respx.get("http://127.0.0.1:8080/x").mock(
        return_value=httpx.Response(
            200, text="ok", headers={"content-type": "text/plain"}
        )
    )
    tool = FetchUrl(WebConfig(allow_private_addresses=True))
    out = await tool.run(FetchUrlParams(url="http://127.0.0.1:8080/x"), ctx)
    assert "ok" in out


@pytest.mark.asyncio
async def test_fetch_url_rejects_non_http_scheme(ctx):
    tool = FetchUrl(WebConfig())
    with pytest.raises(ToolError, match="http"):
        await tool.run(FetchUrlParams(url="file:///etc/passwd"), ctx)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_url_http_error(ctx):
    respx.get("https://example.com/missing").mock(
        return_value=httpx.Response(404)
    )
    tool = FetchUrl(WebConfig())
    with pytest.raises(ToolError, match="404"):
        await tool.run(FetchUrlParams(url="https://example.com/missing"), ctx)


class TestFetchBudget:
    """Bounding the page twice spills an already-cut copy under a note
    that promises the whole of it, so the tool leaves the budget alone."""

    def test_the_budget_comes_from_config_not_the_tool(self, tmp_path):
        tool = FetchUrl(WebConfig())
        ctx = ToolContext(cwd=tmp_path, max_output_chars=8_000)
        assert tool.output_limit(ctx) == WebConfig().fetch_max_chars

    def test_a_per_tool_override_still_wins(self, tmp_path):
        tool = FetchUrl(WebConfig())
        ctx = ToolContext(
            cwd=tmp_path,
            max_output_chars=8_000,
            tool_output_chars={"fetch_url": 500},
        )
        assert tool.output_limit(ctx) == 500

    @pytest.mark.asyncio
    @respx.mock
    async def test_a_long_page_is_returned_whole(self, ctx):
        """The agent bounds it and keeps the rest on disk; the tool must
        not cut it first or that copy is incomplete."""
        body = "<html><body>" + ("word " * 20_000) + "</body></html>"
        respx.get("https://example.com/big").mock(
            return_value=httpx.Response(
                200, text=body, headers={"content-type": "text/html"}
            )
        )
        tool = FetchUrl(WebConfig())
        out = await tool.run(
            FetchUrlParams(url="https://example.com/big"), ctx
        )
        assert len(out) > WebConfig().fetch_max_chars
        assert "truncated" not in out

    @pytest.mark.asyncio
    @respx.mock
    async def test_an_explicit_cap_is_still_honoured(self, ctx):
        body = "<html><body>" + ("word " * 20_000) + "</body></html>"
        respx.get("https://example.com/big").mock(
            return_value=httpx.Response(
                200, text=body, headers={"content-type": "text/html"}
            )
        )
        tool = FetchUrl(WebConfig())
        out = await tool.run(
            FetchUrlParams(url="https://example.com/big", max_chars=1_000),
            ctx,
        )
        assert len(out) <= 1_000


class TestResponseCeiling:
    """A page is read into memory whole before anything trims it, so the
    size of it has to be the server's decision only up to a point."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_an_oversized_response_is_cut_off(self, ctx):
        huge = "x" * (MAX_RESPONSE_BYTES * 2)
        respx.get("https://example.com/huge").mock(
            return_value=httpx.Response(
                200, text=huge, headers={"content-type": "text/plain"}
            )
        )
        out = await FetchUrl(WebConfig()).run(
            FetchUrlParams(url="https://example.com/huge"), ctx
        )
        assert len(out) <= MAX_RESPONSE_BYTES + 500
        assert "not read" in out

    @pytest.mark.asyncio
    @respx.mock
    async def test_a_lying_content_length_does_not_help(self, ctx):
        """The header is the server's claim, not a measurement."""
        huge = "x" * (MAX_RESPONSE_BYTES * 2)
        respx.get("https://example.com/liar").mock(
            return_value=httpx.Response(
                200,
                text=huge,
                headers={
                    "content-type": "text/plain",
                    "content-length": "10",
                },
            )
        )
        out = await FetchUrl(WebConfig()).run(
            FetchUrlParams(url="https://example.com/liar"), ctx
        )
        assert len(out) <= MAX_RESPONSE_BYTES + 500

    @pytest.mark.asyncio
    @respx.mock
    async def test_an_ordinary_page_is_untouched(self, ctx):
        respx.get("https://example.com/small").mock(
            return_value=httpx.Response(
                200,
                text="<html><body>hello</body></html>",
                headers={"content-type": "text/html"},
            )
        )
        out = await FetchUrl(WebConfig()).run(
            FetchUrlParams(url="https://example.com/small"), ctx
        )
        assert "hello" in out
        assert "not read" not in out
