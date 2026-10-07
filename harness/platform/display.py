"""How to give Godot a display for rendering (screenshots). OS-specific on purpose.

Linux: a virtual X server through xvfb-run (software Mesa, no GPU needed), even
when a desktop session exists, so no windows pop up. Windows and macOS: a real
window; it appears briefly while a scenario runs.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass

XVFB_SCREEN = "1920x1080x24"


@dataclass(frozen=True)
class Display:
    available: bool
    prefix: list[str]  # command prefix for Godot, e.g. ["xvfb-run", ...]
    detail: str


def find_display(environ: dict[str, str] | None = None) -> Display:
    import os

    env = os.environ if environ is None else environ
    if sys.platform != "linux":
        return Display(True, [], "window")
    xvfb_run = shutil.which("xvfb-run")
    if xvfb_run:
        return Display(True, [xvfb_run, "-a", "-s", f"-screen 0 {XVFB_SCREEN}"], "xvfb-run")
    if env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"):
        return Display(True, [], f"DISPLAY={env.get('DISPLAY') or env.get('WAYLAND_DISPLAY')}")
    return Display(False, [], "no xvfb-run and no DISPLAY")
