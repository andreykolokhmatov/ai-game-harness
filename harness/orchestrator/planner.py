"""Planner output: JSON schema, validation and the artifacts the Harness writes from it.

The Planner is read-only. It returns one structured object (--json-schema); the Harness
validates it and writes GDD.md, ACCEPTANCE.yaml, CONTRACT.yaml and PLAN.md to
harness/artifacts/ (originals) and repo/docs/ (what the other roles read).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from harness.verify.contract import STATE_TYPES

DOC_FILES = ("GDD.md", "ACCEPTANCE.yaml", "CONTRACT.yaml", "PLAN.md")

_STR = {"type": "string"}
PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "dimension", "gdd", "contract", "milestones"],
    "properties": {
        "title": _STR,
        "dimension": {"type": "string", "enum": ["2d", "3d"]},
        "gdd": {"type": "string", "description": "Game design document in Markdown"},
        "contract": {
            "type": "object",
            "additionalProperties": False,
            "required": ["input_actions", "state", "commands"],
            "properties": {
                "input_actions": {"type": "array", "items": _STR},
                "state": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["key", "type", "description"],
                        "properties": {"key": _STR, "type": {"type": "string", "enum": list(STATE_TYPES)},
                                       "description": _STR},
                    },
                },
                "commands": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "args", "description"],
                        "properties": {"name": _STR, "args": {"type": "array", "items": _STR}, "description": _STR},
                    },
                },
            },
        },
        "milestones": {
            "type": "array",
            "minItems": 1,
            "description": "In build order; the first one is the playable prototype",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "goal", "criteria"],
                "properties": {
                    "title": _STR,
                    "goal": _STR,
                    "criteria": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["description", "verify"],
                            "properties": {"description": _STR, "verify": {
                                "type": "string", "description": "How a test proves it through the Game Contract"}},
                        },
                    },
                },
            },
        },
    },
}


def normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """Planner output -> plan with Harness-assigned ids: milestones m1..mN, criteria AC-1..AC-K."""
    plan = {k: v for k, v in raw.items() if k != "milestones"}
    plan["milestones"], plan["acceptance"] = [], []
    for i, m in enumerate(raw.get("milestones") or [], start=1):
        plan["milestones"].append({"id": f"m{i}", "title": m.get("title", ""), "goal": m.get("goal", "")})
        for c in m.get("criteria") or []:
            plan["acceptance"].append({"id": f"AC-{len(plan['acceptance']) + 1}", "milestone": f"m{i}",
                                       "description": c.get("description", ""), "verify": c.get("verify", "")})
    return plan


def validate_plan(plan: dict[str, Any], max_milestones: int) -> list[str]:
    """Problems the schema cannot express. Empty list: the plan is usable."""
    problems: list[str] = []
    ids = [m["id"] for m in plan.get("milestones") or []]
    if not ids:
        problems.append("the plan has no milestones")
    if len(ids) > max_milestones:
        problems.append(f"{len(ids)} milestones; at most {max_milestones} are allowed")
    criteria = plan.get("acceptance") or []
    for mid in ids:
        if not any(c["milestone"] == mid for c in criteria):
            problems.append(f"milestone {mid} has no acceptance criteria")
    contract = plan.get("contract") or {}
    keys = [s.get("key") for s in contract.get("state") or []]
    if len(set(keys)) != len(keys):
        problems.append("contract state keys must be unique")
    if "scene" not in keys:
        problems.append("contract state must include 'scene' (the current screen)")
    return problems


def contract_yaml(plan: dict[str, Any]) -> str:
    contract = plan["contract"]
    data = {
        "input_actions": list(contract["input_actions"]),
        "state": {s["key"]: s["type"] for s in contract["state"]},
        "commands": {c["name"]: {"args": list(c["args"]), "description": c["description"]} for c in contract["commands"]},
    }
    notes = "\n".join(f"#   {s['key']}: {s['description']}" for s in contract["state"])
    header = (
        "# Game Contract: the interface tests use instead of node names (written by the Planner).\n"
        "# The Engineer may add keys, actions and commands; removing or renaming planned ones breaks acceptance.\n"
        f"# State keys:\n{notes}\n"
    )
    return header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def acceptance_yaml(plan: dict[str, Any]) -> str:
    data = {"criteria": [dict(c) for c in plan["acceptance"]]}
    return "# Acceptance criteria per milestone (written by the Planner). The Evaluator judges each one.\n" + \
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def plan_md(plan: dict[str, Any]) -> str:
    lines = [f"# Plan: {plan['title']}", "", f"Dimension: {plan['dimension'].upper()}", ""]
    for m in plan["milestones"]:
        lines += [f"## {m['id']}: {m['title']}", "", m["goal"], "", "Acceptance:"]
        lines += [f"- {c['id']}: {c['description']}" for c in plan["acceptance"] if c["milestone"] == m["id"]]
        lines.append("")
    return "\n".join(lines)


def write_artifacts(plan: dict[str, Any], artifacts_dir: Path, docs_dir: Path) -> None:
    files = {
        "GDD.md": plan["gdd"].rstrip() + "\n",
        "ACCEPTANCE.yaml": acceptance_yaml(plan),
        "CONTRACT.yaml": contract_yaml(plan),
        "PLAN.md": plan_md(plan),
    }
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for name, text in files.items():
        (artifacts_dir / name).write_text(text, encoding="utf-8")
        (docs_dir / name).write_text(text, encoding="utf-8")


def load_plan(artifacts_dir: Path) -> dict[str, Any]:
    return json.loads((artifacts_dir / "plan.json").read_text(encoding="utf-8"))


def milestone(plan: dict[str, Any], milestone_id: str) -> dict[str, Any]:
    """The milestone with its acceptance criteria attached."""
    m = next(m for m in plan["milestones"] if m["id"] == milestone_id)
    return {**m, "criteria": [c for c in plan["acceptance"] if c["milestone"] == milestone_id]}


def criteria_text(criteria: list[dict[str, Any]]) -> str:
    return "\n".join(f"- {c['id']}: {c['description']}\n  verify: {c['verify']}" for c in criteria)
