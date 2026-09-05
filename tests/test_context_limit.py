"""The context window must be one number, not two that can disagree.

Left to itself Ollama allocates the model's maximum, so an unconfigured
run burned tens of gigabytes while the agent compacted against an
unrelated default.
"""

from __future__ import annotations

from quaso.config import (
    DEFAULT_CONTEXT_TOKENS,
    Config,
    ContextConfig,
    ProviderConfig,
    context_limit,
    resolved_provider,
)


def _config(**provider_options) -> Config:
    return Config(provider=ProviderConfig(options=provider_options))


def test_default_is_modest_not_the_models_maximum():
    assert context_limit(Config()) == DEFAULT_CONTEXT_TOKENS


def test_num_ctx_sets_the_window():
    assert context_limit(_config(num_ctx=65536)) == 65536


def test_max_tokens_overrides_num_ctx():
    config = _config(num_ctx=131072)
    config.context = ContextConfig(max_tokens=50000)
    assert context_limit(config) == 50000


def test_junk_num_ctx_falls_back_to_the_default():
    for junk in ("131072", 0, -1, None):
        assert context_limit(_config(num_ctx=junk)) == DEFAULT_CONTEXT_TOKENS


def test_provider_always_receives_an_explicit_window():
    """Omitting num_ctx makes the server pick, which is the bug."""
    options = resolved_provider(Config()).options
    assert options['num_ctx'] == DEFAULT_CONTEXT_TOKENS


def test_provider_window_matches_what_the_agent_compacts_against():
    for config in (
        Config(),
        _config(num_ctx=65536),
        _config(temperature=0.2),
    ):
        assert resolved_provider(config).options['num_ctx'] == context_limit(
            config
        )


def test_max_tokens_reaches_the_provider_too():
    config = _config(num_ctx=131072)
    config.context = ContextConfig(max_tokens=50000)
    assert resolved_provider(config).options['num_ctx'] == 50000


def test_other_provider_options_survive():
    config = _config(think=True, keep_alive="10m")
    options = resolved_provider(config).options
    assert options['think'] is True
    assert options['keep_alive'] == "10m"
    assert options['num_ctx'] == DEFAULT_CONTEXT_TOKENS


def test_original_config_is_not_mutated():
    config = _config(temperature=0.2)
    resolved_provider(config)
    assert "num_ctx" not in config.provider.options


def test_num_ctx_is_not_forced_on_other_providers():
    """num_ctx is Ollama's parameter; a plugin owns its own window."""
    config = Config(provider=ProviderConfig(name="custom"))
    assert "num_ctx" not in resolved_provider(config).options
