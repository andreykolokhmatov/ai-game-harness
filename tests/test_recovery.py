"""kill -9 of the Harness in the middle of an Engineer run, with a real agent process (fake claude)."""

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from harness.config import DEFAULT_CONFIG_DIR, load_config
from harness.orchestrator.pipeline import Pipeline
from harness.platform.proc import pid_alive
from harness.project import open_project
from harness.runners.claude_cli import PID_FILE
from harness.runners.mock import MockResponse, MockRunner
from harness.verify.basic import CheckResult, VerifyReport

FAKE = Path(__file__).with_name("fake_claude.py")

HARNESS_SCRIPT = """
import sys
from pathlib import Path
sys.path.insert(0, {tests!r})
from harness.config import DEFAULT_CONFIG_DIR, load_config
from harness.orchestrator.pipeline import Pipeline
from harness.project import create_project
from harness.runners.claude_cli import ClaudeCliRunner
from test_pipeline import PLAN

cfg = load_config(DEFAULT_CONFIG_DIR, env={{"HARNESS_WORKSPACE": {ws!r}}})
cfg.raw["gates"]["evaluator"] = False
cfg.raw["sandbox"] = False
project = create_project(cfg, "simple 2D platformer", "game_001")
pipeline = Pipeline(cfg, project, ClaudeCliRunner([sys.executable, {claude!r}]), verify=None,
                    godot_bin=Path(sys.executable))
pipeline.accept_plan(PLAN)
pipeline.run()
"""


def passing_verify(repo, sha, out):
    out.mkdir(parents=True, exist_ok=True)
    return VerifyReport(sha, True, [CheckResult("smoke", "pass", "ok")])


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals; Windows uses taskkill in the same code path")
def test_kill_9_mid_engineer_stops_the_orphan_and_continues(tmp_path):
    hang = tmp_path / "hang_claude.py"
    hang.write_text(textwrap.dedent(f"""
        import os, runpy
        os.environ["FAKE_CLAUDE_MODE"] = "hang"
        runpy.run_path({str(FAKE)!r}, run_name="__main__")
    """), encoding="utf-8")
    ws = tmp_path / "ws"
    script = HARNESS_SCRIPT.format(tests=str(Path(__file__).parent), ws=str(ws), claude=str(hang))
    harness = subprocess.Popen([sys.executable, "-c", script], cwd=Path(__file__).parent.parent)

    runs = ws / "game_001" / "harness" / "runs"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not list(runs.glob(f"*/{PID_FILE}")):
        time.sleep(0.1)
    pid_files = list(runs.glob(f"*/{PID_FILE}"))
    assert pid_files, "the agent never started"
    agent_pid = int(pid_files[0].read_text())

    os.kill(harness.pid, signal.SIGKILL)
    harness.wait()
    assert pid_alive(agent_pid), "the agent should survive the Harness in its own process group"

    cfg = load_config(DEFAULT_CONFIG_DIR, env={"HARNESS_WORKSPACE": str(ws)})
    cfg.raw["gates"]["evaluator"] = False
    project = open_project(cfg, "game_001")
    assert project.state()["open_step"]["step_id"] == "m1.implement.1"
    runner = MockRunner([MockResponse(files={"game.gd": "extends Node\n"})])
    st = Pipeline(cfg, project, runner, passing_verify, godot_bin=Path(sys.executable)).run()

    assert st["state"] == "HUMAN_REVIEW"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and pid_alive(agent_pid):
        time.sleep(0.1)
    assert not pid_alive(agent_pid)
    events = [e["type"] for e in project.log().read()]
    assert "ORPHAN_STOPPED" in events
    assert runner.requests[0].resume_session_id  # the interrupted session continues
