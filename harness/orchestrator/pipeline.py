"""Pipeline: SPEC (Planner) -> MILESTONE m1..mN (IMPLEMENT -> VERIFY -> EVALUATE -> FIX) -> DONE.

`run` is idempotent: it continues from the last consistent state recorded in
the event log, including a step that was interrupted by a crash or kill.
"""

from __future__ import annotations

import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from harness.config import Config
from harness.orchestrator import evaluator, planner
from harness.platform.env import agent_env, write_shim
from harness.project import Project
from harness.prompts.builder import render, write_system_prompt
from harness.runners.base import AgentRequest, AgentResult, AgentRunner
from harness.runners.claude_cli import write_request_log
from harness.runners.permissions import policy_for, sandbox_settings
from harness.state.lock import ProjectLock
from harness.state.projection import State
from harness.verify.report import VerifyReport, failure_digest
from harness.verify.godot import CHECK_SCRIPTS_GD, SCENARIO_RUNNER_GD

VerifyFn = Callable[[Path, str, Path], VerifyReport]  # (project dir, sha, report dir) -> report

STOP_STATES = {"HUMAN_REVIEW", "BLOCKED", "DONE", "FAILED"}
MAX_INTERRUPTIONS = 2
MAX_INFRA_RETRIES = 3
INFRA_PAUSE_S = 600
MILESTONE_CHECKPOINTS = {"m1": "cp/prototype"}
MAX_PLAN_ATTEMPTS = 2
MAX_EVAL_FAILURES = 2  # Evaluator runs without a usable report before BLOCKED (evaluator_failure)
ENGINEER_KINDS = ("implement", "fix", "revise")  # steps that change the repo
LAST_REPORT = "last_report"  # copy of the latest verify report inside the scratch dir


class PipelineError(Exception):
    pass


def fingerprint(report: VerifyReport) -> str:
    """Failed check ids + first error per check, without numbers and paths."""
    parts = []
    for check in report.failures():
        message = check.errors[0]["message"] if check.errors else check.summary
        message = re.sub(r"res://\S+|/\S+|\d+", "#", message)
        parts.append(f"{check.id}:{message}")
    return "|".join(sorted(parts))


class Pipeline:
    def __init__(
        self,
        cfg: Config,
        project: Project,
        runner: AgentRunner,
        verify: VerifyFn,
        godot_bin: Path,
        clock: Callable[[], float] = time.time,
    ):
        self.cfg = cfg
        self.project = project
        self.runner = runner
        self.verify_fn = verify
        self.godot_bin = godot_bin
        self.clock = clock
        self.log = project.log()
        self._infra_failures = 0
        self._carry_interruptions = 0
        self._plan_feedback: list[str] = []
        self._eval_failures = 0
        self.use_evaluator = bool((cfg.raw.get("gates") or {}).get("evaluator", True))
        limits = cfg.raw.get("limits") or {}
        self.max_fix_attempts = int(limits.get("fix_attempts_per_milestone", 4))
        self.breaker = int(limits.get("circuit_breaker_same_fingerprint", 3))
        self.run_timeout_min = limits.get("run_timeout_min") or {}
        self.max_turns = limits.get("max_turns") or {}
        self.idle_min = limits.get("idle_output_timeout_min")
        self.max_milestones = int(limits.get("max_milestones", 3))

    # ---- state helpers -------------------------------------------------

    def state(self) -> State:
        return self.project.state()

    def emit(self, type: str, data: dict[str, Any] | None = None, **context: Any) -> None:
        self.log.append(type, data, project=self.project.name, **context)

    def enter(self, state: str, **data: Any) -> None:
        self.emit("STATE_ENTERED", {"state": state, **data})

    def block(self, reason: str) -> None:
        self.emit("BLOCKED", {"reason": reason})

    # ---- main loop -----------------------------------------------------

    def run(self) -> State:
        with ProjectLock(self.project.lock_path):
            self._recover()
            while True:
                st = self.state()
                current = st["state"]
                if current == "CREATED":
                    self.enter("SPEC")
                elif current == "SPEC":
                    self._plan(st)
                elif current == "PAUSED":
                    if not self._unpause(st):
                        return st
                elif current == "MILESTONE":
                    self._milestone(st)
                elif current in STOP_STATES:
                    return st
                else:
                    raise PipelineError(f"no handler for state {current}")

    def _milestone(self, st: State) -> None:
        phase = st["phase"]
        if phase == "implement":
            self._agent_step(st, "implement", self._milestone_prompt(st["milestone"]))
        elif phase == "fix":
            last = st["last_failure"] or st["last_verify"] or {}
            prompt = render(
                "tasks/fix",
                report_dir=(self.project.scratch_dir / LAST_REPORT).as_posix(),
                attempt=st["attempt"],
                max_attempts=1 + self.max_fix_attempts,
                sha=(last.get("sha") or "")[:12],
                failures=last.get("digest") or "(no details)",
            )
            self._agent_step(st, "fix", prompt)
        elif phase == "revise":
            self._agent_step(st, "revise", render("tasks/revise", comment=st["review_comment"] or ""))
        elif phase == "verify":
            self._verify(st)
        elif phase == "evaluate":
            self._evaluate(st)
        else:
            raise PipelineError(f"unknown milestone phase {phase}")

    def _milestone_prompt(self, milestone_id: str) -> str:
        plan = planner.load_plan(self.project.artifacts_dir)
        m = planner.milestone(plan, milestone_id)
        first = plan["milestones"][0]["id"] == milestone_id
        context = ("The project is still the bare template: build the game from it." if first else
                   "Earlier milestones are done and accepted: keep everything that works.")
        return render("tasks/milestone", milestone_id=m["id"], title=m["title"], goal=m["goal"],
                      criteria=planner.criteria_text(m["criteria"]), context=context)

    # ---- SPEC: the Planner ------------------------------------------------

    def _plan(self, st: State) -> None:
        attempt = st["attempt"] + 1
        step_id = f"spec.plan.{attempt}"
        prompt = render("tasks/plan", idea=st["idea"], max_milestones=self.max_milestones,
                        godot_version=self.cfg.godot.version)
        if self._plan_feedback:
            prompt += "\n\nYour previous plan was rejected:\n" + "\n".join(f"- {p}" for p in self._plan_feedback)
        repo = self.project.repo()
        self.emit("STEP_STARTED", {"kind": "plan", "attempt": attempt, "start_commit": repo.head(), "interruptions": 0},
                  step_id=step_id)
        result = self._run_agent("planner", prompt, step_id, None, json_schema=planner.PLAN_SCHEMA, add_dirs=[])
        raw = result.structured_output if result.status == "ok" else None
        plan = planner.normalize(raw) if raw else None
        problems = planner.validate_plan(plan, self.max_milestones) if plan else []
        status = result.status if plan is not None or result.status != "ok" else "agent_error"
        self.emit("STEP_FINISHED", {"kind": "plan", "status": status}, step_id=step_id)
        if self._handled_failure(result):
            return
        if plan is None or problems:
            self._plan_feedback = problems or [f"no structured plan was returned ({result.status})"]
            self.emit("PLAN_REJECTED", {"problems": self._plan_feedback}, step_id=step_id)
            if attempt >= MAX_PLAN_ATTEMPTS:
                self.block(f"the Planner did not produce a valid plan in {attempt} attempts: {self._plan_feedback[0]}")
            return
        self.accept_plan(plan, step_id)

    def accept_plan(self, plan: dict[str, Any], step_id: str | None = None) -> None:
        """Write the documents, commit them, tag cp/plan and start the first milestone."""
        repo = self.project.repo()
        planner.write_artifacts(plan, self.project.artifacts_dir, self.project.repo_dir / "docs")
        sha = repo.commit_all(f"docs: plan for {plan['title']}") or repo.head()
        repo.tag("cp/plan", sha)
        milestones = [m["id"] for m in plan["milestones"]]
        context = {"step_id": step_id} if step_id else {}
        self.emit("PLAN_ACCEPTED", {"title": plan["title"], "dimension": plan["dimension"], "milestones": milestones,
                                    "criteria": len(plan["acceptance"]), "commit": sha}, **context)
        self.emit("CHECKPOINT_CREATED", {"tag": "cp/plan", "commit": sha})
        self.enter("MILESTONE", milestone=milestones[0], phase="implement")

    # ---- recovery ------------------------------------------------------

    def _recover(self) -> None:
        st = self.state()
        step = st["open_step"]
        if not step:
            return
        interruptions = int(step.get("interruptions") or 0)
        self.emit(
            "STEP_FINISHED",
            {"kind": step.get("kind"), "status": "interrupted", "interruptions": interruptions},
            step_id=step["step_id"],
        )
        repo = self.project.repo()
        if interruptions + 1 > MAX_INTERRUPTIONS:
            repo.reset_hard(step["start_commit"])
            self.block(f"step {step['step_id']} was interrupted {interruptions + 1} times")
            return
        if step.get("kind") in ENGINEER_KINDS and step.get("session_id") and interruptions == 0:
            # Continue the same Claude session: it keeps the context of the interrupted work.
            self._agent_step(
                st,
                step["kind"],
                render("tasks/resume"),
                resume_session=step["session_id"],
                interruptions=interruptions + 1,
                start_commit=step["start_commit"],
                step_id=step["step_id"],
            )
            return
        repo.reset_hard(step["start_commit"])
        self._carry_interruptions = interruptions + 1

    def _unpause(self, st: State) -> bool:
        paused = st["paused"] or {}
        resets_at = paused.get("resets_at")
        if resets_at and self.clock() < float(resets_at):
            return False
        self.enter(paused.get("state") or "MILESTONE", milestone=paused.get("milestone"), phase=paused.get("phase"))
        return True

    # ---- agent step ----------------------------------------------------

    def _agent_step(
        self,
        st: State,
        kind: str,
        prompt: str,
        *,
        resume_session: str | None = None,
        interruptions: int | None = None,
        start_commit: str | None = None,
        step_id: str | None = None,
    ) -> None:
        repo = self.project.repo()
        if start_commit is None:
            if not repo.is_clean():
                raise PipelineError(
                    f"{repo.path} has uncommitted changes made outside the Harness; commit or discard them first"
                )
            start_commit = repo.head()
        if interruptions is None:
            interruptions, self._carry_interruptions = self._carry_interruptions, 0
        milestone = st["milestone"]
        step_id = step_id or f"{milestone}.{kind}.{st['attempt'] + 1}"
        self.emit(
            "STEP_STARTED",
            {"kind": kind, "attempt": st["attempt"] + 1, "start_commit": start_commit, "interruptions": interruptions},
            milestone=milestone,
            step_id=step_id,
        )
        result = self._run_agent("engineer", prompt, step_id, resume_session)
        commit = repo.commit_all(f"harness: {step_id} ({result.status})") or repo.head()
        repo.tag(f"h/{step_id}")
        self.emit(
            "STEP_FINISHED",
            {"kind": kind, "status": result.status, "commit": commit, "interruptions": interruptions},
            milestone=milestone,
            step_id=step_id,
        )
        self._after_agent(result)

    def _after_agent(self, result: AgentResult) -> None:
        if self._handled_failure(result):
            return
        st = self.state()
        self.enter("MILESTONE", milestone=st["milestone"], phase="verify")

    def _handled_failure(self, result: AgentResult) -> bool:
        """Usage limit, infrastructure errors and cancels: True when the caller must stop here.
        The same phase then runs again (or after PAUSED); these do not count as attempts."""
        if result.status == "usage_limit":
            resets_at = (result.rate_limit or {}).get("resetsAt")
            self.emit("PAUSED", {"reason": "usage_limit", "resets_at": resets_at})
            return True
        if result.status == "infra_error":
            if result.infra_error_kind in ("auth", "billing"):
                self.block(f"Claude Code {result.infra_error_kind} error: {result.error}")
                return True
            self._infra_failures += 1
            if self._infra_failures >= MAX_INFRA_RETRIES:
                self.emit(
                    "PAUSED",
                    {"reason": f"infra_error:{result.infra_error_kind}", "resets_at": self.clock() + INFRA_PAUSE_S},
                )
            return True
        if result.status == "cancelled":
            self.block("agent run was cancelled")
            return True
        self._infra_failures = 0
        return False

    def _next_run_id(self) -> str:
        count = sum(1 for e in self.log.read() if e["type"] == "AGENT_STARTED")
        return f"r-{count + 1:04d}"

    def _run_agent(
        self,
        role: str,
        prompt: str,
        step_id: str,
        resume_session: str | None,
        *,
        json_schema: dict[str, Any] | None = None,
        cwd: Path | None = None,
        add_dirs: list[Path] | None = None,
        scratch: Path | None = None,
    ) -> AgentResult:
        role_model = self.cfg.roles[role]
        policy = policy_for(role)
        run_id = self._next_run_id()
        run_dir = self.project.runs_dir / run_id
        system_file = write_system_prompt(
            role,
            run_dir / "system.md",
            godot_version=self.cfg.godot.version,
            check_scripts=CHECK_SCRIPTS_GD.as_posix(),
            scenario_runner=SCENARIO_RUNNER_GD.as_posix(),
            scratch=(scratch or self.project.scratch_dir).as_posix(),
        )
        self.project.scratch_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
        shim = write_shim(self.project.bin_dir, "godot", self.godot_bin)
        aliases = self.cfg.models_raw.get("models") or {}
        fallbacks = [aliases[a] for a in self.cfg.models_raw.get("fallback") or [] if aliases.get(a) != role_model.model_id]
        settings: dict[str, Any] = {"permissions": {"allow": [], "deny": []}}
        settings.update(sandbox_settings(bool(self.cfg.raw.get("sandbox", True))))
        session_id = resume_session or str(uuid.uuid4())
        req = AgentRequest(
            run_id=run_id,
            role=role,
            prompt=prompt,
            model=role_model.model_id,
            effort=role_model.effort,
            cwd=cwd or self.project.repo_dir,
            add_dirs=[self.project.scratch_dir] if add_dirs is None else add_dirs,
            json_schema=json_schema,
            transcript_path=run_dir / "transcript.jsonl",
            session_id=session_id,
            timeout_s=float(self.run_timeout_min.get(role, 60)) * 60,
            idle_timeout_s=float(self.idle_min) * 60 if self.idle_min else None,
            system_append_file=system_file,
            fallback_models=fallbacks,
            env=agent_env(
                prepend_path=[self.project.bin_dir],
                extra={"GODOT_BIN": str(shim)},
                pass_api_key=self.cfg.auth == "api_key",
            ),
            tools=policy.tools,
            allowed_tools=policy.allow,
            disallowed_tools=policy.deny,
            settings=settings,
            max_turns=self.max_turns.get(role),
            resume_session_id=resume_session,
        )
        write_request_log(req, run_dir / "request.json")
        context = {"step_id": step_id, "run_id": run_id}
        self.emit(
            "AGENT_STARTED",
            {"role": role, "model": req.model, "effort": req.effort, "session_id": session_id, "resume": bool(resume_session)},
            **context,
        )
        result = self.runner.run(req)
        self.emit("AGENT_FINISHED", {"role": role, "model": req.model, **result.to_event_data()}, **context)
        return result

    # ---- evaluate ------------------------------------------------------

    def _evaluate(self, st: State) -> None:
        last = st["last_verify"] or {}
        sha = last["sha"]
        report_dir = self.project.harness_dir / last["report"]
        plan = planner.load_plan(self.project.artifacts_dir)
        m = planner.milestone(plan, st["milestone"])
        criteria_ids = [c["id"] for c in m["criteria"]]
        eval_dir = evaluator.prepare_workspace(self.project.harness_dir / "eval" / sha[:12], self.project.repo_dir,
                                               report_dir)
        repo = self.project.repo()
        base = (st["last_checkpoint"] or {}).get("commit") or repo.head()
        prompt = render(
            "tasks/evaluate", milestone_id=m["id"], title=m["title"], goal=m["goal"],
            criteria=planner.criteria_text(m["criteria"]), base=base[:12],
            diff_stat=repo.diff_stat(base, sha) or "(no changes)",
            scenario_runner=SCENARIO_RUNNER_GD.as_posix(), scenarios_dir=(eval_dir / "scenarios").as_posix(),
        )
        n = sum(1 for e in self.log.read() if e["type"] == "EVAL_FINISHED" and e.get("milestone") == m["id"]) + 1
        step_id = f"{m['id']}.evaluate.{n}"
        self.emit("STEP_STARTED", {"kind": "evaluate", "start_commit": sha, "interruptions": 0},
                  milestone=m["id"], step_id=step_id)
        result = self._run_agent("evaluator", prompt, step_id, None, json_schema=evaluator.EVAL_SCHEMA,
                                 cwd=eval_dir, add_dirs=[], scratch=eval_dir / "scenarios")
        report = result.structured_output if result.status == "ok" else None
        self.emit("STEP_FINISHED", {"kind": "evaluate", "status": result.status if report else "agent_error"},
                  milestone=m["id"], step_id=step_id)
        if self._handled_failure(result):
            return
        if report is None:
            self._eval_failures += 1
            if self._eval_failures >= MAX_EVAL_FAILURES:
                self.block(f"evaluator_failure: no usable report in {self._eval_failures} runs ({result.status})")
            return  # the same phase runs again; this does not cost the Engineer an attempt
        self._eval_failures = 0
        passed, reasons = evaluator.verdict(report, criteria_ids)
        evaluator.save(report, report_dir, eval_dir)
        self._publish_report(report_dir)
        self.emit("EVAL_FINISHED", {
            "sha": sha, "passed": passed, "verdict": report.get("verdict"), "summary": report.get("summary"),
            "reasons": reasons, "report": last["report"],
            "criteria": {c.get("id"): c.get("status") for c in report.get("criteria") or []},
            "issues": len(report.get("issues") or []),
            "fingerprint": None if passed else evaluator.fingerprint(report, criteria_ids),
            "digest": None if passed else evaluator.digest(report, m["criteria"]),
        }, milestone=m["id"], step_id=step_id)
        st = self.state()
        if passed:
            self._milestone_passed(st, sha)
        else:
            self._check_failed(st)

    def _milestone_passed(self, st: State, sha: str) -> None:
        repo = self.project.repo()
        tag = MILESTONE_CHECKPOINTS.get(st["milestone"], f"cp/{st['milestone']}")
        repo.tag(tag, sha)
        self.emit("CHECKPOINT_CREATED", {"tag": tag, "commit": sha, "milestone": st["milestone"]})
        gate = bool((self.cfg.raw.get("gates") or {}).get("human_review_after_prototype", True))
        nxt = next_milestone(st)
        if gate and st["milestone"] == "m1":
            self.enter("HUMAN_REVIEW", milestone=st["milestone"])
        elif nxt:
            self.enter("MILESTONE", milestone=nxt, phase="implement")
        else:
            self.enter("DONE", milestone=st["milestone"])

    # ---- verify --------------------------------------------------------

    def _publish_report(self, report_dir: Path) -> None:
        """The Engineer cannot read harness/reports; give it a copy in its scratch dir."""
        _copy_report(report_dir, self.project.scratch_dir / LAST_REPORT)

    def _verify(self, st: State) -> None:
        repo = self.project.repo()
        # Godot import writes .uid files next to new scripts: they belong in the repo.
        repo.commit_all("harness: files generated by Godot import")
        sha = repo.head()
        report_dir = self.project.reports_dir / sha[:12]
        report = self.verify_fn(self.project.repo_dir, sha, report_dir)
        if repo.commit_all("harness: files generated by Godot import"):
            sha = repo.head()
        self._publish_report(report_dir)
        self.emit(
            "VERIFY_FINISHED",
            {
                "sha": sha,
                "passed": report.passed,
                "report": str(report_dir.relative_to(self.project.harness_dir)),
                "fingerprint": None if report.passed else fingerprint(report),
                "digest": None if report.passed else failure_digest(report),
                "checks": {c.id: c.status for c in report.checks},
            },
            milestone=st["milestone"],
        )
        st = self.state()
        if report.passed:
            if self.use_evaluator:
                self.enter("MILESTONE", milestone=st["milestone"], phase="evaluate")
            else:
                self._milestone_passed(st, sha)
            return
        self._check_failed(st)

    def _check_failed(self, st: State) -> None:
        """After a failed verify or evaluation: circuit breaker, attempt limit, or a fix step."""
        fps = st["verify_fingerprints"]
        if len(fps) >= self.breaker and len(set(fps[-self.breaker :])) == 1:
            self.block(f"circuit breaker: the same failure {self.breaker} times in a row: {fps[-1]}")
        elif st["attempt"] >= 1 + self.max_fix_attempts:
            self.block(f"checks still failing after {self.max_fix_attempts} fix attempts")
        else:
            self.enter("MILESTONE", milestone=st["milestone"], phase="fix")


def next_milestone(st: State) -> str | None:
    milestones = (st["plan"] or {}).get("milestones") or []
    if st["milestone"] not in milestones:
        return None
    i = milestones.index(st["milestone"])
    return milestones[i + 1] if i + 1 < len(milestones) else None


def _copy_report(report_dir: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    if report_dir.is_dir():
        shutil.copytree(report_dir, target)


def request_revision(project: Project, comment: str) -> None:
    """Record the human decision `revise` at HUMAN_REVIEW; the next `run` does the work."""
    comment = comment.strip()
    if not comment:
        raise PipelineError("revise needs a comment describing what to change")
    with ProjectLock(project.lock_path):
        st = project.state()
        if st["state"] != "HUMAN_REVIEW":
            raise PipelineError(f"revise is possible only at HUMAN_REVIEW, the project is in {st['state']}")
        log = project.log()
        log.append("HUMAN_DECISION", {"decision": "revise", "comment": comment}, project=project.name)
        log.append(
            "STATE_ENTERED",
            {"state": "MILESTONE", "milestone": st["milestone"], "phase": "revise"},
            project=project.name,
        )


def approve(project: Project) -> str:
    """Record the human decision `approve` at HUMAN_REVIEW; returns the next state."""
    with ProjectLock(project.lock_path):
        st = project.state()
        if st["state"] != "HUMAN_REVIEW":
            raise PipelineError(f"approve is possible only at HUMAN_REVIEW, the project is in {st['state']}")
        log = project.log()
        log.append("HUMAN_DECISION", {"decision": "approve"}, project=project.name)
        nxt = next_milestone(st)
        if nxt:
            log.append("STATE_ENTERED", {"state": "MILESTONE", "milestone": nxt, "phase": "implement"},
                       project=project.name)
            return f"MILESTONE {nxt}"
        log.append("STATE_ENTERED", {"state": "DONE", "milestone": st["milestone"]}, project=project.name)
        return "DONE"


def utc_from_epoch(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(timespec="seconds")


def run_tests(project: Project, verify: VerifyFn) -> tuple[VerifyReport, Path]:
    """`harness test`: only the deterministic checks on the current HEAD, no agents.

    Does not change the pipeline state; records TEST_FINISHED in the log.
    """
    with ProjectLock(project.lock_path):
        repo = project.repo()
        clean = repo.is_clean()
        sha = repo.head()
        report_dir = project.reports_dir / (sha[:12] if clean else f"{sha[:12]}-dirty")
        report = verify(project.repo_dir, sha if clean else f"{sha}-dirty", report_dir)
        if clean:
            # Godot import writes .uid files next to new scripts: they belong in the repo.
            repo.commit_all("harness: files generated by Godot import")
        project.log().append(
            "TEST_FINISHED",
            {
                "sha": report.sha,
                "passed": report.passed,
                "report": str(report_dir.relative_to(project.harness_dir)),
                "checks": {c.id: c.status for c in report.checks},
            },
            project=project.name,
        )
        return report, report_dir
