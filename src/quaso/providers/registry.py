"""Built-in providers merged with `quaso.providers` entry points."""

from __future__ import annotations

from importlib.metadata import entry_points

from quaso.config import ProviderConfig
from quaso.providers.base import Provider
from quaso.providers.ollama import OllamaProvider

_BUILTINS: dict[str, type[Provider]] = {
    "ollama": OllamaProvider,
}


def _discover() -> tuple[dict[str, type[Provider]], dict[str, str]]:
    """Providers that loaded, and why the others did not.

    An installed plugin is code quaso did not write, and importing it can
    fail for reasons that have nothing to do with the session at hand.
    One that does is set aside rather than allowed to take the built-ins
    with it, since it is usually not even the provider being asked for.
    """
    providers = dict(_BUILTINS)
    failures: dict[str, str] = {}
    for entry in entry_points(group="quaso.providers"):
        if entry.name in _BUILTINS:
            # A built-in name means a config that has always worked would
            # quietly change meaning on installing a package.
            continue
        try:
            providers[entry.name] = entry.load()
        except Exception as exc:
            failures[entry.name] = f"{type(exc).__name__}: {exc}"
    return providers, failures


def available_providers() -> dict[str, type[Provider]]:
    return _discover()[0]


def create_provider(config: ProviderConfig) -> Provider:
    providers, failures = _discover()
    if config.name not in providers:
        if config.name in failures:
            raise ValueError(
                f"Provider {config.name!r} is installed but did not load "
                f"({failures[config.name]})"
            )
        known = ", ".join(sorted(providers))
        raise ValueError(
            f"Unknown provider {config.name!r} (available: {known})"
        )
    return providers[config.name](
        base_url=config.base_url,
        model=config.model,
        options=config.options,
    )
