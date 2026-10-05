import shutil
from pathlib import Path

import pytest

from harness.config import DEFAULT_CONFIG_DIR, load_config
from harness.verify import godot as godot_mod
from harness.verify.basic import check_structure, failure_digest, run_basic_verify
from harness.verify.godot import GodotRunner, parse_log

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "godot"


def _godot_bin():
    try:
        return godot_mod.resolve_bin(load_config(DEFAULT_CONFIG_DIR).godot)
    except Exception:
        return None


needs_godot = pytest.mark.skipif(_godot_bin() is None, reason="Godot binary not available")


def test_parse_log_pairs_location():
    log = (
        "Godot Engine v4.4.1\n"
        "SCRIPT ERROR: Parse Error: bad thing\n"
        "          at: GDScript::reload (res://a.gd:5)\n"
        "WARNING: something odd\n"
        "ERROR: no location\n"
    )
    errors, warnings = parse_log(log)
    assert [e.message for e in errors] == ["SCRIPT ERROR: Parse Error: bad thing", "ERROR: no location"]
    assert errors[0].location == "GDScript::reload (res://a.gd:5)"
    assert errors[1].location is None
    assert len(warnings) == 1


def test_structure_missing_main_scene(tmp_path):
    (tmp_path / "project.godot").write_text('config_version=5\n\n[application]\n\nrun/main_scene="res://nope.tscn"\n')
    result = check_structure(tmp_path)
    assert result.status == "fail" and "does not exist" in result.summary


@needs_godot
def test_template_passes_basic_verify(tmp_path):
    project = tmp_path / "game"
    shutil.copytree(TEMPLATE, project)
    report = run_basic_verify(GodotRunner(_godot_bin()), project, "sha", tmp_path / "report")
    assert report.passed, failure_digest(report)
    assert (tmp_path / "report" / "verify.json").is_file()


@needs_godot
def test_broken_script_and_runtime_error_fail(tmp_path):
    project = tmp_path / "game"
    shutil.copytree(TEMPLATE, project)
    (project / "scenes" / "bad.gd").write_text("extends Node\n\nfunc f() -> void:\n\tvar x = 5\n", encoding="utf-8")
    (project / "scenes" / "main.gd").write_text(
        "extends Node2D\n\n\nfunc _ready() -> void:\n\tvar n: Node = null\n\tn.queue_free()\n", encoding="utf-8"
    )
    report = run_basic_verify(GodotRunner(_godot_bin()), project, "sha", tmp_path / "report")
    status = {c.id: c.status for c in report.checks}
    assert status["scripts"] == "fail"
    assert status["smoke"] == "fail"
    digest = failure_digest(report)
    assert "bad.gd" in digest and "queue_free" in digest
