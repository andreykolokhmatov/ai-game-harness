"""Human-readable verify report (report.md next to verify.json)."""

from __future__ import annotations

from pathlib import Path

from harness.verify.report import VerifyReport

_MARK = {"pass": "PASS", "fail": "FAIL", "skipped": "skip"}
MAX_ERRORS = 10


def render_markdown(report: VerifyReport) -> str:
    lines = [
        f"# Verify report {report.sha[:12]}",
        "",
        f"**Result: {'PASS' if report.passed else 'FAIL'}**",
        "",
        f"Rendering: {report.display or 'headless only (no screenshots)'}",
        "",
        "| Check | Result | Summary |",
        "|---|---|---|",
    ]
    for check in report.checks:
        summary = check.summary.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {check.id} | {_MARK[check.status]} | {summary} |")

    failures = report.failures()
    if failures:
        lines += ["", "## Failures"]
        for check in failures:
            if check.id == "scenarios" and not check.errors:
                continue  # the per-scenario sections below carry the details
            lines += ["", f"### {check.id}", "", check.summary]
            for err in check.errors[:MAX_ERRORS]:
                if err["message"] == check.summary:
                    if err.get("at"):
                        lines.append(f"- file: `{err['at']}`")
                    continue
                lines.append(f"- `{err['message']}`" + (f" at `{err['at']}`" if err.get("at") else ""))
            if len(check.errors) > MAX_ERRORS:
                lines.append(f"- ... {len(check.errors) - MAX_ERRORS} more")
            if check.log:
                lines.append(f"- log: [{check.log}]({check.log})")
            if check.id.startswith("scenario:"):
                lines.append(f"- result: [scenarios/{check.id[9:]}.json](scenarios/{check.id[9:]}.json)")

    if report.screenshots:
        lines += ["", "## Screenshots", ""]
        for shot in report.screenshots:
            source = shot.get("viewport") or shot.get("scenario") or ""
            size = "x".join(str(v) for v in shot.get("size") or [])
            lines.append(f"- {source} `{shot.get('label')}` {size}: [{shot['file']}]({shot['file']})")
    return "\n".join(lines) + "\n"


def write_markdown(report: VerifyReport, out_dir: Path) -> Path:
    path = out_dir / "report.md"
    path.write_text(render_markdown(report), encoding="utf-8")
    return path
