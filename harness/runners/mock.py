"""Mock runner: replays scripted responses. For Harness tests without spending usage."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from harness.runners.base import AgentRequest, AgentResult, AgentStatus


@dataclass
class MockResponse:
    status: AgentStatus = "ok"
    files: dict[str, str] = field(default_factory=dict)  # path relative to cwd -> content
    text: str | None = "done"
    structured_output: dict[str, Any] | None = None
    num_turns: int = 1
    usage_by_model: dict[str, dict[str, Any]] = field(default_factory=dict)
    infra_error_kind: str | None = None
    # Runs after files are written; may raise to simulate the Harness being killed mid-run.
    side_effect: Callable[[AgentRequest], None] | None = None


class MockRunner:
    def __init__(self, responses: list[MockResponse]):
        self._responses = list(responses)
        self.requests: list[AgentRequest] = []

    def run(self, req: AgentRequest) -> AgentResult:
        if not self._responses:
            raise AssertionError(f"MockRunner: no scripted response left for run {req.run_id}")
        resp = self._responses.pop(0)
        self.requests.append(req)
        for rel, content in resp.files.items():
            target = req.cwd / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        session_id = req.resume_session_id or req.session_id
        req.transcript_path.parent.mkdir(parents=True, exist_ok=True)
        with req.transcript_path.open("w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "system", "subtype": "init", "session_id": session_id, "model": req.model}) + "\n")
            if resp.side_effect:
                resp.side_effect(req)
            f.write(json.dumps({"type": "result", "subtype": resp.status, "session_id": session_id}) + "\n")
        return AgentResult(
            status=resp.status,
            session_id=session_id,
            exit_code=0 if resp.status == "ok" else 1,
            structured_output=resp.structured_output,
            text=resp.text,
            num_turns=resp.num_turns,
            usage_by_model=resp.usage_by_model,
            infra_error_kind=resp.infra_error_kind,
        )
