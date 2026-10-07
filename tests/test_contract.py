from harness.verify.contract import check_contract, project_actions

PROJECT = """config_version=5

[application]

run/main_scene="res://main.tscn"

[input]

move_left={
"deadzone": 0.2,
"events": []
}
jump={
"deadzone": 0.2,
"events": []
}

[rendering]

renderer/rendering_method="gl_compatibility"
"""


def make(tmp_path, contract):
    (tmp_path / "project.godot").write_text(PROJECT, encoding="utf-8")
    if contract is not None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "CONTRACT.yaml").write_text(contract, encoding="utf-8")
    return tmp_path


def test_project_actions(tmp_path):
    assert project_actions(make(tmp_path, None)) == {"move_left", "jump"}


def test_missing_contract(tmp_path):
    result = check_contract(make(tmp_path, None), None)
    assert result.status == "fail" and "CONTRACT.yaml is missing" in result.summary


def test_contract_matches_state(tmp_path):
    repo = make(tmp_path, "input_actions: [jump]\nstate:\n  scene: string\n  player.pos: vec2\n  score: int\n")
    state = {"scene": "Main", "player": {"pos": [1.0, 2.0]}, "score": 3}
    assert check_contract(repo, state).status == "pass"


def test_contract_problems_are_listed(tmp_path):
    repo = make(tmp_path, "input_actions: [jump, dash]\nstate:\n  score: int\n  lives: int\n  speed: velocity\n")
    result = check_contract(repo, {"score": 1.5})
    assert result.status == "fail"
    messages = [e["message"] for e in result.errors]
    assert messages == ["state 'speed': unknown type 'velocity' (use string, int, float, bool, vec2, vec3, list, dict, any)"]
    (tmp_path / "b").mkdir()
    repo = make(tmp_path / "b", "input_actions: [jump, dash]\nstate:\n  score: int\n  lives: int\n")
    messages = [e["message"] for e in check_contract(repo, {"score": 1.5}).errors]
    assert messages == [
        "input action 'dash' is not defined in project.godot [input]",
        "game_state() 'score' should be int, got 1.5",
        "game_state() has no 'lives' (declared in the contract)",
    ]
