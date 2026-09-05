from __future__ import annotations

from quaso.config import Config, _deep_merge, load_config


def test_defaults():
    config = Config()
    assert config.provider.name == "ollama"
    assert config.permissions.mode == "default"


def test_deep_merge():
    base = {
        "provider": {"name": "ollama", "model": "a"},
        "agent": {"max_iterations": 40},
    }
    override = {"provider": {"model": "b"}}
    merged = _deep_merge(base, override)
    assert merged["provider"] == {"name": "ollama", "model": "b"}
    assert merged["agent"]["max_iterations"] == 40


def test_project_config_overrides(tmp_path, monkeypatch):
    # Point the user-level config somewhere empty so the host machine's real
    # config can't leak into the test.
    monkeypatch.setattr(
        "quaso.config.USER_CONFIG_PATH", tmp_path / "no-user-config.toml"
    )
    project = tmp_path / ".quaso"
    project.mkdir()
    (project / "config.toml").write_text(
        """
[provider]
model = "qwen3.6:latest"
base_url = "http://ollama.example:11434"

[provider.options]
think = true

[permissions]
mode = "acceptEdits"
allow = ["bash(git status*)"]
"""
    )
    config = load_config(cwd=tmp_path)
    assert config.provider.model == "qwen3.6:latest"
    assert config.provider.options["think"] is True
    assert config.permissions.mode == "acceptEdits"
    assert config.permissions.allow == ["bash(git status*)"]
    # Untouched defaults survive.
    assert config.agent.max_iterations == 40


def test_missing_configs_fine(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "quaso.config.USER_CONFIG_PATH", tmp_path / "nope.toml"
    )
    config = load_config(cwd=tmp_path)
    assert isinstance(config, Config)


def test_env_expansion_uses_the_variable_when_set(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "quaso.config.USER_CONFIG_PATH", tmp_path / "none.toml"
    )
    monkeypatch.setenv("QUASO_TEST_URL", "http://set.example:11434")
    project = tmp_path / ".quaso"
    project.mkdir()
    (project / "config.toml").write_text(
        '[provider]\nbase_url = "${QUASO_TEST_URL:-http://fallback:11434}"\n'
    )
    config = load_config(cwd=tmp_path)
    assert config.provider.base_url == "http://set.example:11434"


def test_env_expansion_falls_back_when_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "quaso.config.USER_CONFIG_PATH", tmp_path / "none.toml"
    )
    monkeypatch.delenv("QUASO_TEST_URL", raising=False)
    project = tmp_path / ".quaso"
    project.mkdir()
    (project / "config.toml").write_text(
        '[provider]\nbase_url = "${QUASO_TEST_URL:-http://fallback:11434}"\n'
    )
    config = load_config(cwd=tmp_path)
    assert config.provider.base_url == "http://fallback:11434"


def test_env_expansion_without_fallback_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "quaso.config.USER_CONFIG_PATH", tmp_path / "none.toml"
    )
    monkeypatch.delenv("QUASO_TEST_KEY", raising=False)
    project = tmp_path / ".quaso"
    project.mkdir()
    (project / "config.toml").write_text(
        '[web]\napi_key = "${QUASO_TEST_KEY}"\n'
    )
    assert load_config(cwd=tmp_path).web.api_key == ""
