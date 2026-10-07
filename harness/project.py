"""Project layout in the workspace and `harness create`."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from harness.config import REPO_ROOT, Config
from harness.gitops.checkpoints import GameRepo
from harness.state.events import EventLog
from harness.state.projection import State, load_state

TEMPLATE_DIR = REPO_ROOT / "templates" / "godot"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class ProjectError(Exception):
    pass


@dataclass(frozen=True)
class Project:
    name: str
    root: Path

    @property
    def repo_dir(self) -> Path:
        return self.root / "repo"

    @property
    def harness_dir(self) -> Path:
        return self.root / "harness"

    @property
    def events_path(self) -> Path:
        return self.harness_dir / "events.jsonl"

    @property
    def state_path(self) -> Path:
        return self.harness_dir / "state.json"

    @property
    def lock_path(self) -> Path:
        return self.harness_dir / ".lock"

    @property
    def runs_dir(self) -> Path:
        return self.harness_dir / "runs"

    @property
    def reports_dir(self) -> Path:
        return self.harness_dir / "reports"

    @property
    def scratch_dir(self) -> Path:
        """Writable for agents (--add-dir): throwaway files and a copy of the last verify report."""
        return self.harness_dir / "scratch"

    @property
    def bin_dir(self) -> Path:
        return self.harness_dir / "bin"

    def log(self) -> EventLog:
        return EventLog(self.events_path)

    def state(self) -> State:
        return load_state(self.log(), self.state_path)

    def repo(self) -> GameRepo:
        return GameRepo(self.repo_dir)


def open_project(cfg: Config, name: str) -> Project:
    project = Project(name, cfg.workspace / name)
    if not project.events_path.is_file():
        raise ProjectError(f"project '{name}' not found in {cfg.workspace}")
    return project


def next_name(workspace: Path) -> str:
    taken = {p.name for p in workspace.iterdir()} if workspace.is_dir() else set()
    n = 1
    while f"game_{n:03d}" in taken:
        n += 1
    return f"game_{n:03d}"


def create_project(cfg: Config, idea: str, name: str | None = None) -> Project:
    idea = idea.strip()
    if not idea:
        raise ProjectError("idea must not be empty")
    name = name or next_name(cfg.workspace)
    if not _NAME_RE.match(name):
        raise ProjectError(f"invalid project name '{name}': use lowercase letters, digits, '_' and '-'")
    project = Project(name, cfg.workspace / name)
    if project.root.exists():
        raise ProjectError(f"{project.root} already exists")

    shutil.copytree(TEMPLATE_DIR, project.repo_dir)
    project.harness_dir.mkdir(parents=True)
    (project.harness_dir / "artifacts").mkdir()
    (project.harness_dir / "artifacts" / "idea.md").write_text(idea + "\n", encoding="utf-8")

    repo = project.repo()
    repo.init()
    sha = repo.commit_all("chore: new game from Harness template")
    repo.tag("cp/created")

    log = project.log()
    log.append("PROJECT_CREATED", {"idea": idea, "template_commit": sha}, project=name)
    log.append("CHECKPOINT_CREATED", {"tag": "cp/created", "commit": sha}, project=name)
    project.state()
    return project
