from harness.orchestrator import planner


def _walk(schema, path="$"):
    if schema.get("type") == "object":
        props = schema.get("properties") or {}
        for key in schema.get("required") or []:
            assert key in props, f"{path}: required '{key}' is not in properties"
        if schema.get("additionalProperties") is False:
            assert set(schema.get("required") or []) == set(props), f"{path}: strict object must require all properties"
        for key, sub in props.items():
            _walk(sub, f"{path}.{key}")
    if schema.get("type") == "array":
        _walk(schema["items"], f"{path}[]")


def test_plan_schema_is_consistent():
    _walk(planner.PLAN_SCHEMA)


def test_normalize_assigns_ids_in_order():
    raw = {"title": "t", "dimension": "2d", "gdd": "g",
           "contract": {"input_actions": [], "state": [{"key": "scene", "type": "string", "description": ""}],
                        "commands": []},
           "milestones": [{"title": "a", "goal": "x", "criteria": [{"description": "c1", "verify": "v"},
                                                                     {"description": "c2", "verify": "v"}]},
                          {"title": "b", "goal": "y", "criteria": [{"description": "c3", "verify": "v"}]}]}
    plan = planner.normalize(raw)
    assert [m["id"] for m in plan["milestones"]] == ["m1", "m2"]
    assert [(c["id"], c["milestone"]) for c in plan["acceptance"]] == [("AC-1", "m1"), ("AC-2", "m1"), ("AC-3", "m2")]
    assert planner.validate_plan(plan, 3) == []
    assert planner.validate_plan(planner.normalize({**raw, "milestones": raw["milestones"] * 2}), 3)


def test_eval_schema_is_consistent():
    from harness.orchestrator import evaluator

    _walk(evaluator.EVAL_SCHEMA)
