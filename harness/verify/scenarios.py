"""Running scenario files through the Harness-owned scenario runner (scenario_runner.gd).

Scenarios are JSON files in the game repo under tests/scenarios/. Each one runs in its
own Godot process. With a display (see harness.platform.display) the game renders and
screenshot steps produce images; without one the run is headless and logic-only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.platform.display import Display
from harness.verify.godot import SCENARIO_RUNNER_GD, GodotRun, GodotRunner

SCENARIO_DIR = Path("tests") / "scenarios"
BUILTIN_SMOKE = Path(__file__).with_name("scenarios") / "harness_smoke.json"
DEFAULT_VIEWPORT = (1280, 720)
# Screenshot set for the built-in smoke: desktop 16:9, phone portrait, tablet 4:3.
VIEWPORTS = {"landscape": (1280, 720), "portrait": (720, 1280), "tablet": (1024, 768)}
DEFAULT_TIMEOUT_FRAMES = 3600


@dataclass(frozen=True)
class ScenarioRun:
    id: str
    path: Path  # the scenario file
    result: dict[str, Any] | None  # runner output (None if the runner wrote nothing)
    run: GodotRun
    log: Path
    rendered: bool

    @property
    def passed(self) -> bool:
        return bool(self.result and self.result.get("passed")) and not self.run.timed_out and not self.run.errors


def discover(repo: Path) -> list[Path]:
    directory = repo / SCENARIO_DIR
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _viewport(scenario: dict[str, Any] | None) -> tuple[int, int]:
    value = (scenario or {}).get("viewport")
    if isinstance(value, list) and len(value) == 2 and all(isinstance(v, int) and v > 0 for v in value):
        return value[0], value[1]
    return DEFAULT_VIEWPORT


def _timeout_s(scenario: dict[str, Any] | None) -> float:
    frames = (scenario or {}).get("timeout_frames", DEFAULT_TIMEOUT_FRAMES)
    frames = frames if isinstance(frames, int) and frames > 0 else DEFAULT_TIMEOUT_FRAMES
    # Software rendering can run well below real time; a stuck game still gets killed.
    return 30 + frames / 60 * 4


def run_scenario(
    godot: GodotRunner,
    repo: Path,
    scenario: Path,
    out_dir: Path,
    *,
    display: Display | None,
    run_id: str | None = None,
    viewport: tuple[int, int] | None = None,
) -> ScenarioRun:
    """Run one scenario. display=None (or unavailable) means headless."""
    data = _read_json(scenario)
    scenario_id = run_id or str((data or {}).get("id") or scenario.stem)
    rendered = bool(display and display.available)
    result_path = out_dir / "scenarios" / f"{scenario_id}.json"
    result_path.unlink(missing_ok=True)  # never read a result left over from an earlier run
    args = ["--fixed-fps", "60"]
    if rendered:
        width, height = viewport or _viewport(data)
        args += ["--resolution", f"{width}x{height}"]
    args += ["--script", str(SCENARIO_RUNNER_GD), "--", "--scenario", str(scenario.resolve()),
             "--out", str(result_path.resolve()), "--id", scenario_id]
    if rendered:
        args += ["--shots", str((out_dir / "screenshots").resolve())]
    run = godot.run(
        repo,
        args,
        timeout_s=_timeout_s(data),
        headless=not rendered,
        display_prefix=display.prefix if display else None,
    )
    log = out_dir / "logs" / f"scenario_{scenario_id}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(run.output, encoding="utf-8")
    return ScenarioRun(scenario_id, scenario, _read_json(result_path), run, log, rendered)


def describe_failure(sr: ScenarioRun) -> str:
    """One line: where and why the scenario failed."""
    if sr.run.timed_out:
        return f"Godot did not finish in time (killed after {sr.run.duration_s:.0f}s)"
    result = sr.result
    if result is None:
        return f"runner produced no result (exit code {sr.run.returncode})"
    if result.get("invalid"):
        return f"invalid scenario: {result.get('message')}"
    if not result.get("passed"):
        step_index = result.get("failed_step", -1)
        steps = result.get("steps") or []
        step = next((s.get("step") for s in steps if s.get("index") == step_index), None)
        where = f"step {step_index} {json.dumps(step, ensure_ascii=False)}" if step is not None else "setup"
        return f"{where}: {result.get('message')}"
    if sr.run.errors:
        return f"{len(sr.run.errors)} engine error(s) during the scenario: {sr.run.errors[0].message}"
    return "passed"
