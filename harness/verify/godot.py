"""Locating the Godot binary and working with its version string."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from harness.config import GodotConfig
from harness.platform import proc

_VERSION_LINE = re.compile(r"^\d+\.\d+(\.\d+)*\.[a-z]")
BIN_NAMES = ("godot", "godot4")


def resolve_bin(cfg: GodotConfig) -> Path | None:
    """Configured bin (path or name in PATH), else the first known name in PATH."""
    candidates = [cfg.bin] if cfg.bin else list(BIN_NAMES)
    for name in candidates:
        path = Path(name).expanduser()
        if path.is_file():
            return path.resolve()
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    return None


def parse_version(output: str) -> str | None:
    """Pick the version line from `godot --version`, e.g. '4.4.1.stable.official.49a5bc7b6'."""
    for line in reversed(output.splitlines()):
        line = line.strip()
        if _VERSION_LINE.match(line):
            return line
    return None


def template_version(full_version: str) -> str:
    """'4.4.1.stable.official.49a5bc7b6' -> '4.4.1.stable' (export_templates dir name)."""
    parts = full_version.split(".")
    for i, part in enumerate(parts):
        if not part.isdigit():
            return ".".join(parts[: i + 1])
    return full_version


def numeric_version(full_version: str) -> str:
    """'4.4.1.stable.official.49a5bc7b6' -> '4.4.1'."""
    return ".".join(p for p in template_version(full_version).split(".") if p.isdigit())


def version_matches(full_version: str, pin: str) -> bool:
    """Pin '4.4' matches 4.4 and 4.4.x; pin '4.4.1' matches only 4.4.1."""
    numeric = numeric_version(full_version)
    return numeric == pin or numeric.startswith(pin + ".")


def read_version(bin_path: Path, timeout_s: float = 30) -> str | None:
    result = proc.run([str(bin_path), "--headless", "--version"], timeout_s=timeout_s)
    if not result.ok:
        return None
    return parse_version(result.stdout)
