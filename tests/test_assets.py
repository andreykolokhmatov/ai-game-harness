from harness.verify.assets import check_assets

HEADER = "# Assets\n\n| File | Source | License |\n|---|---|---|\n"


def test_no_assets_passes(tmp_path):
    (tmp_path / "icon.svg").write_text("<svg/>")
    assert check_assets(tmp_path).status == "pass"


def test_unlisted_and_bad_license_fail(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "jump.wav").write_bytes(b"RIFF")
    (tmp_path / "assets" / "hero.png").write_bytes(b"PNG")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "ASSETS.md").write_text(HEADER + "| assets/hero.png | some site | proprietary |\n")
    result = check_assets(tmp_path)
    assert result.status == "fail"
    messages = [e["message"] for e in result.errors]
    assert "assets/jump.wav is not listed in docs/ASSETS.md" in messages
    assert any("license 'proprietary' is not allowed" in m for m in messages)


def test_listed_assets_pass(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "jump.wav").write_bytes(b"RIFF")
    (tmp_path / ".godot").mkdir()
    (tmp_path / ".godot" / "cache.png").write_bytes(b"x")  # import cache is ignored
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "ASSETS.md").write_text(HEADER + "| `res://assets/jump.wav` | sfx made in code | original |\n")
    assert check_assets(tmp_path).status == "pass"
