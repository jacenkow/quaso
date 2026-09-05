"""First-run configuration.

Runs only when there is nothing to run with: no model configured and no
config file to read one from. Asks where the server is, shows what it has,
and writes .quaso/config.toml so the question is never asked again.
"""

from __future__ import annotations

from pathlib import Path

import httpx
from prompt_toolkit import PromptSession
from rich.console import Console

from quaso.config import (
    DEFAULT_CONTEXT_TOKENS,
    PROJECT_CONFIG_DIR,
    Config,
    load_config,
)
from quaso.private import make_private
from quaso.providers.ollama import list_models, supports_tools

LOCAL_URL = "http://localhost:11434"

_TEMPLATE = """\
# Written by quaso on first run. Edit freely; every value is optional.

[provider]
name = "ollama"
base_url = "{base_url}"
model = "{model}"

[provider.options]
# The context window, and the only place to set it. quaso sends this exact
# value on every request, so the window it compacts against and the window
# the server allocates are always the same.
num_ctx = {num_ctx}
keep_alive = "10m"
{think}
[permissions]
mode = "default"                # default | acceptEdits | readonly | yolo
"""


def needs_setup(config: Config, cwd: Path | None = None) -> bool:
    """True when there is no model and no config file to blame for it."""
    if config.provider.model:
        return False
    cwd = cwd or Path.cwd()
    return not (cwd / PROJECT_CONFIG_DIR / "config.toml").is_file()


def _size(model: dict) -> str:
    details = model.get('details') or {}
    if parameters := details.get('parameter_size'):
        return str(parameters)
    if size := model.get('size'):
        return f"{size / 1e9:.0f} GB"
    return ""


def write_config(
    base_url: str, model: str, thinking: bool, cwd: Path | None = None
) -> Path:
    cwd = cwd or Path.cwd()
    directory = cwd / PROJECT_CONFIG_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "config.toml"
    path.write_text(
        _TEMPLATE.format(
            base_url=base_url,
            model=model,
            num_ctx=DEFAULT_CONTEXT_TOKENS,
            think="think = true\n" if thinking else "",
        )
    )
    make_private(path)
    return path


class Wizard:
    def __init__(
        self,
        console: Console | None = None,
        session: PromptSession | None = None,
    ) -> None:
        self.console = console or Console()
        self.session = session or PromptSession()

    async def _ask(self, prompt: str, default: str = "") -> str:
        answer = (await self.session.prompt_async(prompt)).strip()
        return answer or default

    async def _choose_server(self) -> str | None:
        """Returns a reachable base URL, or None if the user gave up."""
        self.console.print(
            "\n[bold]Where is your Ollama server?[/]", highlight=False
        )
        self.console.print(
            "  [bold #fbbf24]1[/] this machine", highlight=False
        )
        self.console.print(
            "  [bold #fbbf24]2[/] another machine", highlight=False
        )
        choice = await self._ask("\n> ", "1")

        if choice == "2":
            url = await self._ask("  Server URL: ")
            if not url:
                return None
            if "://" not in url:
                url = f"http://{url}"
            if url.count(":") < 2:
                url = f"{url}:11434"
            return url
        return LOCAL_URL

    async def _choose_model(self, models: list[dict]) -> str | None:
        usable = [m for m in models if supports_tools(m)]
        if not usable:
            self.console.print(
                "\n[bold red]None of these models can call tools[/], which "
                "quaso needs. Try [bold]ollama pull qwen3[/] first.",
                highlight=False,
            )
            return None

        self.console.print(
            "\n[bold]Which model should quaso use?[/]", highlight=False
        )
        width = max(len(m['name']) for m in usable)
        for index, model in enumerate(usable, 1):
            self.console.print(
                f"  [bold #fbbf24]{index}[/] {model['name']:<{width}}  "
                f"[dim]{_size(model)}[/]",
                highlight=False,
            )
        skipped = len(models) - len(usable)
        if skipped:
            self.console.print(
                f"  [dim]({skipped} more installed, but without tool "
                "support)[/]",
                highlight=False,
            )

        while True:
            answer = await self._ask("\n> ", "1")
            if answer.isdigit() and 1 <= int(answer) <= len(usable):
                return usable[int(answer) - 1]['name']
            if answer in {m['name'] for m in usable}:
                return answer
            self.console.print(
                f"  [dim]Enter 1-{len(usable)}.[/]", highlight=False
            )

    async def run(self, cwd: Path | None = None) -> Config | None:
        """Ask, verify, write. Returns the new config, or None if aborted."""
        self.console.print(
            "\n[bold #fbbf24]Welcome to quaso.[/] No configuration yet, so "
            "let's make one.",
            highlight=False,
        )

        while True:
            base_url = await self._choose_server()
            if base_url is None:
                return None
            self.console.print(
                f"\n  [dim]Checking {base_url} ...[/]", highlight=False
            )
            try:
                models = await list_models(base_url)
            except httpx.HTTPError as exc:
                self.console.print(
                    f"  [red]Could not reach {base_url}[/] [dim]({exc})[/]\n"
                    "  Is Ollama running? Try [bold]ollama serve[/].",
                    highlight=False,
                )
                if (await self._ask("\n  Try again? [Y/n] ", "y")).lower() in (
                    "n",
                    "no",
                ):
                    return None
                continue
            if not models:
                self.console.print(
                    "  [red]That server has no models.[/] Try "
                    "[bold]ollama pull qwen3[/] first.",
                    highlight=False,
                )
                return None
            break

        self.console.print(
            f"  [dim]found {len(models)} models[/]", highlight=False
        )
        model = await self._choose_model(models)
        if model is None:
            return None

        thinking = any(
            m['name'] == model and "thinking" in (m.get('capabilities') or [])
            for m in models
        )
        path = write_config(base_url, model, thinking, cwd)
        self.console.print(f"\n  [dim]Wrote[/] {path}\n", highlight=False)
        return load_config(cwd)
