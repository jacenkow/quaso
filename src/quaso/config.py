"""Layered configuration.

Precedence, lowest first: built-in defaults, ~/.config/quaso/config.toml,
./.quaso/config.toml, then CLI flags applied by the caller.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

USER_CONFIG_PATH = Path.home() / ".config" / "quaso" / "config.toml"
PROJECT_CONFIG_DIR = ".quaso"

# Deliberately modest. Measured accuracy on this harness does not improve
# with a larger window, while VRAM and the model's appetite for reading
# both grow with it.
DEFAULT_CONTEXT_TOKENS = 32768

# ${VAR}, or ${VAR:-fallback} with shell semantics, so a machine-specific
# endpoint can live in the environment instead of a config file.
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ProviderConfig(BaseModel):
    name: str = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = ""
    options: dict[str, Any] = Field(default_factory=dict)


class PermissionsConfig(BaseModel):
    mode: Literal["default", "acceptEdits", "readonly", "yolo"] = "default"
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


class ContextConfig(BaseModel):
    max_tokens: int = 0
    auto_compact: bool = True
    # Backstop only: the model is expected to call the compact tool at its
    # own milestones before this fires.
    compact_threshold: float = 0.7
    keep_recent_messages: int = 8
    # Below this, the model is not told about its context usage.
    notice_threshold: float = 0.5


class WebConfig(BaseModel):
    backend: Literal[
        "auto",
        "duckduckgo",
        "ddg_lite",
        "stackoverflow",
        "hackernews",
        "wikipedia",
        "searxng",
        "tavily",
        "brave",
    ] = "auto"
    searxng_url: str = ""
    api_key: str = ""
    max_results: int = 5
    timeout: float = 20.0
    # The most a fetched page may cost the context. The whole page is
    # kept on disk regardless, so this bounds the quote, not the fetch.
    fetch_max_chars: int = 12_000
    allow_private_addresses: bool = False


class HookConfig(BaseModel):
    matcher: str = "*"
    command: str
    timeout: int = 30


class HooksConfig(BaseModel):
    pre_tool_use: list[HookConfig] = Field(default_factory=list)
    post_tool_use: list[HookConfig] = Field(default_factory=list)


class MCPServerConfig(BaseModel):
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class MCPConfig(BaseModel):
    servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class AgentConfig(BaseModel):
    max_iterations: int = 40
    # Default ceiling on a single tool result. Tools set their own defaults
    # around this, and tool_output_chars overrides both by name.
    max_tool_output_chars: int = 8_000
    tool_output_chars: dict[str, int] = Field(default_factory=dict)
    require_read_before_edit: bool = True
    subagent_max_iterations: int = 20


class SandboxConfig(BaseModel):
    # "auto" uses whatever this machine provides and says so at startup;
    # "off" is the old behaviour, which is what a machine with no
    # sandbox available falls back to anyway.
    mode: Literal["auto", "off"] = "auto"
    # Ordinary work fetches packages, so denying the network by default
    # would mostly teach people to turn the sandbox off. Filesystem
    # confinement is what closes the gap that matters.
    network: bool = True


class Config(BaseModel):
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def _deep_merge(base: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in other.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _substitute(match: re.Match) -> str:
    name, fallback = match.group(1), match.group(2)
    return os.environ.get(name) or fallback or ""


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(_substitute, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load_config(cwd: Path | None = None) -> Config:
    cwd = cwd or Path.cwd()
    project = cwd / PROJECT_CONFIG_DIR / "config.toml"
    data: dict[str, Any] = {}
    for path in (USER_CONFIG_PATH, project):
        data = _deep_merge(data, _read_toml(path))
    return Config.model_validate(_expand_env(data))


def context_limit(config: Config) -> int:
    """The effective context window, in tokens.

    The single source of truth: the agent compacts against this number and
    the provider allocates exactly it, so the two cannot drift apart.
    Precedence is context.max_tokens, then provider.options.num_ctx, then
    the default.
    """
    if config.context.max_tokens > 0:
        return config.context.max_tokens
    num_ctx = config.provider.options.get('num_ctx')
    if isinstance(num_ctx, int) and num_ctx > 0:
        return num_ctx
    return DEFAULT_CONTEXT_TOKENS


def resolved_provider(config: Config) -> ProviderConfig:
    """Provider settings with the context window pinned.

    Left to itself, Ollama allocates the model's maximum window: for a
    262k model that is tens of gigabytes of VRAM, and an agent compacting
    against a number bearing no relation to it. Sending num_ctx on every
    request keeps the server and the agent in agreement.

    num_ctx is Ollama's own parameter, so it is only injected for that
    provider; a third-party provider owns its own window.
    """
    if config.provider.name != "ollama":
        return config.provider
    options = {**config.provider.options, "num_ctx": context_limit(config)}
    return config.provider.model_copy(update={"options": options})
