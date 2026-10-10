"""Full deterministic VERIFY (stage 3): basic checks + screenshots + contract + scenarios.

Order and gates:
  structure -> import (hard gate) -> scripts -> smoke
  -> screens (built-in smoke scenario per viewport; gives the game_state() snapshot)
  -> contract -> assets (docs/ASSETS.md) -> scenarios (one check per tests/scenarios/*.json)
  -> web_export -> web_desktop, web_mobile, web_focus (when web=True, see harness.verify.web)
Runtime checks after `scripts` are skipped when scripts do not compile: every scenario
would fail with the same compile errors.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.platform.display import Display
from harness.state.snapshot import write_json_atomic
from harness.verify.assets import check_assets
from harness.verify.basic import _from_run, check_structure
from harness.verify.contract import check_contract
from harness.verify.godot import GodotRunner
from harness.verify.markdown import write_markdown
from harness.verify.report import CheckResult, VerifyReport
from harness.verify.web import CHECK_IDS as WEB_CHECKS, run_web_checks
from harness.verify.scenarios import BUILTIN_SMOKE, VIEWPORTS, ScenarioRun, describe_failure, discover, run_scenario


def _skipped(check_id: str, reason: str) -> CheckResult:
    return CheckResult(check_id, "skipped", f"skipped: {reason}")


def _engine_errors(sr: ScenarioRun) -> list[dict]:
    return [{"message": e.message, "at": e.location} for e in sr.run.errors]


def _rel(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return str(path)


def scenario_check(sr: ScenarioRun, repo: Path, out_dir: Path) -> CheckResult:
    status = "pass" if sr.passed else "fail"
    summary = f"passed in {(sr.result or {}).get('frames', 0)} frames" if sr.passed else describe_failure(sr)
    errors = _engine_errors(sr)
    if not sr.passed and not errors:
        errors = [{"message": summary, "at": _rel(sr.path, repo)}]
    return CheckResult(
        f"scenario:{sr.id}", status, summary, errors, len(sr.run.warnings), round(sr.run.duration_s, 2),
        _rel(sr.log, out_dir),
    )


def check_screens(
    godot: GodotRunner, repo: Path, out_dir: Path, display: Display | None
) -> tuple[CheckResult, dict[str, Any] | None, list[dict]]:
    """Run the built-in smoke scenario: per viewport with a display, once headless without.

    Returns the check, a game_state() snapshot (for the contract) and screenshot records.
    """
    if not (display and display.available):
        sr = run_scenario(godot, repo, BUILTIN_SMOKE, out_dir, display=None, run_id="harness_smoke")
        state = (sr.result or {}).get("final_state") if sr.result else None
        reason = display.detail if display else "screenshots disabled"
        check = CheckResult("screens", "skipped", f"skipped: no display ({reason})", duration_s=round(sr.run.duration_s, 2))
        if not sr.passed:
            check = CheckResult("screens", "fail", describe_failure(sr), _engine_errors(sr), log=_rel(sr.log, out_dir))
        return check, state, []

    problems: list[dict] = []
    shots: list[dict] = []
    state: dict[str, Any] | None = None
    duration = 0.0
    for name, size in VIEWPORTS.items():
        sr = run_scenario(godot, repo, BUILTIN_SMOKE, out_dir, display=display, run_id=f"smoke_{name}", viewport=size)
        duration += sr.run.duration_s
        if state is None and sr.result:
            state = sr.result.get("final_state")
        if not sr.passed:
            problems.append({"message": f"{name} {size[0]}x{size[1]}: {describe_failure(sr)}", "at": _rel(sr.log, out_dir)})
            problems.extend(_engine_errors(sr))
            continue
        images = (sr.result or {}).get("screenshots") or []
        if not images:
            problems.append({"message": f"{name}: no screenshot was written", "at": None})
        for image in images:
            record = {**image, "file": _rel(Path(image["file"]), out_dir), "viewport": name}
            shots.append(record)
            if image.get("distinct_colors", 0) <= 1:
                problems.append({"message": f"{name}: the screen is a single color (nothing rendered?)", "at": record["file"]})
    if problems:
        return CheckResult("screens", "fail", problems[0]["message"], problems, duration_s=round(duration, 2)), state, shots
    summary = f"{len(shots)} screenshots ({', '.join(VIEWPORTS)}) via {display.detail}"
    return CheckResult("screens", "pass", summary, duration_s=round(duration, 2)), state, shots


def run_verify(
    godot: GodotRunner,
    repo: Path,
    sha: str,
    out_dir: Path,
    *,
    display: Display | None,
    only: list[str] | None = None,
    web: bool = False,
) -> VerifyReport:
    """display=None: everything headless, no screenshots. only: scenario ids or file names to run.
    web: also export the game and check it in Chromium (slow: ~30 s)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    checks = [check_structure(repo)]
    shots: list[dict] = []
    later = ("screens", "contract", "assets", "scenarios", *(WEB_CHECKS if web else ()))
    if checks[0].status != "pass":
        checks += [_skipped(c, "structure failed") for c in ("import", "scripts", "smoke", *later)]
        return _finish(sha, checks, shots, display, out_dir)

    imported = godot.import_project(repo)
    checks.append(_from_run("import", imported, out_dir, "imported"))
    if imported.timed_out or imported.returncode != 0:
        checks += [_skipped(c, "import failed") for c in ("scripts", "smoke", *later)]
        return _finish(sha, checks, shots, display, out_dir)

    scripts = _from_run("scripts", godot.check_scripts(repo), out_dir, "all scripts compile")
    checks.append(scripts)
    checks.append(_from_run("smoke", godot.smoke(repo), out_dir, "main scene ran 120 frames without errors"))
    if scripts.status == "fail":
        checks += [_skipped(c, "scripts do not compile") for c in later]
        return _finish(sha, checks, shots, display, out_dir)

    screens, state, shots = check_screens(godot, repo, out_dir, display)
    checks.append(screens)
    checks.append(check_contract(repo, state))
    checks.append(check_assets(repo))

    scenario_files = discover(repo)
    if only:
        wanted = set(only)
        scenario_files = [p for p in scenario_files if p.stem in wanted or p.name in wanted]
    if not scenario_files:
        checks.append(CheckResult("scenarios", "fail", "no scenarios in tests/scenarios/*.json" + (f" matching {only}" if only else "")))
    else:
        runs = [run_scenario(godot, repo, path, out_dir, display=display) for path in scenario_files]
        failed = sum(1 for sr in runs if not sr.passed)
        checks.append(CheckResult("scenarios", "fail" if failed else "pass", f"{len(runs) - failed}/{len(runs)} passed"))
        for sr in runs:
            checks.append(scenario_check(sr, repo, out_dir))
            for image in (sr.result or {}).get("screenshots") or []:
                shots.append({**image, "file": _rel(Path(image["file"]), out_dir), "scenario": sr.id})
    if web:
        web_checks, web_shots = run_web_checks(godot, repo, out_dir)
        checks += web_checks
        shots += web_shots
    return _finish(sha, checks, shots, display, out_dir)


def _finish(sha: str, checks: list[CheckResult], shots: list[dict], display: Display | None, out_dir: Path) -> VerifyReport:
    rendered = display.detail if display and display.available else None
    report = VerifyReport(sha=sha, passed=not any(c.status == "fail" for c in checks), checks=checks,
                          display=rendered, screenshots=shots)
    write_json_atomic(out_dir / "verify.json", report.to_dict())
    write_markdown(report, out_dir)
    return report
