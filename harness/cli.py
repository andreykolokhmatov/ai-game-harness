"""Command line entry point: `harness <command>`."""

from __future__ import annotations

import argparse
import json
import sys

from harness import __version__
from harness.config import Config, ConfigError, load_config
from harness.gitops.checkpoints import GitError
from harness.orchestrator.pipeline import PipelineError
from harness.project import ProjectError, create_project, open_project
from harness.state.events import CorruptLogError
from harness.state.lock import LockedError


def cmd_doctor(args: argparse.Namespace, cfg: Config) -> int:
    from harness.doctor import format_report, run_checks

    checks = run_checks(cfg)
    print(format_report(checks))
    return 1 if any(c.status == "fail" for c in checks) else 0


def cmd_create(args: argparse.Namespace, cfg: Config) -> int:
    project = create_project(cfg, args.idea, args.name)
    print(f"created {project.name} at {project.root}")
    print(f"next: harness run {project.name}")
    return 0


def _verify_fn(cfg: Config, only: list[str] | None = None):
    """(project dir, sha, report dir) -> VerifyReport, or an error message."""
    from harness.platform.display import find_display
    from harness.verify import godot
    from harness.verify.suite import run_verify

    godot_bin = godot.resolve_bin(cfg.godot)
    if godot_bin is None:
        return None, "Godot binary not found; run `harness doctor`"
    runner = godot.GodotRunner(godot_bin)
    verify_cfg = cfg.raw.get("verify") or {}
    display = find_display() if str(verify_cfg.get("screenshots", "auto")) == "auto" else None
    web = verify_cfg.get("web", True) is not False
    return (lambda repo, sha, out: run_verify(runner, repo, sha, out, display=display, only=only, web=web)), godot_bin


def cmd_run(args: argparse.Namespace, cfg: Config) -> int:
    from harness.doctor import check_sandbox
    from harness.orchestrator.pipeline import Pipeline
    from harness.runners.claude_cli import ClaudeCliRunner

    project = open_project(cfg, args.project)
    sandbox = check_sandbox(cfg)
    if sandbox.status == "fail":
        print(f"bash sandbox: {sandbox.detail}\n  -> {sandbox.hint}", file=sys.stderr)
        return 2
    verify, godot_bin = _verify_fn(cfg)
    if verify is None:
        print(godot_bin, file=sys.stderr)
        return 2
    pipeline = Pipeline(cfg, project, ClaudeCliRunner([cfg.claude_bin]), verify=verify, godot_bin=godot_bin)
    state = pipeline.run()
    print_status(project.name, state)
    return 0 if state["state"] in ("HUMAN_REVIEW", "DONE") else 1


def cmd_test(args: argparse.Namespace, cfg: Config) -> int:
    from harness.orchestrator.pipeline import run_tests

    project = open_project(cfg, args.project)
    verify, detail = _verify_fn(cfg, args.scenario or None)
    if verify is None:
        print(detail, file=sys.stderr)
        return 2
    report, report_dir = run_tests(project, verify)
    width = max(len(c.id) for c in report.checks)
    for check in report.checks:
        print(f"{check.status.upper():<7} {check.id:<{width}}  {check.summary}")
    print(f"\n{'PASS' if report.passed else 'FAIL'}  {report.sha[:12]}  rendering: {report.display or 'headless'}")
    print(f"report: {report_dir / 'report.md'}")
    return 0 if report.passed else 1


def cmd_approve(args: argparse.Namespace, cfg: Config) -> int:
    from harness.orchestrator.pipeline import approve

    project = open_project(cfg, args.project)
    nxt = approve(project)
    print(f"approved; next: {nxt}" + ("" if nxt == "DONE" else f" (harness run {project.name})"))
    return 0


def cmd_rollback(args: argparse.Namespace, cfg: Config) -> int:
    from harness.orchestrator.pipeline import rollback

    project = open_project(cfg, args.project)
    gate = bool((cfg.raw.get("gates") or {}).get("human_review_after_prototype", True))
    target = rollback(project, args.to, gate_after_m1=gate)
    print(f"rolled back to {args.to}; state {target}; next: harness run {project.name}")
    return 0


def cmd_revise(args: argparse.Namespace, cfg: Config) -> int:
    from harness.orchestrator.pipeline import request_revision

    project = open_project(cfg, args.project)
    request_revision(project, args.comment)
    print(f"revision recorded; next: harness run {project.name}")
    return 0


def print_status(name: str, st: dict) -> None:
    from harness.orchestrator.pipeline import utc_from_epoch

    c = st["counters"]
    lines = [
        f"project     {name}",
        f"idea        {st['idea']}",
        f"state       {st['state']}" + (f" ({st['milestone']}, phase {st['phase']})" if st.get("milestone") else ""),
        f"attempt     {st['attempt']}",
    ]
    if st.get("plan"):
        lines.append(f"plan        {st['plan']['title']} ({', '.join(st['plan']['milestones'])})")
    if st.get("blocked_reason"):
        lines.append(f"blocked     {st['blocked_reason']}")
    if st.get("paused"):
        p = st["paused"]
        lines.append(f"paused      {p.get('reason')}, resumes after {utc_from_epoch(p.get('resets_at')) or 'now'}")
    if st.get("open_step"):
        lines.append(f"open step   {st['open_step']['step_id']} (interrupted or running)")
    if st.get("last_verify"):
        v = st["last_verify"]
        lines.append(f"verify      {'PASS' if v['passed'] else 'FAIL'} on {v['sha'][:12]} ({v['report']})")
    if st.get("last_checkpoint"):
        lines.append(f"checkpoint  {st['last_checkpoint']['tag']} {st['last_checkpoint']['commit'][:12]}")
    lines.append(
        f"agents      {c['agent_runs']} runs, {c['agent_failures']} failed, {c['turns']} turns, "
        f"{c['agent_duration_s'] / 60:.1f} min, ~${c['cost_usd_estimate']:.2f} API-equivalent"
    )
    for model, usage in c["tokens_by_model"].items():
        lines.append(
            f"tokens      {model}: in {usage.get('inputTokens', 0)}, out {usage.get('outputTokens', 0)}, "
            f"cache read {usage.get('cacheReadInputTokens', 0)}"
        )
    print("\n".join(lines))


def cmd_status(args: argparse.Namespace, cfg: Config) -> int:
    project = open_project(cfg, args.project)
    st = project.state()
    if args.json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
    else:
        print_status(project.name, st)
    return 0


def cmd_logs(args: argparse.Namespace, cfg: Config) -> int:
    project = open_project(cfg, args.project)
    events = project.log().read()
    if args.run:
        run_dir = project.runs_dir / args.run
        if not run_dir.is_dir():
            print(f"no run {args.run}", file=sys.stderr)
            return 1
        finished = [e for e in events if e["type"] == "AGENT_FINISHED" and e.get("run_id") == args.run]
        for e in finished:
            d = e["data"]
            print(f"{args.run} {d['role']} {d['model']} status={d['status']} turns={d['num_turns']}")
            print(d.get("text") or d.get("error") or "")
        print(f"\ntranscript: {run_dir / 'transcript.jsonl'}")
        return 0
    for e in events:
        if args.role and e["data"].get("role") not in (None, args.role):
            continue
        brief = {k: v for k, v in e["data"].items() if k in ("state", "phase", "status", "role", "model", "passed", "tag", "reason")}
        where = " ".join(str(e[k]) for k in ("step_id", "run_id") if e.get(k))
        print(f"{e['seq']:>4} {e['ts'][:19]} {e['type']:<18} {where:<24} {json.dumps(brief, ensure_ascii=False)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness", description="AI Game Development Harness")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check the environment").set_defaults(func=cmd_doctor)

    p = sub.add_parser("create", help="create a project from an idea (no LLM)")
    p.add_argument("idea")
    p.add_argument("--name")
    p.set_defaults(func=cmd_create)

    for name, help_text in (("run", "run or continue the pipeline"), ("resume", "alias for run")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("project")
        p.set_defaults(func=cmd_run)

    p = sub.add_parser("test", help="run only the checks (no agents) on the current game commit")
    p.add_argument("project")
    p.add_argument("--scenario", action="append", help="scenario id or file name; repeat for several")
    p.set_defaults(func=cmd_test)

    p = sub.add_parser("rollback", help="return the game to a checkpoint tag (cp/plan, cp/prototype, cp/m2, ...)")
    p.add_argument("project")
    p.add_argument("--to", required=True, help="checkpoint tag")
    p.set_defaults(func=cmd_rollback)

    p = sub.add_parser("approve", help="at HUMAN_REVIEW: accept the prototype and continue with the next milestone")
    p.add_argument("project")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("revise", help="at HUMAN_REVIEW: send the game back to the engineer with a comment")
    p.add_argument("project")
    p.add_argument("comment")
    p.set_defaults(func=cmd_revise)

    p = sub.add_parser("status", help="show project state")
    p.add_argument("project")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("logs", help="show the event log or one agent run")
    p.add_argument("project")
    p.add_argument("--role")
    p.add_argument("--run")
    p.set_defaults(func=cmd_logs)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args, load_config())
    except (ConfigError, ProjectError, LockedError, CorruptLogError, GitError, PipelineError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
