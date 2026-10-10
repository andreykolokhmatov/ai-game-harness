from godot_helpers import TEMPLATE
from harness.verify.locale import check_locale

PROJECT = 'config_version=5\n\n[internationalization]\n\nlocale/translations=PackedStringArray("res://locale/s.en.translation")\n'


def test_template_is_localized():
    assert check_locale(TEMPLATE).status == "pass"


def test_missing_russian_text_fails(tmp_path):
    (tmp_path / "project.godot").write_text(PROJECT)
    (tmp_path / "locale").mkdir()
    (tmp_path / "locale" / "s.csv").write_text("keys,en,ru\nPLAY,Play,Играть\nQUIT,Quit,\n", encoding="utf-8")
    result = check_locale(tmp_path)
    assert result.status == "fail" and "key QUIT has no ru text" in result.summary


def test_no_translations_fails(tmp_path):
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "data.csv").write_text("name,score\na,1\n")  # not a translation file
    assert check_locale(tmp_path).status == "fail"
