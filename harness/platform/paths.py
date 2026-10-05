"""OS-specific locations used by Godot."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def godot_data_dir() -> Path:
    """Godot editor data directory (holds export_templates/)."""
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Godot"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Godot"
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "godot"


def godot_export_templates_dir(template_version: str) -> Path:
    """Directory for templates of one engine version, e.g. '4.4.1.stable'."""
    return godot_data_dir() / "export_templates" / template_version
