"""Escalation tiers for fix steps (ARCHITECTURE.md 6.2), from `escalation` in models.yaml.

A fix step runs at a tier chosen from the number of consecutive failed checks in the
milestone: the first fix at tier 0, then one tier up per failure. The same failure twice
in a row (no progress) skips one tier ahead. Disabled tiers are skipped; past the last
enabled tier the last one repeats until the attempt limit or the circuit breaker stops it.
"""

from __future__ import annotations

from dataclasses import dataclass

from harness.config import Config


@dataclass(frozen=True)
class Tier:
    tier: int
    role: str  # engineer | debugger
    model_id: str
    effort: str | None


def enabled_tiers(cfg: Config) -> list[Tier]:
    aliases = cfg.models_raw.get("models") or {}
    tiers = []
    for spec in cfg.models_raw.get("escalation") or []:
        if spec.get("enabled", True) is False:
            continue
        role = spec.get("role", "engineer")
        base = cfg.roles[role]
        model_id = str(aliases[spec["model"]]) if spec.get("model") else base.model_id
        tiers.append(Tier(int(spec.get("tier", len(tiers))), role, model_id, spec.get("effort", base.effort)))
    if not tiers:
        base = cfg.roles["engineer"]
        tiers.append(Tier(0, "engineer", base.model_id, base.effort))
    return sorted(tiers, key=lambda t: t.tier)


def pick_tier(cfg: Config, fingerprints: list[str | None], escalate_after: int = 2) -> Tier:
    """fingerprints: consecutive failed checks of this milestone, oldest first."""
    failures = len(fingerprints)
    level = max(0, failures - max(1, escalate_after) + 1)
    if failures >= 2 and fingerprints[-1] == fingerprints[-2]:
        level += 1
    tiers = enabled_tiers(cfg)
    return tiers[min(level, len(tiers) - 1)]


def tier_by_number(cfg: Config, number: int) -> Tier | None:
    """The enabled tier with this number (to resume an interrupted step at the same tier)."""
    return next((t for t in enabled_tiers(cfg) if t.tier == number), None)
