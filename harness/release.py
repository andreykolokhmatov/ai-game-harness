"""`harness release` (stage 8, MVP part): web package, images, metadata, Final gate.

release/<sha12>/
  web/                       release export (the game's Web preset, no Harness probe)
  <project>-<sha12>.zip      the package: index.html at the root
  icon.png, cover.png        512x512 and 800x470 from the game's start screen
  screenshots/               desktop and phone shots from the last accepted verify report
  metadata.json, METADATA.md title, descriptions and how to play in English and Russian
  final_gate.json            READY or FAILED with every check and its reason
The Final gate is deterministic: every verdict is bound to the commit it was made on.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from harness.platform.display import Display
from harness.project import Project
from harness.verify.godot import GodotRunner
from harness.verify.scenarios import BUILTIN_SMOKE, run_scenario
from harness.verify.web import export_web

ICON = (512, 512)
COVER = (800, 470)
MAX_UNPACKED_MB = 100  # the Yandex Games limit; a sane bound for any web portal
LANGS = ("en", "ru")
META_FIELDS = ("title", "short_description", "description", "how_to_play")
HOW_TO_PLAY = {"en": "**How to play**", "ru": "**Как играть**"}

_TEXT = {"type": "string"}
_LANG = {"type": "object", "additionalProperties": False, "required": list(META_FIELDS),
         "properties": {"title": {"type": "string", "description": "At most 50 characters"},
                        "short_description": {"type": "string", "description": "One sentence, at most 120 characters"},
                        "description": {"type": "string", "description": "Two or three short paragraphs"},
                        "how_to_play": {"type": "string", "description": "Controls on desktop and on phones"}}}
METADATA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["en", "ru", "tags", "orientation"],
    "properties": {
        "en": _LANG,
        "ru": _LANG,
        "tags": {"type": "array", "items": _TEXT, "description": "3 to 6 genre tags in English"},
        "orientation": {"type": "string", "enum": ["landscape", "portrait", "any"]},
    },
}


@dataclass
class GateCheck:
    id: str
    status: str  # pass | fail | manual
    detail: str


def package(web_dir: Path, zip_path: Path) -> int:
    """Zip web_dir with index.html at the root; returns the unpacked size in bytes."""
    total = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(web_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(web_dir).as_posix())
                total += path.stat().st_size
    return total


def capture_images(godot: GodotRunner, repo: Path, out_dir: Path, display: Display | None) -> dict[str, dict]:
    """Icon and cover: the start screen rendered at their exact sizes, in English (the default)
    and the cover in Russian too. The language is fixed, not taken from the build machine."""
    images: dict[str, dict] = {}
    shots_dir = out_dir / "_shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    for name, size, locale in (("icon", ICON, "en"), ("cover", COVER, "en"), ("cover_ru", COVER, "ru")):
        scenario = shots_dir / f"{name}.json"
        smoke = json.loads(BUILTIN_SMOKE.read_text(encoding="utf-8"))
        scenario.write_text(json.dumps({**smoke, "locale": locale}), encoding="utf-8")
        sr = run_scenario(godot, repo, scenario, shots_dir, display=display, run_id=f"release_{name}",
                          viewport=size)
        shots = (sr.result or {}).get("screenshots") or []
        if sr.passed and shots:
            target = out_dir / f"{name}.png"
            shutil.copyfile(shots[0]["file"], target)
            images[name] = {"file": target.name, "size": list(size), "distinct_colors": shots[0].get("distinct_colors", 0)}
    shutil.rmtree(out_dir / "_shots", ignore_errors=True)
    return images


def copy_screenshots(report_dir: Path, out_dir: Path) -> list[str]:
    target = out_dir / "screenshots"
    target.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in ("web_desktop.png", "web_mobile_portrait.png", "web_mobile_landscape.png"):
        source = report_dir / "screenshots" / name
        if source.is_file():
            shutil.copyfile(source, target / name)
            copied.append(f"screenshots/{name}")
    return copied


def metadata_md(meta: dict[str, Any]) -> str:
    lines = [f"Tags: {', '.join(meta.get('tags') or [])}", f"Orientation: {meta.get('orientation')}", ""]
    for lang in LANGS:
        m = meta.get(lang) or {}
        lines += [f"## {lang.upper()}: {m.get('title', '')}", "", m.get("short_description", ""), "",
                  m.get("description", ""), "", HOW_TO_PLAY[lang], "", m.get("how_to_play", ""), ""]
    return "\n".join(lines)


def final_gate(st: dict[str, Any], head: str, *, use_evaluator: bool, unpacked: int | None, zip_path: Path,
               images: dict[str, dict], screenshots: list[str], meta: dict[str, Any] | None) -> list[GateCheck]:
    checks: list[GateCheck] = []

    def add(check_id: str, ok: bool, good: str, bad: str) -> None:
        checks.append(GateCheck(check_id, "pass" if ok else "fail", good if ok else bad))

    add("milestones", st["state"] in ("DONE", "READY"), "every planned milestone is accepted",
        f"the project is in {st['state']} ({st.get('milestone')}): not every milestone is accepted")
    verify = st.get("last_verify") or {}
    add("verify_head", verify.get("sha") == head and bool(verify.get("passed")), f"verify passed on {head[:12]}",
        f"no passing verify on HEAD {head[:12]} (last: {str(verify.get('sha'))[:12]}, passed={verify.get('passed')})")
    if use_evaluator:
        ev = st.get("last_eval") or {}
        add("eval_head", ev.get("sha") == head and bool(ev.get("passed")), f"the Evaluator accepted {head[:12]}",
            f"no Evaluator acceptance on HEAD {head[:12]}")
    names = zipfile.ZipFile(zip_path).namelist() if zip_path.is_file() else []
    add("archive", "index.html" in names, f"{zip_path.name}: index.html at the root, {len(names)} files",
        "the archive has no index.html at its root")
    if unpacked is not None:
        mb = unpacked / 1e6
        add("archive_size", mb <= MAX_UNPACKED_MB, f"{mb:.1f} MB unpacked", f"{mb:.1f} MB unpacked > {MAX_UNPACKED_MB} MB")
    for name, size in (("icon", ICON), ("cover", COVER)):
        img = images.get(name)
        add(name, bool(img) and img["distinct_colors"] > 1, f"{name}.png {size[0]}x{size[1]}",
            f"no usable {name}.png {size[0]}x{size[1]} (not captured or a single color)")
    add("screenshots", len(screenshots) >= 2, f"{len(screenshots)} screenshots", "fewer than 2 screenshots")
    missing = [f"{lang}.{f}" for lang in LANGS for f in META_FIELDS if not str(((meta or {}).get(lang) or {}).get(f, "")).strip()]
    add("metadata", meta is not None and not missing, "title, descriptions and how to play in en and ru",
        "metadata missing" if meta is None else f"empty fields: {', '.join(missing)}")
    long_titles = [lang for lang in LANGS if len(str(((meta or {}).get(lang) or {}).get("title", ""))) > 50]
    if meta is not None:
        add("title_length", not long_titles, "titles fit in 50 characters", f"title longer than 50 characters: {long_titles}")
    # Images rendered from the game are fine for a universal package; portals such as Yandex Games
    # want drawn art that is not a screenshot. That needs a person (or paid generation, stage 6).
    checks.append(GateCheck("art_review", "manual", "icon and cover are screenshots of the start screen: "
                                                    "replace them with drawn art for portals that require it"))
    return checks


def build(project: Project, godot: GodotRunner, display: Display | None) -> dict[str, Any]:
    """Export, package and capture images for HEAD. Returns paths and facts for the gate."""
    repo = project.repo()
    head = repo.head()
    out = project.harness_dir / "release" / head[:12]
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="harness_release_") as tmp:
        copy = Path(tmp) / "game"
        shutil.copytree(project.repo_dir, copy, ignore=shutil.ignore_patterns(".git", "build"))
        export = export_web(godot, copy, out / "web", out / "export.log")
    zip_path = out / f"{project.name}-{head[:12]}.zip"
    unpacked = package(out / "web", zip_path) if export.status == "pass" else None
    images = capture_images(godot, project.repo_dir, out, display)
    return {"head": head, "out": out, "export": export, "zip": zip_path, "unpacked": unpacked, "images": images}


def write_gate(out: Path, verdict: str, checks: list[GateCheck], head: str) -> Path:
    path = out / "final_gate.json"
    path.write_text(json.dumps({"sha": head, "verdict": verdict, "checks": [asdict(c) for c in checks]},
                               indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
