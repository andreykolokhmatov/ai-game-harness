from harness.doctor import Check, check_workspace, format_report
from harness.config import DEFAULT_CONFIG_DIR, load_config


def test_format_report_counts():
    report = format_report(
        [Check("a", "ok", "fine"), Check("b", "warn", "meh", "do x"), Check("c", "fail", "bad", "do y")]
    )
    assert "[FAIL] c" in report
    assert "-> do y" in report
    assert report.endswith("1 failed, 1 warnings")


def test_format_report_all_ok():
    assert format_report([Check("a", "ok", "fine")]).endswith("all checks passed")


def test_workspace_not_yet_created_is_ok(tmp_path):
    cfg = load_config(DEFAULT_CONFIG_DIR, env={"HARNESS_WORKSPACE": str(tmp_path / "new" / "ws")})
    assert check_workspace(cfg).status == "ok"
