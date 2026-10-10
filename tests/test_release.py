import json
import sys
import zipfile
from pathlib import Path

from godot_helpers import godot_bin, needs_display, needs_godot
from harness.config import DEFAULT_CONFIG_DIR, load_config
from harness.orchestrator.pipeline import Pipeline
from harness.platform.display import find_display
from harness.project import create_project
from harness.runners.mock import MockResponse, MockRunner
from harness.verify.basic import CheckResult, VerifyReport
from harness.verify.godot import GodotRunner
from test_pipeline import PLAN

META = {
    "en": {"title": "Hop", "short_description": "Jump over gaps.", "description": "A tiny jumper.",
           "how_to_play": "Space to jump, tap on phones."},
    "ru": {"title": "Прыг", "short_description": "Прыгай через ямы.", "description": "Маленький прыгун.",
           "how_to_play": "Пробел для прыжка, тап на телефоне."},
    "tags": ["arcade"], "orientation": "landscape",
}


def screenshot_verify(repo, sha, out):
    """Fake VERIFY that leaves the web screenshots a real report would have."""
    shots = out / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    for name in ("web_desktop.png", "web_mobile_portrait.png"):
        (shots / name).write_bytes(b"png")
    return VerifyReport(sha, True, [CheckResult("smoke", "pass", "ok")])


@needs_godot
@needs_display
def test_release_builds_package_and_passes_the_gate(tmp_path):
    cfg = load_config(DEFAULT_CONFIG_DIR, env={"HARNESS_WORKSPACE": str(tmp_path / "ws")})
    cfg.raw["gates"].update(evaluator=False, human_review_after_prototype=False)
    project = create_project(cfg, "simple 2D platformer", "game_001")
    one = {**PLAN, "milestones": PLAN["milestones"][:1], "acceptance": PLAN["acceptance"][:1]}
    runner = MockRunner([MockResponse(files={"notes.txt": "x"}), MockResponse(structured_output=META)])
    pipeline = Pipeline(cfg, project, runner, screenshot_verify, godot_bin=Path(sys.executable))
    pipeline.accept_plan(one)
    assert pipeline.run()["state"] == "DONE"

    verdict, out = pipeline.release(GodotRunner(godot_bin()), find_display())
    gate = json.loads((out / "final_gate.json").read_text(encoding="utf-8"))
    assert verdict == "READY", gate
    assert runner.requests[1].role == "release_writer" and runner.requests[1].json_schema
    zip_path = next(out.glob("*.zip"))
    assert "index.html" in zipfile.ZipFile(zip_path).namelist()
    assert (out / "icon.png").is_file() and (out / "cover.png").is_file()
    assert "Прыг" in (out / "METADATA.md").read_text(encoding="utf-8")
    st = project.state()
    assert st["state"] == "READY" and project.repo().tag_sha("cp/release-candidate") == project.repo().head()
    assert {c["id"]: c["status"] for c in gate["checks"]}["art_review"] == "manual"


@needs_godot
def test_gate_fails_before_all_milestones_are_done(tmp_path):
    cfg = load_config(DEFAULT_CONFIG_DIR, env={"HARNESS_WORKSPACE": str(tmp_path / "ws")})
    cfg.raw["gates"]["evaluator"] = False
    project = create_project(cfg, "simple 2D platformer", "game_001")
    runner = MockRunner([MockResponse(files={"notes.txt": "x"}), MockResponse(structured_output=None)])
    pipeline = Pipeline(cfg, project, runner, screenshot_verify, godot_bin=Path(sys.executable))
    pipeline.accept_plan(PLAN)
    assert pipeline.run()["state"] == "HUMAN_REVIEW"
    verdict, out = pipeline.release(GodotRunner(godot_bin()), None)
    failed = json.loads((out / "final_gate.json").read_text(encoding="utf-8"))
    failed = {c["id"] for c in failed["checks"] if c["status"] == "fail"}
    assert verdict == "FAILED" and {"milestones", "metadata", "icon", "cover"} <= failed
    assert project.state()["state"] == "HUMAN_REVIEW"
