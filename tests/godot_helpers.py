"""Shared helpers for tests that need a real Godot binary (skipped without one)."""

import shutil
from pathlib import Path

import pytest

from harness.config import DEFAULT_CONFIG_DIR, load_config
from harness.platform.display import find_display
from harness.verify import godot as godot_mod

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "godot"


def godot_bin() -> Path | None:
    try:
        return godot_mod.resolve_bin(load_config(DEFAULT_CONFIG_DIR).godot)
    except Exception:
        return None


needs_godot = pytest.mark.skipif(godot_bin() is None, reason="Godot binary not available")
needs_display = pytest.mark.skipif(not find_display().available, reason="no display for rendering")


def copy_game(source: Path, target: Path) -> Path:
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(".godot", "*.uid"))
    return target
