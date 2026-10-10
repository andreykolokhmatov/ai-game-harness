"""Environment for agent processes: built from an allowlist, never the full user env."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

_COMMON = [
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "SHELL",
    "TMPDIR", "TEMP", "TMP",
    "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
    "DISPLAY", "WAYLAND_DISPLAY",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN",
]
_WINDOWS = [
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "USERPROFILE", "USERNAME",
    "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES",
    "PROGRAMFILES(X86)", "COMMONPROGRAMFILES",
]


def agent_env(
    *,
    extra: dict[str, str] | None = None,
    prepend_path: list[Path] | None = None,
    pass_api_key: bool = False,
    source: dict[str, str] | None = None,
) -> dict[str, str]:
    src = dict(os.environ) if source is None else source
    names = _COMMON + (_WINDOWS if sys.platform == "win32" else [])
    if pass_api_key:
        names.append("ANTHROPIC_API_KEY")
    if sys.platform == "win32":  # env names are case-insensitive on Windows
        upper = {k.upper(): v for k, v in src.items()}
        env = {n: upper[n.upper()] for n in names if n.upper() in upper}
    else:
        env = {n: src[n] for n in names if n in src}
    if prepend_path:
        env["PATH"] = os.pathsep.join([*(str(p) for p in prepend_path), env.get("PATH", "")])
    env.update(extra or {})
    return env


def write_shim(bin_dir: Path, name: str, target: Path) -> Path:
    """Make `name` in bin_dir run target (symlink on POSIX, .cmd file on Windows)."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        shim = bin_dir / f"{name}.cmd"
        shim.write_text(f'@echo off\r\n"{target}" %*\r\n', encoding="utf-8")
        return shim
    shim = bin_dir / name
    if shim.is_symlink() or shim.exists():
        shim.unlink()
    shim.symlink_to(target)
    return shim


def write_render_shim(bin_dir: Path, name: str, godot: Path, display_prefix: list[str] | None) -> Path:
    """`name` runs Godot with rendering: through the display prefix (xvfb-run on Linux) and dummy
    audio, so agents get the same GUI behaviour as the Harness checks. display_prefix=None: no
    display on this machine, the shim falls back to headless."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        shim = bin_dir / f"{name}.cmd"
        shim.write_text(f'@echo off\r\n"{godot}" --audio-driver Dummy %*\r\n', encoding="utf-8")
        return shim
    mode = ["--audio-driver", "Dummy"] if display_prefix is not None else ["--headless"]
    cmd = " ".join(shlex.quote(part) for part in [*(display_prefix or []), str(godot), *mode])
    shim = bin_dir / name
    if shim.is_symlink():
        shim.unlink()
    shim.write_text(f'#!/bin/sh\nexec {cmd} "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    return shim


def user_data_env(root: Path, base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment that moves Godot's user:// (saves, settings) under root.

    Linux: user:// lives in $XDG_DATA_HOME/godot/app_userdata; Windows: %APPDATA%/Godot/app_userdata.
    Do not use it for exports: export templates are looked up in the same data directory.
    """
    env = dict(os.environ if base is None else base)
    env["APPDATA" if sys.platform == "win32" else "XDG_DATA_HOME"] = str(root)
    return env
