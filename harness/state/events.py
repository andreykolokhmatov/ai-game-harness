"""Append-only event log (events.jsonl), the source of truth for project state.

One JSON object per line. Each append is flushed and fsynced. A torn last line
(the process died mid-write) is cut off on open; a corrupt line in the middle
means the log was damaged by something else and is an error.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Context fields that may sit next to `data` at the top level of an event.
CONTEXT_FIELDS = ("project", "state", "milestone", "step_id", "run_id")


class CorruptLogError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_lines(raw: bytes, path: Path) -> list[dict[str, Any]]:
    events = []
    for lineno, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CorruptLogError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
        if not isinstance(event, dict) or "seq" not in event or "type" not in event:
            raise CorruptLogError(f"{path}:{lineno}: not an event")
        events.append(event)
    return events


class EventLog:
    def __init__(self, path: Path):
        self.path = path
        self._repair_tail()
        events = self.read()
        self._last_seq = events[-1]["seq"] if events else 0

    @property
    def last_seq(self) -> int:
        return self._last_seq

    def _repair_tail(self) -> None:
        """Cut a torn last line so the next append starts on a fresh line."""
        if not self.path.exists():
            return
        raw = self.path.read_bytes()
        if not raw or raw.endswith(b"\n"):
            return
        keep = raw.rfind(b"\n") + 1
        log.warning("%s: dropping torn last line (%d bytes)", self.path, len(raw) - keep)
        with self.path.open("r+b") as f:
            f.truncate(keep)
            f.flush()
            os.fsync(f.fileno())

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return _parse_lines(self.path.read_bytes(), self.path)

    def append(self, type: str, data: dict[str, Any] | None = None, **context: Any) -> dict[str, Any]:
        unknown = set(context) - set(CONTEXT_FIELDS)
        if unknown:
            raise ValueError(f"unknown event context fields: {sorted(unknown)}")
        event: dict[str, Any] = {"seq": self._last_seq + 1, "ts": _now(), "type": type}
        event.update({k: v for k, v in context.items() if v is not None})
        event["data"] = data or {}
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        self._last_seq = event["seq"]
        return event
