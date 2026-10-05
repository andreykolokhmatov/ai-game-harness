import sys
from pathlib import Path

import pytest

from harness.config import DEFAULT_CONFIG_DIR, load_config
from harness.orchestrator.pipeline import Pipeline, PipelineError
from harness.project import create_project
from harness.runners.mock import MockResponse, MockRunner
from harness.verify.basic import CheckResult, VerifyReport


class SimulatedKill(BaseException):
    """Stands in for the Harness process dying mid-step."""


def kill(_req):
    raise SimulatedKill()


def verify_requires(filename: str, message: str = "ERROR: game.gd is missing"):
    """Fake VERIFY: passes once `filename` exists in the repo."""

    def verify(repo: Path, sha: str, out: Path) -> VerifyReport:
        ok = (repo / filename).exists()
        check = CheckResult("scripts", "pass" if ok else "fail", "ok" if ok else message,
                            errors=[] if ok else [{"message": message, "at": None}])
        return VerifyReport(sha=sha, passed=ok, checks=[check])

    return verify


@pytest.fixture
def cfg(tmp_path):
    return load_config(DEFAULT_CONFIG_DIR, env={"HARNESS_WORKSPACE": str(tmp_path / "ws")})


def make(cfg, runner, verify, clock=None):
    project = create_project(cfg, "simple 2D platformer", "game_001")
    kwargs = {"clock": clock} if clock else {}
    return project, Pipeline(cfg, project, runner, verify, godot_bin=Path(sys.executable), **kwargs)


def types(project):
    return [e["type"] for e in project.log().read()]


def test_happy_path_reaches_human_review(cfg):
    runner = MockRunner([MockResponse(files={"game.gd": "extends Node\n"})])
    project, pipeline = make(cfg, runner, verify_requires("game.gd"))
    st = pipeline.run()
    assert st["state"] == "HUMAN_REVIEW"
    assert st["last_checkpoint"]["tag"] == "cp/prototype"
    repo = project.repo()
    assert repo.tag_sha("cp/prototype") == repo.head()
    assert repo.tag_sha("h/m1.implement.1")
    req = runner.requests[0]
    assert req.model == cfg.roles["engineer"].model_id
    assert "simple 2D platformer" in req.prompt
    assert req.system_append_file.read_text(encoding="utf-8").startswith("# Role: Engineer")
    assert "Bash" in req.tools and "Edit(.claude/**)" in req.disallowed_tools
    assert "GODOT_BIN" in req.env
    assert (project.runs_dir / req.run_id / "request.json").is_file()


def test_fix_loop_gets_failure_digest(cfg):
    runner = MockRunner([MockResponse(files={"other.gd": "x"}), MockResponse(files={"game.gd": "extends Node\n"})])
    project, pipeline = make(cfg, runner, verify_requires("game.gd"))
    st = pipeline.run()
    assert st["state"] == "HUMAN_REVIEW"
    assert st["attempt"] == 2
    fix_prompt = runner.requests[1].prompt
    assert "game.gd is missing" in fix_prompt
    assert types(project).count("VERIFY_FINISHED") == 2


def test_circuit_breaker_blocks_on_same_failure(cfg):
    runner = MockRunner([MockResponse(files={f"f{i}.txt": str(i)}) for i in range(5)])
    project, pipeline = make(cfg, runner, verify_requires("never.gd", "ERROR: same thing at line 12"))
    st = pipeline.run()
    assert st["state"] == "BLOCKED"
    assert "circuit breaker" in st["blocked_reason"]
    assert len(runner.requests) == 3


def test_fix_attempts_exhausted(cfg):
    runner = MockRunner([MockResponse(files={f"f{i}.txt": str(i)}) for i in range(6)])
    counter = {"n": 0}

    def verify(repo, sha, out):
        counter["n"] += 1
        msg = f"ERROR: failure kind {'abcdef'[counter['n']]}"  # different every time: breaker stays closed
        return VerifyReport(sha, False, [CheckResult("smoke", "fail", msg, errors=[{"message": msg, "at": None}])])

    project, pipeline = make(cfg, runner, verify)
    st = pipeline.run()
    assert st["state"] == "BLOCKED"
    assert "fix attempts" in st["blocked_reason"]
    assert len(runner.requests) == 1 + 4


def test_kill_mid_step_resumes_same_session(cfg):
    first = MockRunner([MockResponse(files={"half.gd": "x"}, side_effect=kill)])
    project, pipeline = make(cfg, first, verify_requires("game.gd"))
    with pytest.raises(SimulatedKill):
        pipeline.run()
    killed_session = first.requests[0].session_id
    assert project.state()["open_step"]["session_id"] == killed_session
    assert not project.repo().is_clean()

    second = MockRunner([MockResponse(files={"game.gd": "extends Node\n"})])
    st = Pipeline(cfg, project, second, verify_requires("game.gd"), godot_bin=Path(sys.executable)).run()
    assert st["state"] == "HUMAN_REVIEW"
    assert second.requests[0].resume_session_id == killed_session
    assert "interrupted" in second.requests[0].prompt
    assert (project.repo_dir / "half.gd").exists()  # resumed on top of the interrupted work
    assert st["attempt"] == 1  # the interrupted run is not an attempt


def test_second_kill_resets_to_step_start(cfg):
    first = MockRunner([MockResponse(files={"a.gd": "x"}, side_effect=kill)])
    project, pipeline = make(cfg, first, verify_requires("game.gd"))
    with pytest.raises(SimulatedKill):
        pipeline.run()
    second = MockRunner([MockResponse(files={"b.gd": "x"}, side_effect=kill)])
    with pytest.raises(SimulatedKill):
        Pipeline(cfg, project, second, verify_requires("game.gd"), godot_bin=Path(sys.executable)).run()

    third = MockRunner([MockResponse(files={"game.gd": "extends Node\n"})])
    st = Pipeline(cfg, project, third, verify_requires("game.gd"), godot_bin=Path(sys.executable)).run()
    assert st["state"] == "HUMAN_REVIEW"
    assert third.requests[0].resume_session_id is None  # fresh session after the reset
    assert not (project.repo_dir / "a.gd").exists() and not (project.repo_dir / "b.gd").exists()


def test_usage_limit_pauses_until_reset(cfg):
    now = {"t": 1000.0}
    runner = MockRunner([MockResponse(status="usage_limit"), MockResponse(files={"game.gd": "extends Node\n"})])
    runner._responses[0].text = None
    project, pipeline = make(cfg, runner, verify_requires("game.gd"), clock=lambda: now["t"])
    # MockResponse has no rate_limit field: patch the result through a wrapper runner.
    original = runner.run

    def run_with_reset(req):
        result = original(req)
        if result.status == "usage_limit":
            result.rate_limit = {"status": "rejected", "resetsAt": 5000}
        return result

    runner.run = run_with_reset
    st = pipeline.run()
    assert st["state"] == "PAUSED" and st["paused"]["resets_at"] == 5000
    assert pipeline.run()["state"] == "PAUSED"  # still before the reset
    now["t"] = 6000.0
    st = pipeline.run()
    assert st["state"] == "HUMAN_REVIEW"
    assert st["attempt"] == 1  # the limited run did not count


def test_manual_changes_stop_the_pipeline(cfg):
    runner = MockRunner([MockResponse()])
    project, pipeline = make(cfg, runner, verify_requires("game.gd"))
    (project.repo_dir / "manual.txt").write_text("edited by hand", encoding="utf-8")
    with pytest.raises(PipelineError, match="uncommitted changes"):
        pipeline.run()
    assert not project.lock_path.exists()


def test_revise_after_human_review(cfg):
    from harness.orchestrator.pipeline import request_revision

    runner = MockRunner([MockResponse(files={"game.gd": "v1"}), MockResponse(files={"game.gd": "v2"})])
    project, pipeline = make(cfg, runner, verify_requires("game.gd"))
    assert pipeline.run()["state"] == "HUMAN_REVIEW"
    with pytest.raises(PipelineError):
        request_revision(project, "   ")
    request_revision(project, "hide touch buttons on desktop")
    st = pipeline.run()
    assert st["state"] == "HUMAN_REVIEW"
    assert "hide touch buttons on desktop" in runner.requests[1].prompt
    assert st["attempt"] == 1
    assert project.repo().tag_sha("h/m1.revise.1")
    assert (project.repo_dir / "game.gd").read_text(encoding="utf-8") == "v2"


def test_revise_only_at_human_review(cfg):
    from harness.orchestrator.pipeline import request_revision

    project, _ = make(cfg, MockRunner([]), verify_requires("game.gd"))
    with pytest.raises(PipelineError, match="only at HUMAN_REVIEW"):
        request_revision(project, "change it")
