"""Builds agent prompts from agents/*.md with string.Template placeholders."""

from __future__ import annotations

from pathlib import Path
from string import Template

from harness.config import REPO_ROOT

AGENTS_DIR = REPO_ROOT / "agents"


def render(name: str, agents_dir: Path = AGENTS_DIR, **values: object) -> str:
    """Render agents/<name>.md. Unknown placeholders raise KeyError (no silent blanks)."""
    text = (agents_dir / f"{name}.md").read_text(encoding="utf-8")
    return Template(text).substitute({k: str(v) for k, v in values.items()})


def write_system_prompt(role: str, target: Path, agents_dir: Path = AGENTS_DIR, **guide_values: object) -> Path:
    """Role prompt + engine guide, written to a file for --append-system-prompt-file."""
    parts = [render(role, agents_dir), render("engine_guide", agents_dir, **guide_values)]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n\n".join(p.strip() for p in parts) + "\n", encoding="utf-8")
    return target
