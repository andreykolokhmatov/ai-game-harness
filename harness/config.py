"""Loading and validation of config/*.yaml.

config/harness.yaml holds defaults, config/local.yaml (optional, not in Git)
overrides them per machine, environment variables override both.
Model IDs live only in config/models.yaml.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_DIR = REPO_ROOT / "config"

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class GodotConfig:
    version: str
    bin: str | None


@dataclass(frozen=True)
class RoleModel:
    model_id: str
    effort: str | None


@dataclass(frozen=True)
class Config:
    config_dir: Path
    workspace: Path
    godot: GodotConfig
    claude_bin: str
    auth: str
    claude_config: str
    roles: dict[str, RoleModel]
    raw: dict[str, Any] = field(repr=False)
    models_raw: dict[str, Any] = field(repr=False)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _choice(raw: dict[str, Any], key: str, allowed: tuple[str, ...]) -> str:
    value = raw.get(key)
    if value not in allowed:
        raise ConfigError(f"harness.yaml: '{key}' must be one of {allowed}, got {value!r}")
    return value


def resolve_roles(models_raw: dict[str, Any]) -> dict[str, RoleModel]:
    aliases = models_raw.get("models")
    roles = models_raw.get("roles")
    if not isinstance(aliases, dict) or not aliases:
        raise ConfigError("models.yaml: 'models' must be a non-empty mapping alias -> model ID")
    if not isinstance(roles, dict) or not roles:
        raise ConfigError("models.yaml: 'roles' must be a non-empty mapping")
    resolved: dict[str, RoleModel] = {}
    for role, spec in roles.items():
        if not isinstance(spec, dict) or "model" not in spec:
            raise ConfigError(f"models.yaml: role '{role}' needs a 'model' alias")
        alias = spec["model"]
        if alias not in aliases:
            raise ConfigError(f"models.yaml: role '{role}' uses unknown model alias '{alias}'")
        effort = spec.get("effort")
        if effort is not None and effort not in EFFORT_LEVELS:
            raise ConfigError(f"models.yaml: role '{role}' has invalid effort '{effort}'")
        resolved[role] = RoleModel(model_id=str(aliases[alias]), effort=effort)
    for alias in models_raw.get("fallback") or []:
        if alias not in aliases:
            raise ConfigError(f"models.yaml: fallback uses unknown model alias '{alias}'")
    return resolved


def load_config(config_dir: Path | None = None, env: dict[str, str] | None = None) -> Config:
    config_dir = (config_dir or Path(os.environ.get("HARNESS_CONFIG_DIR", DEFAULT_CONFIG_DIR))).resolve()
    env = dict(os.environ) if env is None else env

    harness_file = config_dir / "harness.yaml"
    models_file = config_dir / "models.yaml"
    for path in (harness_file, models_file):
        if not path.is_file():
            raise ConfigError(f"missing config file: {path}")

    raw = _read_yaml(harness_file)
    local_file = config_dir / "local.yaml"
    if local_file.is_file():
        raw = _deep_merge(raw, _read_yaml(local_file))
    models_raw = _read_yaml(models_file)

    workspace = Path(env.get("HARNESS_WORKSPACE") or raw.get("workspace") or "workspace").expanduser()
    if not workspace.is_absolute():
        workspace = config_dir.parent / workspace

    godot_raw = raw.get("godot") or {}
    version = str(godot_raw.get("version") or "")
    if not version:
        raise ConfigError("harness.yaml: 'godot.version' is required")
    godot_bin = env.get("GODOT_BIN") or godot_raw.get("bin")

    claude_bin = env.get("CLAUDE_BIN") or (raw.get("claude") or {}).get("bin") or "claude"

    return Config(
        config_dir=config_dir,
        workspace=workspace,
        godot=GodotConfig(version=version, bin=str(godot_bin) if godot_bin else None),
        claude_bin=str(claude_bin),
        auth=_choice(raw, "auth", ("subscription", "api_key")),
        claude_config=_choice(raw, "claude_config", ("inherit", "isolated")),
        roles=resolve_roles(models_raw),
        raw=raw,
        models_raw=models_raw,
    )
