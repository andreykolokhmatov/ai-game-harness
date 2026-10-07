"""`harness doctor`: checks that the machine can run the Harness."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from harness.config import Config
from harness.platform import proc
from harness.platform.paths import godot_export_templates_dir
from harness.verify import godot

Status = Literal["ok", "warn", "fail"]

MIN_PYTHON = (3, 11)
# Single-threaded web export (see ARCHITECTURE.md 1.5): release for games, debug for test builds.
REQUIRED_WEB_TEMPLATES = ("web_nothreads_release.zip", "web_nothreads_debug.zip")


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str
    hint: str = ""


def check_python() -> Check:
    version = ".".join(map(str, sys.version_info[:3]))
    if sys.version_info[:2] < MIN_PYTHON:
        return Check("python", "fail", version, f"need Python >= {'.'.join(map(str, MIN_PYTHON))}")
    return Check("python", "ok", version)


def _tool_version(name: str, args: list[str], timeout_s: float = 30) -> tuple[str | None, str]:
    """(path, first output line) or (None, '') if the tool is not in PATH."""
    path = shutil.which(name)
    if not path:
        return None, ""
    result = proc.run([path, *args], timeout_s=timeout_s)
    line = (result.stdout or result.stderr).strip().splitlines()
    return path, line[0] if line else ""


def check_git() -> Check:
    path, version = _tool_version("git", ["--version"])
    if not path:
        return Check("git", "fail", "not found", "install git")
    return Check("git", "ok", version)


def check_claude(cfg: Config) -> list[Check]:
    path = shutil.which(cfg.claude_bin)
    if not path:
        return [
            Check("claude", "fail", f"'{cfg.claude_bin}' not found", "install Claude Code or set claude.bin / CLAUDE_BIN")
        ]
    version = proc.run([path, "--version"], timeout_s=30)
    checks = [Check("claude", "ok" if version.ok else "fail", version.stdout.strip() or version.stderr.strip())]

    if cfg.auth == "api_key":
        if os.environ.get("ANTHROPIC_API_KEY"):
            checks.append(Check("claude auth", "ok", "api_key (ANTHROPIC_API_KEY is set)"))
        else:
            checks.append(Check("claude auth", "fail", "auth: api_key but ANTHROPIC_API_KEY is not set"))
        return checks

    status = proc.run([path, "auth", "status"], timeout_s=30)
    try:
        info = json.loads(status.stdout)
    except json.JSONDecodeError:
        checks.append(Check("claude auth", "fail", "cannot parse `claude auth status`", "run `claude auth status`"))
        return checks
    if info.get("loggedIn"):
        checks.append(Check("claude auth", "ok", f"logged in ({info.get('authMethod', 'unknown method')})"))
    else:
        checks.append(Check("claude auth", "fail", "not logged in", "run `claude` and log in with your subscription"))
    return checks


def check_godot(cfg: Config) -> list[Check]:
    bin_path = godot.resolve_bin(cfg.godot)
    if not bin_path:
        return [
            Check(
                "godot",
                "fail",
                "binary not found",
                "set GODOT_BIN, or godot.bin in config/local.yaml, or put `godot` in PATH",
            )
        ]
    full = godot.read_version(bin_path)
    if not full:
        return [Check("godot", "fail", f"{bin_path}: cannot read version")]
    if not godot.version_matches(full, cfg.godot.version):
        return [
            Check(
                "godot",
                "fail",
                f"{full} at {bin_path}",
                f"pinned version is {cfg.godot.version} (godot.version in config/harness.yaml)",
            )
        ]
    checks = [Check("godot", "ok", f"{full} at {bin_path}")]

    templates = godot_export_templates_dir(godot.template_version(full))
    missing = [name for name in REQUIRED_WEB_TEMPLATES if not (templates / name).is_file()]
    if missing:
        checks.append(
            Check(
                "export templates",
                "fail",
                f"missing in {templates}: {', '.join(missing)}",
                f"install export templates for Godot {godot.template_version(full)}",
            )
        )
    else:
        checks.append(Check("export templates", "ok", str(templates)))
    return checks


def check_display() -> Check:
    from harness.platform.display import find_display

    display = find_display()
    if not display.available:
        return Check("display", "warn", f"{display.detail}: screenshots unavailable", "install xvfb")
    if sys.platform == "linux" and not display.prefix:
        return Check("display", "warn", f"no xvfb-run, will use {display.detail}", "install xvfb")
    return Check("display", "ok", display.detail if display.prefix else "windowed capture (no Xvfb needed on this OS)")


USERNS_RESTRICT = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")


def check_sandbox(cfg: Config) -> Check:
    if not cfg.raw.get("sandbox", True):
        return Check("bash sandbox", "warn", "disabled in config (sandbox: false): isolation relies on permissions only")
    if sys.platform == "win32":
        return Check(
            "bash sandbox",
            "warn",
            "not available on native Windows; isolation relies on permissions only",
            "optional: run the Harness in WSL2",
        )
    if sys.platform == "darwin":
        return Check("bash sandbox", "ok", "seatbelt (built into macOS)")
    missing = [tool for tool in ("bwrap", "socat") if not shutil.which(tool)]
    if missing:
        return Check("bash sandbox", "fail", f"missing: {', '.join(missing)}", "install bubblewrap and socat, or set sandbox: false")
    try:
        restricted = USERNS_RESTRICT.read_text(encoding="utf-8").strip() == "1"
    except OSError:
        restricted = False
    if restricted:
        # Ubuntu's AppArmor profile lets bwrap create a user namespace but not a nested one,
        # and Claude Code applies seccomp from a nested one: every sandboxed Bash call fails.
        return Check(
            "bash sandbox",
            "fail",
            "kernel.apparmor_restrict_unprivileged_userns=1 breaks the Claude Code sandbox (nested user namespace)",
            "set sandbox: false in config/local.yaml, or allow unprivileged user namespaces",
        )
    return Check("bash sandbox", "ok", "bubblewrap + socat")


def check_ffmpeg() -> Check:
    path, version = _tool_version("ffmpeg", ["-version"])
    if not path:
        return Check("ffmpeg", "warn", "not found (optional: gameplay video)", "install ffmpeg")
    return Check("ffmpeg", "ok", version.split(" Copyright")[0])


def check_playwright() -> Check:
    if importlib.util.find_spec("playwright") is None:
        return Check("playwright", "warn", "not installed (needed from stage 4: web checks)")
    return Check("playwright", "ok", "python package installed")


def check_workspace(cfg: Config) -> Check:
    target = cfg.workspace
    probe = target if target.exists() else next((p for p in target.parents if p.exists()), None)
    if probe is None or not os.access(probe, os.W_OK):
        return Check("workspace", "fail", f"{target} is not writable", "set HARNESS_WORKSPACE or workspace in config")
    return Check("workspace", "ok", str(target))


def run_checks(cfg: Config) -> list[Check]:
    groups: list[Callable[[], Check | list[Check]]] = [
        check_python,
        check_git,
        lambda: check_claude(cfg),
        lambda: check_godot(cfg),
        check_display,
        lambda: check_sandbox(cfg),
        check_ffmpeg,
        check_playwright,
        lambda: check_workspace(cfg),
    ]
    checks: list[Check] = []
    for group in groups:
        try:
            result = group()
        except (OSError, ValueError) as exc:  # a broken tool must not crash the whole report
            result = Check(getattr(group, "__name__", "check"), "fail", f"error: {exc}")
        checks.extend(result if isinstance(result, list) else [result])
    return checks


_LABELS = {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]"}


def format_report(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    lines = []
    for c in checks:
        lines.append(f"{_LABELS[c.status]} {c.name.ljust(width)}  {c.detail}")
        if c.hint and c.status != "ok":
            lines.append(f"       {' ' * width}  -> {c.hint}")
    fails = sum(c.status == "fail" for c in checks)
    warns = sum(c.status == "warn" for c in checks)
    lines.append("")
    lines.append(f"{fails} failed, {warns} warnings" if fails or warns else "all checks passed")
    return "\n".join(lines)
