import json

import pytest

from godot_helpers import FIXTURES, copy_game, godot_bin, needs_display, needs_godot
from harness.platform.display import find_display
from harness.verify.godot import GodotRunner
from harness.verify.scenarios import describe_failure, run_scenario

pytestmark = needs_godot


@pytest.fixture(scope="module")
def game(tmp_path_factory):
    repo = copy_game(FIXTURES / "mover", tmp_path_factory.mktemp("mover") / "game")
    GodotRunner(godot_bin()).import_project(repo)
    return repo


def write(repo, name, scenario):
    path = repo / "tests" / "scenarios" / f"{name}.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    return path


def run(game, path, out):
    return run_scenario(GodotRunner(godot_bin()), game, path, out, display=None)


def test_input_command_and_touch_scenario_passes(game, tmp_path):
    sr = run(game, game / "tests" / "scenarios" / "move.json", tmp_path)
    assert sr.passed, describe_failure(sr)
    assert sr.result["snapshots"]["start"]["player"]["position"] == [100.0, 360.0]
    assert sr.result["final_state"]["player"]["position"] == [600.0, 100.0]
    assert not sr.rendered


def test_false_assert_reports_step_and_state(game, tmp_path):
    path = write(game, "far", {"steps": [{"wait": 2}, {"assert": "state.player.position.x > 5000"}]})
    sr = run(game, path, tmp_path)
    assert not sr.passed
    message = describe_failure(sr)
    assert message.startswith('step 1 {"assert": "state.player.position.x > 5000"}')
    assert '"position":[100.0,360.0]' in message


@pytest.mark.parametrize(
    "step, expected",
    [
        ({"hold": "fly", "frames": 2}, "input action 'fly' is not in the InputMap; known actions: [move_right]"),
        ({"command": "explode"}, "command 'explode' [] was not handled"),
        ({"wait_until": "state.taps == 5", "timeout": 3}, "still false after 3 frames"),
        ({"assert": "state.nope.x"}, "Invalid named index 'nope'"),
        ({"key": "NOT_A_KEY"}, "unknown key 'NOT_A_KEY'"),
    ],
)
def test_step_failures_are_explained(game, tmp_path, step, expected):
    path = write(game, "bad_step", {"steps": [step]})
    sr = run(game, path, tmp_path)
    assert not sr.passed
    assert expected in describe_failure(sr)


def test_invalid_scenario(game, tmp_path):
    path = write(game, "invalid", {"steps": [{"fly": 3}]})
    sr = run(game, path, tmp_path)
    assert sr.run.returncode == 2
    assert describe_failure(sr).startswith("invalid scenario: ")


def test_engine_error_fails_a_passing_scenario(game, tmp_path):
    # teleport with a missing argument raises a script error inside the game
    path = write(game, "crash", {"steps": [{"command": "teleport", "args": [1]}]})
    sr = run(game, path, tmp_path)
    assert not sr.passed
    assert sr.run.errors


def test_directory_mode_for_agents(game):
    write(game, "zz_fail", {"steps": [{"assert": "false"}]})
    try:
        result = GodotRunner(godot_bin()).run(
            game,
            ["--fixed-fps", "60", "--script", str(run_scenario.__globals__["SCENARIO_RUNNER_GD"]), "--",
             "--scenario", "tests/scenarios"],
            timeout_s=120,
        )
    finally:
        for leftover in (game / "tests" / "scenarios").glob("*.json"):
            if leftover.name != "move.json":
                leftover.unlink()
    assert "HARNESS_SCENARIO PASS move" in result.output
    assert "HARNESS_SCENARIO FAIL zz_fail" in result.output
    assert "SUMMARY total=" in result.output and result.returncode == 1


@needs_display
def test_screenshot_with_display(game, tmp_path):
    sr = run_scenario(GodotRunner(godot_bin()), game, game / "tests" / "scenarios" / "move.json", tmp_path,
                      display=find_display(), viewport=(720, 1280))
    assert sr.passed, describe_failure(sr)
    [shot] = sr.result["screenshots"]
    assert shot["size"] == [720, 1280] and shot["distinct_colors"] > 1
    assert (tmp_path / "screenshots" / "move__end.png").is_file()


@needs_godot
def test_scenario_runs_get_a_fresh_user_dir(tmp_path):
    from harness.verify.godot import GodotRunner

    script = tmp_path / "game" / "where.gd"
    repo = copy_game(FIXTURES / "mover", tmp_path / "game")
    script.write_text('extends SceneTree\n\nfunc _init() -> void:\n\tprint("USERDIR=", OS.get_user_data_dir())\n\tquit()\n')
    runner = GodotRunner(godot_bin())
    fresh = runner.run(repo, ["--script", str(script)], timeout_s=60, fresh_user_data=True).output
    shared = runner.run(repo, ["--script", str(script)], timeout_s=60).output
    assert "harness_userdata_" in fresh and "harness_userdata_" not in shared
