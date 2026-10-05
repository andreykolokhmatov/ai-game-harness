import json
import os
import socket
import subprocess
import sys

import pytest

from harness.state.events import CorruptLogError, EventLog
from harness.state.lock import LockedError, ProjectLock
from harness.state.projection import fold, load_state
from harness.state.snapshot import write_json_atomic


def test_append_assigns_seq_and_survives_reopen(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.append("PROJECT_CREATED", {"idea": "платформер"}, project="game_001")
    log.append("STATE_ENTERED", {"state": "SPEC"}, project="game_001")
    reopened = EventLog(path)
    assert reopened.last_seq == 2
    event = reopened.append("BLOCKED", {"reason": "x"})
    assert event["seq"] == 3
    assert [e["type"] for e in reopened.read()] == ["PROJECT_CREATED", "STATE_ENTERED", "BLOCKED"]
    assert reopened.read()[0]["data"]["idea"] == "платформер"


def test_torn_last_line_is_dropped(tmp_path):
    path = tmp_path / "events.jsonl"
    EventLog(path).append("PROJECT_CREATED", {"idea": "x"})
    with path.open("ab") as f:
        f.write(b'{"seq": 2, "type": "STATE_ENTER')  # crash mid-write
    log = EventLog(path)
    assert log.last_seq == 1
    log.append("STATE_ENTERED", {"state": "SPEC"})
    assert [e["seq"] for e in log.read()] == [1, 2]


def test_corrupt_middle_line_is_an_error(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"seq":1,"type":"A","data":{}}\nnot json\n{"seq":3,"type":"B","data":{}}\n', encoding="utf-8")
    with pytest.raises(CorruptLogError, match=":2:"):
        EventLog(path)


def test_unknown_context_field_rejected(tmp_path):
    with pytest.raises(ValueError):
        EventLog(tmp_path / "e.jsonl").append("X", typo_field=1)


def test_fold_tracks_steps_usage_and_checkpoints(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append("PROJECT_CREATED", {"idea": "snake"}, project="game_001")
    log.append("STATE_ENTERED", {"state": "MILESTONE", "milestone": "m1"})
    log.append("STEP_STARTED", {"start_commit": "aaa"}, step_id="m1.impl.1")
    log.append("AGENT_STARTED", {"session_id": "s-1"}, step_id="m1.impl.1", run_id="r-0001")
    state = fold(log.read())
    assert state["open_step"]["session_id"] == "s-1"
    assert state["open_step"]["start_commit"] == "aaa"

    usage = {"claude-sonnet-5-5": {"input_tokens": 10, "output_tokens": 5, "is_sub": True}}
    log.append("AGENT_FINISHED", {"status": "ok", "num_turns": 7, "usage_by_model": usage, "cost_usd_estimate": 0.5})
    log.append("AGENT_FINISHED", {"status": "timeout", "num_turns": 3, "usage_by_model": usage})
    log.append("STEP_FINISHED", {"status": "ok"}, step_id="m1.impl.1")
    log.append("CHECKPOINT_CREATED", {"tag": "cp/prototype", "commit": "bbb"})
    state = fold(log.read())
    assert state["open_step"] is None
    assert state["state"] == "MILESTONE" and state["milestone"] == "m1"
    assert state["counters"]["agent_runs"] == 2
    assert state["counters"]["agent_failures"] == 1
    assert state["counters"]["turns"] == 10
    assert state["counters"]["tokens_by_model"]["claude-sonnet-5-5"] == {"input_tokens": 20, "output_tokens": 10}
    assert state["last_checkpoint"] == {"tag": "cp/prototype", "commit": "bbb"}
    assert state["last_seq"] == 8


def test_load_state_rebuilds_stale_snapshot(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    snap = tmp_path / "state.json"
    log.append("PROJECT_CREATED", {"idea": "x"}, project="p")
    assert load_state(log, snap)["state"] == "CREATED"
    log.append("STATE_ENTERED", {"state": "SPEC"})
    assert load_state(log, snap)["state"] == "SPEC"  # snapshot had last_seq 1, rebuilt
    assert json.loads(snap.read_text(encoding="utf-8"))["last_seq"] == 2
    snap.write_text("{broken", encoding="utf-8")
    assert load_state(log, snap)["state"] == "SPEC"


def test_write_json_atomic_leaves_no_tmp(tmp_path):
    target = tmp_path / "a" / "s.json"
    write_json_atomic(target, {"k": "в"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"k": "в"}
    assert list(target.parent.iterdir()) == [target]


def test_lock_excludes_second_holder(tmp_path):
    path = tmp_path / ".lock"
    with ProjectLock(path):
        with pytest.raises(LockedError):
            ProjectLock(path).acquire()
    assert not path.exists()


def test_stale_lock_from_dead_process_is_taken(tmp_path):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    path = tmp_path / ".lock"
    path.write_text(json.dumps({"pid": dead.pid, "host": socket.gethostname()}), encoding="utf-8")
    with ProjectLock(path):
        assert json.loads(path.read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_lock_from_other_host_is_respected(tmp_path):
    path = tmp_path / ".lock"
    path.write_text(json.dumps({"pid": 1, "host": "other-machine"}), encoding="utf-8")
    with pytest.raises(LockedError):
        ProjectLock(path).acquire()
