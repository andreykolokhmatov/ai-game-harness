"""Agent runner interface. Adapters: claude_cli (main), mock (tests), sdk (later)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

AgentStatus = Literal["ok", "agent_error", "timeout", "usage_limit", "max_turns", "infra_error", "cancelled"]


@dataclass
class AgentRequest:
    run_id: str
    role: str
    prompt: str
    model: str
    cwd: Path
    transcript_path: Path
    session_id: str  # the Harness picks the UUID, so it is known before the first event
    timeout_s: float
    effort: str | None = None
    system_append_file: Path | None = None
    fallback_models: list[str] = field(default_factory=list)
    add_dirs: list[Path] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    tools: list[str] | None = None  # built-in tools available at all (--tools); None = CLI default
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)  # permissions and sandbox for this run
    json_schema: dict[str, Any] | None = None
    max_turns: int | None = None
    max_budget_usd: float | None = None  # only with auth: api_key
    idle_timeout_s: float | None = None
    resume_session_id: str | None = None

    def to_log(self) -> dict[str, Any]:
        """Serializable view for runs/<run_id>/request.json: no env values, no prompt body."""
        data = asdict(self)
        data["env"] = sorted(self.env)
        data["prompt"] = f"<{len(self.prompt)} chars>"
        return {k: str(v) if isinstance(v, Path) else v for k, v in data.items()}


@dataclass
class AgentResult:
    status: AgentStatus
    session_id: str
    exit_code: int | None = None
    structured_output: dict[str, Any] | None = None
    text: str | None = None
    cost_usd_estimate: float = 0.0
    usage_by_model: dict[str, dict[str, Any]] = field(default_factory=dict)
    num_turns: int = 0
    duration_s: float = 0.0
    permission_denials: list[Any] = field(default_factory=list)
    infra_error_kind: str | None = None  # rate_limit, overloaded, auth, network, ...
    rate_limit: dict[str, Any] | None = None  # rate_limit_info when the subscription limit was hit
    error: str | None = None

    def to_event_data(self) -> dict[str, Any]:
        return asdict(self)


class AgentRunner(Protocol):
    def run(self, req: AgentRequest) -> AgentResult: ...
