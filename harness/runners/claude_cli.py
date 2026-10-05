"""Main adapter: runs `claude -p --output-format stream-json` as a subprocess.

Isolation from the user's environment (ARCHITECTURE.md 4.6, amended 2026-10-05):
`--setting-sources ""` skips user, project and local settings files, so hooks or
MCP servers that an agent writes into the game repo are never loaded.
The same flag also skips CLAUDE.md, so the role prompt and the engine guide
come only through --append-system-prompt-file. Permissions come only from
--settings built by the Harness for this run.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, IO

from harness.platform import proc
from harness.runners.base import AgentRequest, AgentResult, AgentStatus

INTERRUPT_GRACE_S = 20.0

_RESULT_SUBTYPES: dict[str, AgentStatus] = {
    "success": "ok",
    "error_max_turns": "max_turns",
    "error_max_budget_usd": "agent_error",
    "error_during_execution": "agent_error",
}

_INFRA_KINDS = {
    "rate_limit": "rate_limit",
    "overloaded": "overloaded",
    "authentication_failed": "auth",
    "billing_error": "billing",
    "server_error": "server",
    "network": "network",
}


def build_settings(req: AgentRequest) -> dict[str, Any]:
    settings = json.loads(json.dumps(req.settings))  # deep copy
    perms = settings.setdefault("permissions", {})
    perms["allow"] = list(perms.get("allow", [])) + list(req.allowed_tools)
    perms["deny"] = list(perms.get("deny", [])) + list(req.disallowed_tools)
    return settings


def build_command(req: AgentRequest, claude_cmd: list[str]) -> list[str]:
    """Argument list. The prompt goes through stdin (no shell quoting, no length limit)."""
    cmd = [
        *claude_cmd,
        "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--model", req.model,
        "--permission-mode", "dontAsk",
        "--permission-prompts", "none",
        "--setting-sources", "",
        "--strict-mcp-config",
        "--settings", json.dumps(build_settings(req), separators=(",", ":")),
    ]
    if req.resume_session_id:
        cmd += ["--resume", req.resume_session_id]
    else:
        cmd += ["--session-id", req.session_id]
    if req.tools is not None:
        cmd += ["--tools", ",".join(req.tools)]
    if req.effort:
        cmd += ["--effort", req.effort]
    if req.fallback_models:
        cmd += ["--fallback-model", req.fallback_models[0]]
    if req.system_append_file:
        cmd += ["--append-system-prompt-file", str(req.system_append_file)]
    for directory in req.add_dirs:
        cmd += ["--add-dir", str(directory)]
    if req.json_schema is not None:
        cmd += ["--json-schema", json.dumps(req.json_schema, separators=(",", ":"))]
    if req.max_turns is not None:
        cmd += ["--max-turns", str(req.max_turns)]
    if req.max_budget_usd is not None:
        cmd += ["--max-budget-usd", str(req.max_budget_usd)]
    return cmd


class StreamParser:
    """Collects what the Harness needs from stream-json events."""

    def __init__(self) -> None:
        self.result: dict[str, Any] | None = None
        self.init: dict[str, Any] | None = None
        self.infra_error_kind: str | None = None
        self.rate_limit: dict[str, Any] | None = None  # last non-"allowed" rate_limit_info
        self._assistant_usage: dict[str, tuple[str, dict[str, Any]]] = {}  # message id -> (model, usage)

    def feed(self, event: dict[str, Any]) -> None:
        etype = event.get("type")
        if etype == "result":
            self.result = event
        elif etype == "system" and event.get("subtype") == "init":
            self.init = event
        elif etype == "system" and event.get("subtype") == "api_retry":
            kind = str(event.get("error") or event.get("error_type") or "")
            self.infra_error_kind = _INFRA_KINDS.get(kind, kind or "unknown")
        elif etype == "rate_limit_event":
            info = event.get("rate_limit_info") or {}
            if info.get("status") not in (None, "allowed", "allowed_warning"):
                self.rate_limit = info
        elif etype == "assistant":
            message = event.get("message") or {}
            if message.get("id") and message.get("usage"):
                # The same message id repeats for each content block: keep the last.
                self._assistant_usage[message["id"]] = (message.get("model", "unknown"), message["usage"])

    def fallback_usage(self) -> dict[str, dict[str, Any]]:
        """Usage summed from assistant messages, for runs that never produced a result."""
        totals: dict[str, dict[str, Any]] = {}
        for model, usage in self._assistant_usage.values():
            bucket = totals.setdefault(model, {})
            for key, src in (
                ("inputTokens", "input_tokens"),
                ("outputTokens", "output_tokens"),
                ("cacheReadInputTokens", "cache_read_input_tokens"),
                ("cacheCreationInputTokens", "cache_creation_input_tokens"),
            ):
                bucket[key] = bucket.get(key, 0) + int(usage.get(src) or 0)
        return totals


def to_result(
    parser: StreamParser,
    *,
    session_id: str,
    exit_code: int | None,
    timed_out: bool,
    duration_s: float,
    stderr_tail: str = "",
) -> AgentResult:
    r = parser.result
    if r is not None:
        status = _RESULT_SUBTYPES.get(str(r.get("subtype")), "agent_error")
        if status == "ok" and r.get("is_error"):
            status = "agent_error"
        if status != "ok" and parser.rate_limit is not None:
            status = "usage_limit"
        if timed_out:
            status = "timeout"  # we interrupted it; the result event only reports the interruption
        model_usage = r.get("modelUsage") or parser.fallback_usage()
        return AgentResult(
            status=status,
            session_id=str(r.get("session_id") or session_id),
            exit_code=exit_code,
            structured_output=r.get("structured_output"),
            text=r.get("result") if isinstance(r.get("result"), str) else None,
            cost_usd_estimate=float(r.get("total_cost_usd") or 0.0),
            usage_by_model=model_usage,
            num_turns=int(r.get("num_turns") or 0),
            duration_s=duration_s,
            permission_denials=list(r.get("permission_denials") or []),
            infra_error_kind=parser.infra_error_kind if status != "ok" else None,
            rate_limit=parser.rate_limit,
            error=None if status == "ok" else str(r.get("result") or r.get("subtype")),
        )

    if timed_out:
        status: AgentStatus = "timeout"
    elif parser.rate_limit is not None:
        status = "usage_limit"
    elif parser.infra_error_kind:
        status = "infra_error"
    else:
        status = "agent_error"
    return AgentResult(
        status=status,
        session_id=session_id,
        exit_code=exit_code,
        usage_by_model=parser.fallback_usage(),
        duration_s=duration_s,
        infra_error_kind=parser.infra_error_kind,
        rate_limit=parser.rate_limit,
        error=stderr_tail.strip()[-2000:] or f"no result event (exit code {exit_code})",
    )


def _pump(stream: IO[str], out: queue.Queue) -> None:
    for line in stream:
        out.put(line)
    out.put(None)


class ClaudeCliRunner:
    def __init__(self, claude_cmd: list[str]):
        """claude_cmd: the executable plus any fixed leading args, e.g. ["claude"]."""
        self.claude_cmd = list(claude_cmd)

    def run(self, req: AgentRequest) -> AgentResult:
        run_dir = req.transcript_path.parent
        run_dir.mkdir(parents=True, exist_ok=True)
        stderr_path = run_dir / "stderr.log"
        session_id = req.resume_session_id or req.session_id
        parser = StreamParser()
        start = time.monotonic()

        with stderr_path.open("w", encoding="utf-8") as stderr_file, req.transcript_path.open(
            "a", encoding="utf-8", newline="\n"
        ) as transcript:
            child = subprocess.Popen(
                build_command(req, self.claude_cmd),
                cwd=req.cwd,
                env=req.env or None,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                text=True,
                encoding="utf-8",
                errors="replace",
                **proc.new_group_kwargs(),
            )
            assert child.stdin is not None and child.stdout is not None
            child.stdin.write(req.prompt)
            child.stdin.close()

            lines: queue.Queue = queue.Queue()
            threading.Thread(target=_pump, args=(child.stdout, lines), daemon=True).start()
            timed_out = self._consume(child, lines, transcript, parser, req, start)
            exit_code = child.wait()

        return to_result(
            parser,
            session_id=session_id,
            exit_code=exit_code,
            timed_out=timed_out,
            duration_s=time.monotonic() - start,
            stderr_tail=stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:],
        )

    def _consume(
        self,
        child: subprocess.Popen,
        lines: queue.Queue,
        transcript: IO[str],
        parser: StreamParser,
        req: AgentRequest,
        start: float,
    ) -> bool:
        """Read events until EOF; on timeout interrupt, then kill. Returns True if timed out."""
        last_output = time.monotonic()
        interrupted_at: float | None = None
        timed_out = False
        while True:
            try:
                line = lines.get(timeout=1.0)
            except queue.Empty:
                line = ""
            now = time.monotonic()
            if line is None:
                return timed_out
            if line:
                last_output = now
                transcript.write(line if line.endswith("\n") else line + "\n")
                transcript.flush()
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    parser.feed(event)
                continue
            if interrupted_at is None:
                over_total = now - start > req.timeout_s
                idle = req.idle_timeout_s is not None and now - last_output > req.idle_timeout_s
                if over_total or idle:
                    # SIGINT lets Claude finish the turn and write a result event.
                    timed_out = True
                    interrupted_at = now
                    proc.interrupt(child)
            elif now - interrupted_at > INTERRUPT_GRACE_S:
                proc.terminate_tree(child)


def write_request_log(req: AgentRequest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(req.to_log(), ensure_ascii=False, indent=2), encoding="utf-8")
