"""Basic deterministic VERIFY (MVP-0): structure, import, script compile, smoke run.

Later stages add scenarios, screenshots and web checks to the same report.
"""

from __future__ import annotations

import configparser
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from harness.state.snapshot import write_json_atomic
from harness.verify.godot import GodotRun, GodotRunner

CheckStatus = Literal["pass", "fail", "skipped"]


@dataclass
class CheckResult:
    id: str
    status: CheckStatus
    summary: str
    errors: list[dict] = field(default_factory=list)
    warnings_count: int = 0
    duration_s: float = 0.0
    log: str | None = None  # path relative to the report directory


@dataclass
class VerifyReport:
    sha: str
    passed: bool
    checks: list[CheckResult]

    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == "fail"]

    def to_dict(self) -> dict:
        return asdict(self)


def _main_scene(project: Path) -> str | None:
    """run/main_scene from project.godot, e.g. 'res://scenes/main.tscn'."""
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    text = (project / "project.godot").read_text(encoding="utf-8")
    # project.godot has a few top-level lines before the first section.
    parser.read_string("[_top]\n" + text)
    value = parser.get("application", "run/main_scene", fallback=None)
    return value.strip('"') if value else None


def check_structure(project: Path) -> CheckResult:
    if not (project / "project.godot").is_file():
        return CheckResult("structure", "fail", "project.godot is missing")
    main = _main_scene(project)
    if not main:
        return CheckResult("structure", "fail", "run/main_scene is not set in project.godot")
    if not main.startswith("res://") or not (project / main.removeprefix("res://")).is_file():
        return CheckResult("structure", "fail", f"main scene {main} does not exist")
    return CheckResult("structure", "pass", f"main scene {main}")


def _from_run(check_id: str, run: GodotRun, out_dir: Path, ok_summary: str) -> CheckResult:
    log_name = f"{check_id}.log"
    (out_dir / log_name).write_text(run.output, encoding="utf-8")
    errors = [{"message": e.message, "at": e.location} for e in run.errors]
    if run.timed_out:
        status, summary = "fail", "timed out"
    elif run.errors:
        status, summary = "fail", f"{len(run.errors)} error(s): {run.errors[0].message}"
    elif run.returncode != 0:
        status, summary = "fail", f"exit code {run.returncode}"
    else:
        status, summary = "pass", ok_summary
    return CheckResult(check_id, status, summary, errors, len(run.warnings), round(run.duration_s, 2), log_name)


def run_basic_verify(godot: GodotRunner, project: Path, sha: str, out_dir: Path) -> VerifyReport:
    out_dir.mkdir(parents=True, exist_ok=True)
    checks = [check_structure(project)]
    steps = [
        ("import", lambda: godot.import_project(project), "imported"),
        ("scripts", lambda: godot.check_scripts(project), "all scripts compile"),
        ("smoke", lambda: godot.smoke(project), "main scene ran 120 frames without errors"),
    ]
    # Hard gates: broken structure, or an import that hung or crashed. Error lines alone do not
    # block: the import log repeats script errors, and scripts/smoke add what import cannot see.
    blocker = None if checks[0].status == "pass" else "structure"
    for check_id, call, ok_summary in steps:
        if blocker:
            checks.append(CheckResult(check_id, "skipped", f"skipped: {blocker} failed"))
            continue
        run = call()
        checks.append(_from_run(check_id, run, out_dir, ok_summary))
        if check_id == "import" and (run.timed_out or run.returncode != 0):
            blocker = "import"
    report = VerifyReport(sha=sha, passed=all(c.status == "pass" for c in checks), checks=checks)
    write_json_atomic(out_dir / "verify.json", report.to_dict())
    return report


def failure_digest(report: VerifyReport, max_errors: int = 20) -> str:
    """Short text for the Engineer's fix prompt."""
    lines = []
    for check in report.failures():
        lines.append(f"- {check.id}: {check.summary}")
        for err in check.errors[:max_errors]:
            lines.append(f"    {err['message']}" + (f"  (at {err['at']})" if err.get("at") else ""))
    return "\n".join(lines)
