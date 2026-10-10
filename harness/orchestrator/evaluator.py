"""Evaluator: its workspace, the EvalReport schema and the verdict the Harness enforces.

The Evaluator never sees the real repository. It works in harness/eval/<sha>/:
  game/       a copy of the game at the verified commit (read-only by permissions)
  report/     a copy of the VERIFY report (logs, scenario results, screenshots)
  scenarios/  the only writable place: its own scenarios that try to break the game
So it cannot fix the game it judges, and whatever it runs cannot touch the repo.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

SEVERITIES = ("critical", "major", "minor")
CATEGORIES = ("crash", "softlock", "logic", "ui", "controls", "perf", "visual", "platform")
BLOCKING = ("critical", "major")

_STR = {"type": "string"}
EVAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "summary", "criteria", "issues"],
    "properties": {
        "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
        "summary": {"type": "string", "description": "Two or three sentences for the human"},
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "status", "evidence"],
                "properties": {
                    "id": {"type": "string", "description": "Criterion id, e.g. AC-3"},
                    "status": {"type": "string", "enum": ["pass", "fail", "inconclusive"]},
                    "evidence": {"type": "string", "description": "Scenario, screenshot or code that shows it"},
                },
            },
        },
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["severity", "category", "description", "reproduction", "expected", "actual", "evidence"],
                "properties": {
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "description": _STR,
                    "reproduction": {"type": "string", "description": "Steps a developer can follow"},
                    "expected": _STR,
                    "actual": _STR,
                    "evidence": _STR,
                },
            },
        },
    },
}


def prepare_workspace(eval_dir: Path, repo: Path, report_dir: Path) -> Path:
    """Fresh game/ and report/ copies; scenarios/ survives so a rerun can reuse them."""
    for name in ("game", "report"):
        if (eval_dir / name).exists():
            shutil.rmtree(eval_dir / name)
    shutil.copytree(repo, eval_dir / "game", ignore=shutil.ignore_patterns(".git"))
    shutil.copytree(report_dir, eval_dir / "report")
    (eval_dir / "scenarios").mkdir(parents=True, exist_ok=True)
    return eval_dir


def verdict(report: dict[str, Any], criteria_ids: list[str]) -> tuple[bool, list[str]]:
    """PASS only if the Evaluator says PASS, every criterion of the milestone passed
    (inconclusive is not pass) and no critical or major issue is open."""
    reasons: list[str] = []
    statuses = {c.get("id"): c.get("status") for c in report.get("criteria") or []}
    for cid in criteria_ids:
        status = statuses.get(cid)
        if status != "pass":
            reasons.append(f"{cid}: {status or 'not judged'}")
    blocking = [i for i in report.get("issues") or [] if i.get("severity") in BLOCKING]
    reasons += [f"{i['severity']} {i.get('category')}: {i.get('description')}" for i in blocking]
    if report.get("verdict") != "PASS" and not reasons:
        reasons.append("the Evaluator's verdict is FAIL")
    return not reasons, reasons


def digest(report: dict[str, Any], criteria: list[dict[str, Any]]) -> str:
    """What the Engineer gets for a fix: failed criteria and issues with reproduction.
    Not the Evaluator's scenario files: the fix must address the cause, not a test."""
    by_id = {c["id"]: c for c in criteria}
    lines = []
    for c in report.get("criteria") or []:
        if c.get("status") != "pass" and c.get("id") in by_id:
            lines.append(f"- {c['id']} {c['status']}: {by_id[c['id']]['description']}")
            lines.append(f"    evaluator: {c.get('evidence', '')}")
    for issue in sorted(report.get("issues") or [], key=lambda i: SEVERITIES.index(i.get("severity", "minor"))):
        lines.append(f"- [{issue['severity']}/{issue['category']}] {issue['description']}")
        for key in ("reproduction", "expected", "actual"):
            if issue.get(key):
                lines.append(f"    {key}: {issue[key]}")
    return "\n".join(lines) or "- the Evaluator rejected the milestone without details"


def fingerprint(report: dict[str, Any], criteria_ids: list[str]) -> str:
    statuses = {c.get("id"): c.get("status") for c in report.get("criteria") or []}
    failed = sorted(cid for cid in criteria_ids if statuses.get(cid) != "pass")
    blocking = sorted({str(i.get("category")) for i in report.get("issues") or [] if i.get("severity") in BLOCKING})
    return "eval:" + ",".join(failed) + "|" + ",".join(blocking)


def save(report: dict[str, Any], report_dir: Path, eval_dir: Path) -> Path:
    """eval.json next to verify.json, plus the Evaluator's scenarios as evidence."""
    path = report_dir / "eval.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    scenarios = eval_dir / "scenarios"
    if scenarios.is_dir() and any(scenarios.iterdir()):
        target = report_dir / "eval_scenarios"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(scenarios, target)
    shutil.rmtree(eval_dir / "game", ignore_errors=True)  # a full copy of the game: not needed any more
    return path
