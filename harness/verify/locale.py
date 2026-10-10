"""Localization check: games ship in English and Russian (ARCHITECTURE.md 0.1).

UI text lives in Godot translation CSV files (`keys,en,ru,...`) registered in project.godot
`internationalization/locale/translations`; every key needs non-empty en and ru text.
"""

from __future__ import annotations

import csv
from pathlib import Path

from harness.verify.report import CheckResult

LANGUAGES = ("en", "ru")
SKIP_DIRS = {".godot", ".git", "build", "tests"}


def translation_csvs(repo: Path) -> list[Path]:
    found = []
    for path in sorted(repo.rglob("*.csv")):
        rel = path.relative_to(repo)
        if rel.parts[0] in SKIP_DIRS:
            continue
        with path.open(encoding="utf-8", newline="") as f:
            header = next(csv.reader(f), [])
        if header and header[0].strip().lower() == "keys":
            found.append(path)
    return found


def _registers_translations(project_godot: str) -> bool:
    """[internationalization] section with a non-empty locale/translations line."""
    section = None
    for line in project_godot.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif section == "internationalization" and line.startswith("locale/translations="):
            return "res://" in line or "uid://" in line
    return False


def check_locale(repo: Path) -> CheckResult:
    files = translation_csvs(repo)
    if not files:
        return CheckResult("locale", "fail", "no translation CSV (keys,en,ru): UI text must be in English and Russian",
                           [{"message": "add locale/strings.csv with columns keys,en,ru and use tr(\"KEY\")", "at": None}])
    problems: list[dict] = []
    keys = 0
    for path in files:
        rel = path.relative_to(repo).as_posix()
        with path.open(encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
        header = [h.strip().lower() for h in rows[0]]
        missing = [lang for lang in LANGUAGES if lang not in header]
        if missing:
            problems.append({"message": f"{rel}: no column for {', '.join(missing)}", "at": rel})
            continue
        for line_no, row in enumerate(rows[1:], start=2):
            if not row or not row[0].strip():
                continue
            keys += 1
            for lang in LANGUAGES:
                i = header.index(lang)
                if i >= len(row) or not row[i].strip():
                    problems.append({"message": f"{rel}:{line_no}: key {row[0]} has no {lang} text", "at": rel})
    if not _registers_translations((repo / "project.godot").read_text(encoding="utf-8")):
        problems.append({"message": "project.godot does not register the translations "
                                    "(internationalization/locale/translations)", "at": "project.godot"})
    if problems:
        return CheckResult("locale", "fail", problems[0]["message"], problems)
    return CheckResult("locale", "pass", f"{keys} keys in {', '.join(LANGUAGES)}")
