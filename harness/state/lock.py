"""Per-project lock: only one Harness process works on a project at a time."""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path

from harness.platform.proc import pid_alive


class LockedError(Exception):
    pass


class ProjectLock:
    def __init__(self, path: Path):
        self.path = path
        self._held = False

    def _owner(self) -> dict | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _is_stale(self, owner: dict | None) -> bool:
        if owner is None:
            return True  # empty or garbage lock file: the writer died mid-write
        if owner.get("host") != socket.gethostname():
            return False  # cannot check a pid on another machine
        return not pid_alive(int(owner.get("pid", -1)))

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        info = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "since": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                owner = self._owner()
                if not self._is_stale(owner):
                    raise LockedError(f"project is locked by {owner} ({self.path})") from None
                self.path.unlink(missing_ok=True)
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(info, f)
            self._held = True
            return
        raise LockedError(f"cannot acquire {self.path}")

    def release(self) -> None:
        if self._held:
            self.path.unlink(missing_ok=True)
            self._held = False

    def __enter__(self) -> ProjectLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
