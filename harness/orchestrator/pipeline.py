"""MVP-0 pipeline: one milestone (prototype) with IMPLEMENT -> VERIFY -> FIX.

`run` is idempotent: it continues from the last consistent state recorded in
the event log, including a step that was interrupted by a crash or kill.
Planner, Evaluator and escalation come in stage 5.
"""

from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from harness.config import Config
from harness.platform.env import agent_env, write_shim
from harness.project import Project
from harness.prompts.builder import render, write_system_prompt
from harness.runners.base import AgentRequest, AgentResult, AgentRunner
from harness.runners.claude_cli import write_request_log
from harness.runners.permissions import policy_for, sandbox_settings
from harness.state.lock import ProjectLock
from harness.state.projection import State
from harness.verify.basic import VerifyReport, failure_digest
from harness.verify.godot import CHECK_SCRIPTS_GD

VerifyFn = Callable[[Path, str, Path], VerifyReport]  # (project dir, sha, report dir) -> report

STOP_STATES = {"HUMAN_REVIEW", "BLOCKED", "DONE", "FAILED"}
MAX_INTERRUPTIONS = 2
MAX_INFRA_RETRIES = 3
INFRA_PAUSE_S = 600
MILESTONE_CHECKPOINTS = {"m1": "cp/prototype"}


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
        limits = cfg.raw.get("limits") or {}
        self.max_fix_attempts = int(limits.get("fix_attempts_per_milestone", 4))
        self.breaker = int(limits.get("circuit_breaker_same_fingerprint", 3))
        self.run_timeout_min = limits.get("run_timeout_min") or {}
        self.max_turns = limits.get("max_turns") or {}
        self.idle_min = limits.get("idle_output_timeout_min")

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
                    self.enter("MILESTONE", milestone="m1", phase="implement")
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
            prompt = render("tasks/prototype", idea=st["idea"])
            self._agent_step(st, "implement", prompt)
        elif phase == "fix":
            last = st["last_verify"] or {}
            prompt = render(
                "tasks/fix",
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
        else:
            raise PipelineError(f"unknown milestone phase {phase}")

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
        if step.get("session_id") and interruptions == 0:
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
        if result.status == "usage_limit":
            resets_at = (result.rate_limit or {}).get("resetsAt")
            self.emit("PAUSED", {"reason": "usage_limit", "resets_at": resets_at})
            return
        if result.status == "infra_error":
            if result.infra_error_kind in ("auth", "billing"):
                self.block(f"Claude Code {result.infra_error_kind} error: {result.error}")
                return
            self._infra_failures += 1
            if self._infra_failures >= MAX_INFRA_RETRIES:
                self.emit(
                    "PAUSED",
                    {"reason": f"infra_error:{result.infra_error_kind}", "resets_at": self.clock() + INFRA_PAUSE_S},
                )
            return  # same phase runs again; infra failures do not count as attempts
        if result.status == "cancelled":
            self.block("agent run was cancelled")
            return
        self._infra_failures = 0
        st = self.state()
        self.enter("MILESTONE", milestone=st["milestone"], phase="verify")

    def _next_run_id(self) -> str:
        count = sum(1 for e in self.log.read() if e["type"] == "AGENT_STARTED")
        return f"r-{count + 1:04d}"

    def _run_agent(self, role: str, prompt: str, step_id: str, resume_session: str | None) -> AgentResult:
        role_model = self.cfg.roles[role]
        policy = policy_for(role)
        run_id = self._next_run_id()
        run_dir = self.project.runs_dir / run_id
        system_file = write_system_prompt(
            role,
            run_dir / "system.md",
            godot_version=self.cfg.godot.version,
            check_scripts=CHECK_SCRIPTS_GD.as_posix(),
        )
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
            cwd=self.project.repo_dir,
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

    # ---- verify --------------------------------------------------------

    def _verify(self, st: State) -> None:
        repo = self.project.repo()
        # Godot import writes .uid files next to new scripts: they belong in the repo.
        repo.commit_all("harness: files generated by Godot import")
        sha = repo.head()
        report_dir = self.project.reports_dir / sha[:12]
        report = self.verify_fn(self.project.repo_dir, sha, report_dir)
        if repo.commit_all("harness: files generated by Godot import"):
            sha = repo.head()
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
            tag = MILESTONE_CHECKPOINTS.get(st["milestone"], f"cp/{st['milestone']}")
            repo.tag(tag, sha)
            self.emit("CHECKPOINT_CREATED", {"tag": tag, "commit": sha})
            gate = bool((self.cfg.raw.get("gates") or {}).get("human_review_after_prototype", True))
            self.enter("HUMAN_REVIEW" if gate else "DONE", milestone=st["milestone"])
            return
        fps = st["verify_fingerprints"]
        if len(fps) >= self.breaker and len(set(fps[-self.breaker :])) == 1:
            self.block(f"circuit breaker: the same failure {self.breaker} times in a row: {fps[-1]}")
        elif st["attempt"] >= 1 + self.max_fix_attempts:
            self.block(f"verify still failing after {self.max_fix_attempts} fix attempts")
        else:
            self.enter("MILESTONE", milestone=st["milestone"], phase="fix")


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


def utc_from_epoch(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(timespec="seconds")
