from godot_helpers import TEMPLATE, copy_game, godot_bin, needs_godot, needs_web
from harness.verify.godot import GodotRunner
from harness.verify.web import add_autoload, has_web_preset, run_web_checks

PROJECT = """config_version=5

[application]

config/name="x"

[autoload]

Platform="*res://autoload/platform.gd"
Game="*res://autoload/game.gd"

[display]

window/stretch/mode="canvas_items"
"""


def test_add_autoload_goes_last_in_the_section(tmp_path):
    path = tmp_path / "project.godot"
    path.write_text(PROJECT)
    add_autoload(path, "HarnessProbe", "res://harness_probe/web_probe.gd")
    text = path.read_text()
    assert 'Game="*res://autoload/game.gd"\nHarnessProbe="*res://harness_probe/web_probe.gd"\n\n[display]' in text


def test_add_autoload_creates_the_section(tmp_path):
    path = tmp_path / "project.godot"
    path.write_text('config_version=5\n\n[application]\n\nconfig/name="x"\n')
    add_autoload(path, "HarnessProbe", "res://p.gd")
    assert path.read_text().endswith('[autoload]\n\nHarnessProbe="*res://p.gd"\n')


def test_template_has_web_preset(tmp_path):
    assert has_web_preset(TEMPLATE)
    assert not has_web_preset(tmp_path)


def statuses(checks):
    return {c.id: c.status for c in checks}


@needs_godot
@needs_web
def test_template_passes_web_checks(tmp_path):
    repo = copy_game(TEMPLATE, tmp_path / "game")
    checks, shots = run_web_checks(GodotRunner(godot_bin()), repo, tmp_path / "report")
    assert statuses(checks) == {"web_export": "pass", "web_desktop": "pass", "web_mobile": "pass", "web_focus": "pass"}, \
        [c.summary for c in checks]
    assert sorted(s["label"] for s in shots) == ["web_desktop", "web_mobile_landscape", "web_mobile_portrait"]
    for shot in shots:
        assert (tmp_path / "report" / shot["file"]).is_file() and shot["distinct_colors"] > 1
    assert not (repo / "harness_probe").exists()  # the probe lives only in the build copy


@needs_godot
@needs_web
def test_focus_check_catches_platform_without_page_events(tmp_path):
    repo = copy_game(TEMPLATE, tmp_path / "game")
    platform = repo / "autoload" / "platform.gd"
    platform.write_text(platform.read_text().replace("\t\t_listen_to_page()", "\t\tpass"))
    checks, _ = run_web_checks(GodotRunner(godot_bin()), repo, tmp_path / "report")
    st = statuses(checks)
    assert st["web_desktop"] == "pass" and st["web_focus"] == "fail"


@needs_godot
def test_missing_preset_fails_export(tmp_path):
    repo = copy_game(TEMPLATE, tmp_path / "game")
    (repo / "export_presets.cfg").unlink()
    checks, _ = run_web_checks(GodotRunner(godot_bin()), repo, tmp_path / "report")
    assert statuses(checks)["web_export"] in ("fail", "skipped")
