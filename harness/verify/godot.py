"""Locating the Godot binary and working with its version string."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
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


CHECK_SCRIPTS_GD = Path(__file__).with_name("gdscript") / "check_scripts.gd"

# Godot often exits 0 after script and import errors, so the log decides.
_ERROR_PREFIXES = ("SCRIPT ERROR:", "ERROR:", "USER ERROR:", "USER SCRIPT ERROR:")
_WARNING_PREFIXES = ("WARNING:", "USER WARNING:", "SCRIPT WARNING:")


@dataclass(frozen=True)
class LogIssue:
    message: str
    location: str | None  # the "at: ..." line that follows, if any


@dataclass(frozen=True)
class GodotRun:
    args: list[str]
    returncode: int | None
    timed_out: bool
    duration_s: float
    output: str
    errors: list[LogIssue]
    warnings: list[LogIssue]

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.returncode == 0 and not self.errors


def parse_log(text: str) -> tuple[list[LogIssue], list[LogIssue]]:
    errors: list[LogIssue] = []
    warnings: list[LogIssue] = []
    lines = text.splitlines()
    for i, raw in enumerate(lines):
        line = raw.strip()
        target = errors if line.startswith(_ERROR_PREFIXES) else warnings if line.startswith(_WARNING_PREFIXES) else None
        if target is None:
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        target.append(LogIssue(line, nxt[3:].strip() if nxt.startswith("at:") else None))
    return errors, warnings


class GodotRunner:
    """Every Godot call goes through here: one binary, a timeout, log parsing."""

    def __init__(self, bin_path: Path):
        self.bin_path = bin_path

    def run(self, project: Path, args: list[str], *, timeout_s: float, headless: bool = True) -> GodotRun:
        full = [str(self.bin_path), *(["--headless"] if headless else []), "--path", str(project), *args]
        result = proc.run(full, timeout_s=timeout_s, cwd=project)
        output = result.stdout + result.stderr
        errors, warnings = parse_log(output)
        return GodotRun(full, result.returncode, result.timed_out, result.duration_s, output, errors, warnings)

    def import_project(self, project: Path, timeout_s: float = 300) -> GodotRun:
        return self.run(project, ["--import"], timeout_s=timeout_s)

    def check_scripts(self, project: Path, timeout_s: float = 120) -> GodotRun:
        """Compile all .gd files with autoloads registered (--check-only cannot see autoloads)."""
        return self.run(project, ["--script", str(CHECK_SCRIPTS_GD)], timeout_s=timeout_s)

    def smoke(self, project: Path, frames: int = 120, timeout_s: float = 60) -> GodotRun:
        """Start the main scene, run `frames` frames, quit."""
        return self.run(project, ["--fixed-fps", "60", "--quit-after", str(frames)], timeout_s=timeout_s)
