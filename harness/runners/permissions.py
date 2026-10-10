"""Per-role tool lists and permission rules (ARCHITECTURE.md 4.7).

Runs use --permission-mode dontAsk: anything not allowed here is denied
(read-only shell commands such as `ls` are allowed by Claude Code itself).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

COMMON_DENY = [
    "Edit(.claude/**)",
    "Write(.claude/**)",
    "Edit(.mcp.json)",
    "Write(.mcp.json)",
    "Edit(addons/harness/**)",
    "Write(addons/harness/**)",
    "Read(.env)",
    "Read(**/.env)",
    "Bash(git push *)",
    "Bash(git reset *)",
    "Bash(git checkout *)",
    "Bash(git switch *)",
    "Bash(git rebase *)",
    "Bash(git tag *)",
    "Bash(git clean *)",
    "Bash(git config *)",
    "Bash(git remote *)",
]

_FILE_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep"]
_ENGINEER_BASH = [
    "Bash(godot *)",
    "Bash(godot-render *)",
    "Bash(git status *)",
    "Bash(git status)",
    "Bash(git diff *)",
    "Bash(git diff)",
    "Bash(git log *)",
    "Bash(git add *)",
    "Bash(git commit *)",
    "Bash(git rm *)",
    "Bash(git mv *)",
    "Bash(mkdir *)",
]


@dataclass(frozen=True)
class RolePolicy:
    tools: list[str]
    allow: list[str]
    deny: list[str] = field(default_factory=lambda: list(COMMON_DENY))


POLICIES: dict[str, RolePolicy] = {
    "engineer": RolePolicy(tools=[*_FILE_TOOLS, "Bash"], allow=[*_FILE_TOOLS, *_ENGINEER_BASH]),
    "debugger": RolePolicy(tools=[*_FILE_TOOLS, "Bash"], allow=[*_FILE_TOOLS, *_ENGINEER_BASH]),
    "planner": RolePolicy(tools=["Read", "Glob", "Grep"], allow=["Read", "Glob", "Grep"]),
    "release_writer": RolePolicy(tools=["Read", "Glob", "Grep"], allow=["Read", "Glob", "Grep"]),
    # cwd is harness/eval/<sha>: writes only to its scenarios/, never to the game copy or the report.
    "evaluator": RolePolicy(
        tools=[*_FILE_TOOLS, "Bash"],
        allow=["Read", "Glob", "Grep", "Write(./scenarios/**)", "Edit(./scenarios/**)", "Bash(godot *)",
               "Bash(godot-render *)"],
        deny=[*COMMON_DENY, "Write(./game/**)", "Edit(./game/**)", "Write(./report/**)", "Edit(./report/**)"],
    ),
}


def sandbox_settings(enabled: bool) -> dict[str, Any]:
    """Bash sandbox (bubblewrap on Linux, seatbelt on macOS). Not available on native Windows."""
    if not enabled or sys.platform == "win32":
        return {}
    return {
        "sandbox": {
            "enabled": True,
            "allowUnsandboxedCommands": False,
            "network": {"allowedDomains": []},
        }
    }


def policy_for(role: str) -> RolePolicy:
    if role not in POLICIES:
        raise KeyError(f"no permission policy for role '{role}'")
    return POLICIES[role]
