import pytest

from harness.verify.godot import numeric_version, parse_version, template_version, version_matches

FULL = "4.4.1.stable.official.49a5bc7b6"


def test_parse_version_skips_noise():
    assert parse_version(f"WARNING: something\n{FULL}\n") == FULL
    assert parse_version("garbage") is None


def test_template_version():
    assert template_version(FULL) == "4.4.1.stable"
    assert template_version("4.4.stable.official.abc") == "4.4.stable"
    assert numeric_version(FULL) == "4.4.1"


@pytest.mark.parametrize(
    ("pin", "expected"),
    [("4.4", True), ("4.4.1", True), ("4", True), ("4.3", False), ("4.4.2", False), ("4.41", False)],
)
def test_version_matches(pin, expected):
    assert version_matches(FULL, pin) is expected
