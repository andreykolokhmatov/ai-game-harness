"""Fold the event log into the current project state (state.json).

state.json is only a cache: if its last_seq differs from the log, it is rebuilt.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable

from harness.state.events import EventLog
from harness.state.snapshot import read_json, write_json_atomic

State = dict[str, Any]
Event = dict[str, Any]

INITIAL_STATE: State = {
    "last_seq": 0,
    "project": None,
    "idea": None,
    "created_at": None,
    "state": None,
    "milestone": None,
    "open_step": None,
    "last_checkpoint": None,
    "blocked_reason": None,
    "counters": {
        "steps_finished": 0,
        "agent_runs": 0,
        "agent_failures": 0,
        "turns": 0,
        "agent_duration_s": 0.0,
        "cost_usd_estimate": 0.0,  # reference only: work runs on a subscription
        "tokens_by_model": {},
    },
}


def _project_created(s: State, e: Event) -> None:
    s["project"] = e.get("project")
    s["idea"] = e["data"].get("idea")
    s["created_at"] = e["ts"]
    s["state"] = "CREATED"


def _state_entered(s: State, e: Event) -> None:
    s["state"] = e["data"]["state"]
    if "milestone" in e["data"]:
        s["milestone"] = e["data"]["milestone"]
    if s["state"] != "BLOCKED":
        s["blocked_reason"] = None


def _step_started(s: State, e: Event) -> None:
    s["open_step"] = {
        "step_id": e["step_id"],
        "started_seq": e["seq"],
        "start_commit": e["data"].get("start_commit"),
        "session_id": None,
        "interruptions": e["data"].get("interruptions", 0),
    }


def _step_finished(s: State, e: Event) -> None:
    s["open_step"] = None
    s["counters"]["steps_finished"] += 1


def _agent_started(s: State, e: Event) -> None:
    if s["open_step"] is not None:
        s["open_step"]["session_id"] = e["data"].get("session_id")
        s["open_step"]["run_id"] = e.get("run_id")


def _add_usage(target: dict[str, dict[str, int]], usage_by_model: dict[str, dict[str, Any]]) -> None:
    for model, usage in usage_by_model.items():
        bucket = target.setdefault(model, {})
        for key, value in usage.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                bucket[key] = bucket.get(key, 0) + value


def _agent_finished(s: State, e: Event) -> None:
    c = s["counters"]
    d = e["data"]
    c["agent_runs"] += 1
    if d.get("status") != "ok":
        c["agent_failures"] += 1
    c["turns"] += d.get("num_turns") or 0
    c["agent_duration_s"] += d.get("duration_s") or 0.0
    c["cost_usd_estimate"] += d.get("cost_usd_estimate") or 0.0
    _add_usage(c["tokens_by_model"], d.get("usage_by_model") or {})


def _checkpoint_created(s: State, e: Event) -> None:
    s["last_checkpoint"] = {"tag": e["data"]["tag"], "commit": e["data"]["commit"]}


def _rollback(s: State, e: Event) -> None:
    s["open_step"] = None
    s["state"] = e["data"].get("state", s["state"])
    s["milestone"] = e["data"].get("milestone", s["milestone"])


def _blocked(s: State, e: Event) -> None:
    s["state"] = "BLOCKED"
    s["blocked_reason"] = e["data"].get("reason")


REDUCERS: dict[str, Callable[[State, Event], None]] = {
    "PROJECT_CREATED": _project_created,
    "STATE_ENTERED": _state_entered,
    "STEP_STARTED": _step_started,
    "STEP_FINISHED": _step_finished,
    "AGENT_STARTED": _agent_started,
    "AGENT_FINISHED": _agent_finished,
    "CHECKPOINT_CREATED": _checkpoint_created,
    "ROLLBACK": _rollback,
    "BLOCKED": _blocked,
}


def fold(events: list[Event]) -> State:
    state = copy.deepcopy(INITIAL_STATE)
    for event in events:
        reducer = REDUCERS.get(event["type"])
        if reducer:
            reducer(state, event)
        state["last_seq"] = event["seq"]
    return state


def load_state(log: EventLog, snapshot_path: Path) -> State:
    """Current state from the snapshot, rebuilt from the log if stale or missing."""
    snapshot = read_json(snapshot_path)
    if isinstance(snapshot, dict) and snapshot.get("last_seq") == log.last_seq:
        return snapshot
    state = fold(log.read())
    write_json_atomic(snapshot_path, state)
    return state
