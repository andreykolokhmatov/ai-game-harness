"""Game Contract (docs/CONTRACT.yaml): the interface tests use instead of node names.

input_actions: InputMap action names the game reads.
state: dot paths returned by Game.game_state(), with a type each.
commands: debug commands handled by Game.harness_command(name, args).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from harness.verify.report import CheckResult

CONTRACT_FILE = Path("docs") / "CONTRACT.yaml"
STATE_TYPES = ("string", "int", "float", "bool", "vec2", "vec3", "list", "dict", "any")
_INPUT_SECTION = re.compile(r"^\[input\]\s*$(.*?)(?=^\[|\Z)", re.MULTILINE | re.DOTALL)
_ACTION_LINE = re.compile(r"^([A-Za-z0-9_]+)=\{", re.MULTILINE)


def project_actions(repo: Path) -> set[str]:
    """Action names from the [input] section of project.godot."""
    text = (repo / "project.godot").read_text(encoding="utf-8")
    section = _INPUT_SECTION.search(text)
    return set(_ACTION_LINE.findall(section.group(1))) if section else set()


def load_contract(repo: Path) -> tuple[dict[str, Any] | None, list[str]]:
    path = repo / CONTRACT_FILE
    if not path.is_file():
        return None, [f"{CONTRACT_FILE.as_posix()} is missing"]
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return None, [f"{CONTRACT_FILE.as_posix()}: invalid YAML: {exc}"]
    if not isinstance(data, dict):
        return None, [f"{CONTRACT_FILE.as_posix()}: top level must be a mapping"]
    problems = []
    actions = data.get("input_actions", [])
    if not isinstance(actions, list) or not all(isinstance(a, str) for a in actions):
        problems.append("input_actions must be a list of action names")
    state = data.get("state", {})
    if not isinstance(state, dict):
        problems.append("state must be a mapping 'dot.path: type'")
    else:
        for key, kind in state.items():
            if kind not in STATE_TYPES:
                problems.append(f"state '{key}': unknown type '{kind}' (use {', '.join(STATE_TYPES)})")
    commands = data.get("commands", {})
    if commands is not None and not isinstance(commands, dict):
        problems.append("commands must be a mapping 'name: {args: [...], description: ...}'")
    return (data if not problems else None), problems


def _lookup(state: Any, dotted: str) -> tuple[bool, Any]:
    value = state
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    return True, value


def _type_ok(kind: str, value: Any) -> bool:
    number = (int, float)
    if kind == "string":
        return isinstance(value, str)
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "float":
        return isinstance(value, number) and not isinstance(value, bool)
    if kind == "bool":
        return isinstance(value, bool)
    if kind in ("vec2", "vec3"):
        size = 2 if kind == "vec2" else 3
        return isinstance(value, list) and len(value) == size and all(isinstance(v, number) for v in value)
    if kind == "list":
        return isinstance(value, list)
    if kind == "dict":
        return isinstance(value, dict)
    return True


def check_contract(repo: Path, state: dict[str, Any] | None) -> CheckResult:
    """Static checks plus, when `state` (a game_state() snapshot) is given, the state keys."""
    contract, problems = load_contract(repo)
    if contract is not None:
        known = project_actions(repo)
        for action in contract.get("input_actions") or []:
            if action not in known:
                problems.append(f"input action '{action}' is not defined in project.godot [input]")
        if state is None:
            note = "state keys not checked (no game_state() snapshot)"
        else:
            note = "state keys match game_state()"
            for key, kind in (contract.get("state") or {}).items():
                found, value = _lookup(state, key)
                if not found:
                    problems.append(f"game_state() has no '{key}' (declared in the contract)")
                elif not _type_ok(kind, value):
                    problems.append(f"game_state() '{key}' should be {kind}, got {value!r}")
    if problems:
        return CheckResult(
            "contract",
            "fail",
            problems[0] if len(problems) == 1 else f"{len(problems)} contract problems",
            errors=[{"message": p, "at": CONTRACT_FILE.as_posix()} for p in problems],
        )
    return CheckResult("contract", "pass", note)
