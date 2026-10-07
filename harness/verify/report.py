"""Verify report: check results, the JSON report and the short failure digest."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

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
    display: str | None = None  # how screenshots were rendered, None when headless only
    screenshots: list[dict] = field(default_factory=list)  # {file (relative), label, size, distinct_colors}

    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == "fail"]

    def to_dict(self) -> dict:
        return asdict(self)


def failure_digest(report: VerifyReport, max_errors: int = 20) -> str:
    """Short text for the Engineer's fix prompt."""
    lines = []
    for check in report.failures():
        lines.append(f"- {check.id}: {check.summary}")
        for err in check.errors[:max_errors]:
            lines.append(f"    {err['message']}" + (f"  (at {err['at']})" if err.get("at") else ""))
    return "\n".join(lines)
