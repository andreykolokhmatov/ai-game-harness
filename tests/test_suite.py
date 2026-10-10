import json

from godot_helpers import FIXTURES, TEMPLATE, copy_game, godot_bin, needs_display, needs_godot
from harness.cli import main
from harness.config import DEFAULT_CONFIG_DIR, load_config
from harness.platform.display import find_display
from harness.project import create_project
from harness.verify.basic import check_structure
from harness.verify.godot import GodotRunner
from harness.verify.suite import run_verify


def statuses(report):
    return {c.id: c.status for c in report.checks}


def test_structure_reads_project_with_input_section(tmp_path):
    copy_game(FIXTURES / "mover", tmp_path / "game")
    assert check_structure(tmp_path / "game").status == "pass"


@needs_godot
def test_template_passes_headless(tmp_path):
    repo = copy_game(TEMPLATE, tmp_path / "game")
    report = run_verify(GodotRunner(godot_bin()), repo, "sha", tmp_path / "report", display=None)
    assert report.passed, (tmp_path / "report" / "report.md").read_text()
    assert statuses(report) == {
        "structure": "pass", "import": "pass", "scripts": "pass", "smoke": "pass",
        "screens": "skipped", "contract": "pass", "assets": "pass", "scenarios": "pass", "scenario:smoke": "pass",
    }
    data = json.loads((tmp_path / "report" / "verify.json").read_text())
    assert data["passed"] and data["display"] is None


@needs_godot
def test_failing_scenario_and_contract_reach_the_report(tmp_path):
    repo = copy_game(FIXTURES / "mover", tmp_path / "game")
    (repo / "docs" / "CONTRACT.yaml").write_text("input_actions: [move_right, jump]\nstate:\n  taps: int\n")
    (repo / "tests" / "scenarios" / "far.json").write_text(
        json.dumps({"steps": [{"assert": "state.player.position.x > 5000"}]})
    )
    report = run_verify(GodotRunner(godot_bin()), repo, "sha", tmp_path / "report", display=None)
    st = statuses(report)
    assert not report.passed
    assert st["contract"] == "fail" and st["scenario:far"] == "fail" and st["scenario:move"] == "pass"
    md = (tmp_path / "report" / "report.md").read_text()
    assert "input action 'jump' is not defined" in md
    assert 'step 0 {"assert": "state.player.position.x > 5000"}' in md
    assert "file: `tests/scenarios/far.json`" in md


@needs_godot
def test_broken_scripts_skip_runtime_checks(tmp_path):
    repo = copy_game(TEMPLATE, tmp_path / "game")
    (repo / "scenes" / "bad.gd").write_text("extends Node\n\nfunc f() -> void:\n\tvar x = 5\n", encoding="utf-8")
    report = run_verify(GodotRunner(godot_bin()), repo, "sha", tmp_path / "report", display=None)
    st = statuses(report)
    assert st["scripts"] == "fail"
    assert st["screens"] == st["contract"] == st["assets"] == st["scenarios"] == "skipped"


@needs_godot
def test_no_scenarios_fails(tmp_path):
    repo = copy_game(TEMPLATE, tmp_path / "game")
    (repo / "tests" / "scenarios" / "smoke.json").unlink()
    report = run_verify(GodotRunner(godot_bin()), repo, "sha", tmp_path / "report", display=None)
    assert statuses(report)["scenarios"] == "fail"


@needs_godot
@needs_display
def test_screens_with_display(tmp_path):
    repo = copy_game(TEMPLATE, tmp_path / "game")
    report = run_verify(GodotRunner(godot_bin()), repo, "sha", tmp_path / "report", display=find_display(),
                        only=["smoke"])
    assert report.passed, (tmp_path / "report" / "report.md").read_text()
    viewports = sorted(s["viewport"] for s in report.screenshots if "viewport" in s)
    assert viewports == ["landscape", "portrait", "tablet"]
    for shot in report.screenshots:
        assert (tmp_path / "report" / shot["file"]).is_file()


@needs_godot
def test_harness_test_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HARNESS_WORKSPACE", str(tmp_path / "ws"))
    cfg = load_config(DEFAULT_CONFIG_DIR)
    project = create_project(cfg, "a test game", "game_001")
    code = main(["test", "game_001"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "PASS" in out and "report.md" in out
    events = project.log().read()
    assert events[-1]["type"] == "TEST_FINISHED" and events[-1]["data"]["passed"]
    assert project.state()["state"] == "CREATED"  # tests do not move the pipeline
    assert project.repo().is_clean()
