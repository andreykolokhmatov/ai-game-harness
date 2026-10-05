"""Git operations on the game repository. The Harness owns history and tags.

Tags: h/<step_id> after each agent step, cp/<name> for gate checkpoints.
"""

from __future__ import annotations

from pathlib import Path

from harness.platform import proc

GIT_TIMEOUT_S = 120
# Identity for commits made by the Harness inside game repos; does not touch user config.
HARNESS_AUTHOR = ("AI Game Harness", "harness@localhost")


class GitError(Exception):
    pass


class GameRepo:
    def __init__(self, path: Path):
        self.path = path

    def git(self, *args: str, check: bool = True) -> str:
        result = proc.run(["git", *args], timeout_s=GIT_TIMEOUT_S, cwd=self.path)
        if check and not result.ok:
            raise GitError(f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
        return result.stdout.strip()

    def init(self) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", HARNESS_AUTHOR[0])
        self.git("config", "user.email", HARNESS_AUTHOR[1])
        self.git("config", "core.autocrlf", "false")

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def is_clean(self) -> bool:
        return self.git("status", "--porcelain") == ""

    def commit_all(self, message: str) -> str | None:
        """Commit every change. Returns the new SHA, or None when there was nothing to commit."""
        self.git("add", "-A")
        if self._nothing_staged():
            return None
        self.git("commit", "-q", "-m", message)
        return self.head()

    def _nothing_staged(self) -> bool:
        result = proc.run(["git", "diff", "--cached", "--quiet"], timeout_s=GIT_TIMEOUT_S, cwd=self.path)
        return result.returncode == 0

    def tag(self, name: str, ref: str = "HEAD") -> None:
        """Lightweight tag; moves an existing tag of the same name."""
        self.git("tag", "-f", name, ref)

    def tag_sha(self, name: str) -> str | None:
        out = self.git("rev-parse", "-q", "--verify", f"refs/tags/{name}^{{commit}}", check=False)
        return out or None

    def reset_hard(self, ref: str) -> None:
        """Return the work tree to ref. Keeps .godot/ (ignored import cache)."""
        self.git("reset", "-q", "--hard", ref)
        self.git("clean", "-q", "-fd")

    def diff_stat(self, base: str, ref: str = "HEAD") -> str:
        return self.git("diff", "--stat", base, ref)
