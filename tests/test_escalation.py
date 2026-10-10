from harness.config import DEFAULT_CONFIG_DIR, load_config
from harness.orchestrator.escalation import enabled_tiers, pick_tier


def cfg():
    return load_config(DEFAULT_CONFIG_DIR, env={})


def test_one_tier_up_per_failure_and_frontier_disabled():
    c = cfg()
    assert [t.tier for t in enabled_tiers(c)] == [0, 1, 2]  # tier 3 (frontier) is disabled by default
    picks = [pick_tier(c, fps) for fps in (["a"], ["a", "b"], ["a", "b", "c"], ["a", "b", "c", "d"])]
    assert [(t.tier, t.role, t.effort) for t in picks] == [
        (0, "engineer", "medium"), (1, "engineer", "high"), (2, "debugger", "high"), (2, "debugger", "high")]
    assert picks[2].model_id == c.roles["debugger"].model_id


def test_same_failure_twice_skips_a_tier():
    assert pick_tier(cfg(), ["a", "a"]).tier == 2


def test_escalate_after_three():
    c = cfg()
    assert [pick_tier(c, list("abc")[:n], escalate_after=3).tier for n in (1, 2, 3)] == [0, 0, 1]


def test_enabled_frontier_tier_uses_its_model():
    c = cfg()
    c.models_raw["escalation"][3]["enabled"] = True
    top = pick_tier(c, list("abcd"))
    assert top.tier == 3 and top.model_id == c.models_raw["models"]["frontier"] and top.effort == "xhigh"
