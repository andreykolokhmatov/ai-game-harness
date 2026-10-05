from pathlib import Path

import pytest
import yaml

from harness.config import DEFAULT_CONFIG_DIR, ConfigError, load_config, resolve_roles


def _copy_config(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for name in ("harness.yaml", "models.yaml"):
        (config_dir / name).write_text((DEFAULT_CONFIG_DIR / name).read_text(encoding="utf-8"), encoding="utf-8")
    return config_dir


def test_repo_config_loads():
    cfg = load_config(DEFAULT_CONFIG_DIR, env={})
    assert cfg.godot.version == "4.4"
    assert cfg.auth == "subscription"
    assert cfg.roles["engineer"].model_id.startswith("claude-")
    assert set(cfg.roles) >= {"planner", "engineer", "evaluator", "debugger"}


def test_workspace_relative_to_repo_root(tmp_path):
    cfg = load_config(_copy_config(tmp_path), env={})
    assert cfg.workspace == tmp_path / "workspace"


def test_env_overrides(tmp_path):
    env = {"HARNESS_WORKSPACE": str(tmp_path / "ws"), "GODOT_BIN": "/opt/godot", "CLAUDE_BIN": "my-claude"}
    cfg = load_config(_copy_config(tmp_path), env=env)
    assert cfg.workspace == tmp_path / "ws"
    assert cfg.godot.bin == "/opt/godot"
    assert cfg.claude_bin == "my-claude"


def test_local_yaml_deep_merges(tmp_path):
    config_dir = _copy_config(tmp_path)
    (config_dir / "local.yaml").write_text("godot:\n  bin: /x/godot\n", encoding="utf-8")
    cfg = load_config(config_dir, env={})
    assert cfg.godot.bin == "/x/godot"
    assert cfg.godot.version == "4.4"  # sibling key kept


def test_invalid_auth_rejected(tmp_path):
    config_dir = _copy_config(tmp_path)
    (config_dir / "local.yaml").write_text("auth: password\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="auth"):
        load_config(config_dir, env={})


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="missing config file"):
        load_config(tmp_path, env={})


def test_unknown_model_alias():
    raw = yaml.safe_load("models: {a: m-1}\nroles: {engineer: {model: b}}\n")
    with pytest.raises(ConfigError, match="unknown model alias 'b'"):
        resolve_roles(raw)


def test_invalid_effort():
    raw = yaml.safe_load("models: {a: m-1}\nroles: {engineer: {model: a, effort: turbo}}\n")
    with pytest.raises(ConfigError, match="invalid effort"):
        resolve_roles(raw)
