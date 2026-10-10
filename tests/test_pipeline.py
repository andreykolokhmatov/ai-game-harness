import sys
from pathlib import Path

import pytest

from harness.config import DEFAULT_CONFIG_DIR, load_config
from harness.orchestrator import planner
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
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.md").write_text(f"report for {sha}\n", encoding="utf-8")
        check = CheckResult("scripts", "pass" if ok else "fail", "ok" if ok else message,
                            errors=[] if ok else [{"message": message, "at": None}])
        return VerifyReport(sha=sha, passed=ok, checks=[check])

    return verify


@pytest.fixture
def cfg(tmp_path):
    """Pipeline without the Evaluator: a green verify accepts the milestone."""
    config = load_config(DEFAULT_CONFIG_DIR, env={"HARNESS_WORKSPACE": str(tmp_path / "ws")})
    config.raw.setdefault("gates", {})["evaluator"] = False
    return config


@pytest.fixture
def cfg_eval(tmp_path):
    return load_config(DEFAULT_CONFIG_DIR, env={"HARNESS_WORKSPACE": str(tmp_path / "ws")})


RAW_PLAN = {  # what the Planner returns: no ids, criteria nested in milestones
    "title": "Hop",
    "dimension": "2d",
    "gdd": "# Hop\n\nJump over gaps.",
    "contract": {
        "input_actions": ["jump"],
        "state": [{"key": "scene", "type": "string", "description": "screen"},
                  {"key": "score", "type": "int", "description": "points"}],
        "commands": [{"name": "kill_player", "args": [], "description": "lose a life"}],
    },
    "milestones": [
        {"title": "Prototype", "goal": "jump over one gap",
         "criteria": [{"description": "jump works", "verify": "press jump, y decreases"}]},
        {"title": "Polish", "goal": "three levels",
         "criteria": [{"description": "three levels", "verify": "set_level 3"}]},
    ],
}
PLAN = planner.normalize(RAW_PLAN)


def make(cfg, runner, verify, clock=None, planned=True):
    """A project at MILESTONE m1 (the plan already accepted), unless planned=False."""
    project = create_project(cfg, "simple 2D platformer", "game_001")
    kwargs = {"clock": clock} if clock else {}
    pipeline = Pipeline(cfg, project, runner, verify, godot_bin=Path(sys.executable), **kwargs)
    if planned:
        pipeline.accept_plan(PLAN)
    return project, pipeline


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
    assert "jump over one gap" in req.prompt and "AC-1: jump works" in req.prompt
    assert "AC-2" not in req.prompt  # only the criteria of this milestone
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
    # The engineer gets a writable scratch dir holding a copy of the last report.
    last_report = project.scratch_dir / "last_report"
    assert runner.requests[1].add_dirs == [project.scratch_dir]
    assert last_report.as_posix() in fix_prompt
    assert (last_report / "report.md").read_text(encoding="utf-8").startswith("report for ")
    assert "scenario_runner.gd" in runner.requests[1].system_append_file.read_text(encoding="utf-8")


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


def test_planner_writes_documents_and_starts_m1(cfg):
    runner = MockRunner([MockResponse(structured_output=RAW_PLAN), MockResponse(files={"game.gd": "extends Node\n"})])
    project, pipeline = make(cfg, runner, verify_requires("game.gd"), planned=False)
    st = pipeline.run()
    assert st["state"] == "HUMAN_REVIEW" and st["milestone"] == "m1"
    assert st["plan"] == {"title": "Hop", "milestones": ["m1", "m2"]}
    plan_req = runner.requests[0]
    assert plan_req.role == "planner" and plan_req.json_schema is not None
    assert "simple 2D platformer" in plan_req.prompt
    assert "Bash" not in plan_req.tools and "Write" not in plan_req.tools
    docs = project.repo_dir / "docs"
    for name in ("GDD.md", "ACCEPTANCE.yaml", "CONTRACT.yaml", "PLAN.md"):
        assert (docs / name).is_file() and (project.artifacts_dir / name).is_file()
    assert "kill_player" in (docs / "CONTRACT.yaml").read_text(encoding="utf-8")
    assert project.repo().tag_sha("cp/plan")


def test_invalid_plan_is_retried_with_the_problems(cfg):
    bad = {**RAW_PLAN, "milestones": RAW_PLAN["milestones"] * 2}  # 4 milestones, at most 3
    runner = MockRunner([MockResponse(structured_output=bad), MockResponse(structured_output=RAW_PLAN),
                         MockResponse(files={"game.gd": "x"})])
    project, pipeline = make(cfg, runner, verify_requires("game.gd"), planned=False)
    assert pipeline.run()["state"] == "HUMAN_REVIEW"
    assert "at most 3 are allowed" in runner.requests[1].prompt
    assert types(project).count("PLAN_REJECTED") == 1


def test_planner_gives_up_after_two_bad_plans(cfg):
    runner = MockRunner([MockResponse(structured_output=None), MockResponse(structured_output=None)])
    project, pipeline = make(cfg, runner, verify_requires("game.gd"), planned=False)
    st = pipeline.run()
    assert st["state"] == "BLOCKED" and "valid plan" in st["blocked_reason"]


def test_kill_during_planning_reruns_the_planner(cfg):
    first = MockRunner([MockResponse(structured_output=RAW_PLAN, side_effect=kill)])
    project, pipeline = make(cfg, first, verify_requires("game.gd"), planned=False)
    with pytest.raises(SimulatedKill):
        pipeline.run()
    second = MockRunner([MockResponse(structured_output=RAW_PLAN), MockResponse(files={"game.gd": "x"})])
    st = Pipeline(cfg, project, second, verify_requires("game.gd"), godot_bin=Path(sys.executable)).run()
    assert st["state"] == "HUMAN_REVIEW"
    assert second.requests[0].role == "planner" and second.requests[0].resume_session_id is None


def test_approve_runs_the_next_milestone_to_done(cfg):
    from harness.orchestrator.pipeline import approve

    runner = MockRunner([MockResponse(files={"game.gd": "v1"}), MockResponse(files={"game.gd": "v2"})])
    project, pipeline = make(cfg, runner, verify_requires("game.gd"))
    assert pipeline.run()["state"] == "HUMAN_REVIEW"
    assert approve(project) == "MILESTONE m2"
    st = pipeline.run()
    assert st["state"] == "DONE" and st["milestone"] == "m2"
    assert "three levels" in runner.requests[1].prompt and "Earlier milestones are done" in runner.requests[1].prompt
    assert project.repo().tag_sha("cp/m2") == project.repo().head()
    with pytest.raises(PipelineError, match="only at HUMAN_REVIEW"):
        approve(project)


def eval_report(status="pass", verdict=None, issues=()):
    return {"verdict": verdict or ("PASS" if status == "pass" and not issues else "FAIL"), "summary": "s",
            "criteria": [{"id": "AC-1", "status": status, "evidence": "scenario jump"}], "issues": list(issues)}


BUG = {"severity": "major", "category": "controls", "description": "jump ignores touch",
       "reproduction": "tap the jump button", "expected": "player jumps", "actual": "nothing", "evidence": "shot"}


def test_evaluator_accepts_the_milestone(cfg_eval):
    runner = MockRunner([MockResponse(files={"game.gd": "x"}), MockResponse(structured_output=eval_report())])
    project, pipeline = make(cfg_eval, runner, verify_requires("game.gd"))
    st = pipeline.run()
    assert st["state"] == "HUMAN_REVIEW" and st["last_eval"]["passed"]
    req = runner.requests[1]
    eval_dir = project.harness_dir / "eval" / st["last_verify"]["sha"][:12]
    assert req.role == "evaluator" and req.cwd == eval_dir and req.json_schema is not None
    assert "AC-1: jump works" in req.prompt and "game.gd" in req.prompt  # criteria and the milestone diff
    assert "Write(./scenarios/**)" in req.allowed_tools and "Write(./game/**)" in req.disallowed_tools
    assert (project.harness_dir / st["last_verify"]["report"] / "eval.json").is_file()
    assert not (eval_dir / "game").exists()  # the game copy is removed after the evaluation


def test_evaluator_rejection_goes_to_the_engineer(cfg_eval):
    runner = MockRunner([
        MockResponse(files={"game.gd": "v1"}),
        MockResponse(structured_output=eval_report("fail", issues=[BUG])),
        MockResponse(files={"game.gd": "v2"}),
        MockResponse(structured_output=eval_report()),
    ])
    project, pipeline = make(cfg_eval, runner, verify_requires("game.gd"))
    st = pipeline.run()
    assert st["state"] == "HUMAN_REVIEW" and st["attempt"] == 2
    fix = runner.requests[2].prompt
    assert "AC-1 fail: jump works" in fix and "tap the jump button" in fix and "eval.json" in fix
    assert (project.scratch_dir / "last_report" / "eval.json").is_file()


def test_inconclusive_criterion_is_not_a_pass(cfg_eval):
    runner = MockRunner([MockResponse(files={"game.gd": "v1"}),
                         MockResponse(structured_output=eval_report("inconclusive", verdict="PASS")),
                         MockResponse(files={"game.gd": "v2"}), MockResponse(structured_output=eval_report())])
    project, pipeline = make(cfg_eval, runner, verify_requires("game.gd"))
    assert pipeline.run()["state"] == "HUMAN_REVIEW"
    ev = [e for e in project.log().read() if e["type"] == "EVAL_FINISHED"]
    assert not ev[0]["data"]["passed"] and ev[0]["data"]["reasons"] == ["AC-1: inconclusive"]


def test_evaluator_without_report_blocks_without_costing_attempts(cfg_eval):
    runner = MockRunner([MockResponse(files={"game.gd": "x"}), MockResponse(structured_output=None),
                         MockResponse(structured_output=None)])
    project, pipeline = make(cfg_eval, runner, verify_requires("game.gd"))
    st = pipeline.run()
    assert st["state"] == "BLOCKED" and st["blocked_reason"].startswith("evaluator_failure")
    assert st["attempt"] == 1


def test_same_evaluator_finding_opens_the_circuit_breaker(cfg_eval):
    responses = []
    for i in range(3):
        responses += [MockResponse(files={"game.gd": f"v{i}"}), MockResponse(structured_output=eval_report("fail", issues=[BUG]))]
    project, pipeline = make(cfg_eval, MockRunner(responses), verify_requires("game.gd"))
    st = pipeline.run()
    assert st["state"] == "BLOCKED" and "circuit breaker" in st["blocked_reason"]
