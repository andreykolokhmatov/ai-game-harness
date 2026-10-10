"""Web checks (stage 4): Web-Test export, a local HTTP server and Chromium through Playwright.

The Web-Test build is the game's own "Web" export preset applied to a temporary copy of the
repository with the Harness probe (gdscript/web_probe.gd) added as the last autoload. The probe
publishes `window.harnessState`; the browser checks read it:
  web_export   the copy exports with the "Web" preset
  web_desktop  1280x720: loads, no console errors, screenshot
  web_mobile   phone emulation with touch: loads, a tap switches Platform to touch mode,
               portrait and landscape screenshots, no console errors
  web_focus    hiding the page mutes audio, showing it again unmutes
The single-threaded export needs no COOP/COEP headers, so a plain static server is enough.
"""

from __future__ import annotations

import base64
import functools
import http.server
import importlib.util
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.verify.godot import GodotRunner
from harness.verify.report import CheckResult

WEB_PRESET = "Web"
PROBE_GD = Path(__file__).with_name("gdscript") / "web_probe.gd"
PROBE_RES = "res://harness_probe/web_probe.gd"
CHECK_IDS = ("web_export", "web_desktop", "web_mobile", "web_focus")
DESKTOP = (1280, 720)
PHONE = (390, 844)
LOAD_TIMEOUT_S = 60
SETTLE_MS = 1500  # after ready: let the main scene run before judging the console
# SwiftShader gives WebGL 2 without a GPU (headless servers, CI).
CHROMIUM_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
_PRESET_NAME = re.compile(r'^name="([^"]*)"', re.MULTILINE)

# Counts colors in a 160x160 copy of a PNG, the same blank-frame rule as the Godot screens check.
_DISTINCT_COLORS_JS = """async (b64) => {
  const img = new Image(); img.src = 'data:image/png;base64,' + b64; await img.decode();
  const c = document.createElement('canvas'); c.width = 160; c.height = 160;
  const ctx = c.getContext('2d'); ctx.drawImage(img, 0, 0, 160, 160);
  const d = ctx.getImageData(0, 0, 160, 160).data; const seen = new Set();
  for (let i = 0; i < d.length; i += 4) seen.add((d[i] << 16) | (d[i + 1] << 8) | d[i + 2]);
  return seen.size;
}"""
_HIDE_PAGE_JS = """() => {
  Object.defineProperty(document, 'hidden', {value: true, configurable: true});
  Object.defineProperty(document, 'visibilityState', {value: 'hidden', configurable: true});
  document.dispatchEvent(new Event('visibilitychange'));
  window.dispatchEvent(new Event('blur'));
}"""
_SHOW_PAGE_JS = """() => {
  Object.defineProperty(document, 'hidden', {value: false, configurable: true});
  Object.defineProperty(document, 'visibilityState', {value: 'visible', configurable: true});
  document.dispatchEvent(new Event('visibilitychange'));
  window.dispatchEvent(new Event('focus'));
}"""


def playwright_available() -> tuple[bool, str]:
    if importlib.util.find_spec("playwright") is None:
        return False, "playwright is not installed (uv sync --extra web, then: playwright install chromium)"
    return True, "playwright"


def has_web_preset(repo: Path) -> bool:
    path = repo / "export_presets.cfg"
    return path.is_file() and WEB_PRESET in _PRESET_NAME.findall(path.read_text(encoding="utf-8"))


def add_autoload(project_godot: Path, name: str, res_path: str) -> None:
    """Append an autoload as the last entry of [autoload] (so the game's autoloads exist first)."""
    lines = project_godot.read_text(encoding="utf-8").splitlines()
    entry = f'{name}="*{res_path}"'
    if "[autoload]" not in lines:
        lines += ["", "[autoload]", "", entry]
    else:
        first = lines.index("[autoload]") + 1
        end = next((i for i in range(first, len(lines)) if lines[i].startswith("[")), len(lines))
        while end > first and not lines[end - 1].strip():
            end -= 1
        lines.insert(end, entry)
    project_godot.write_text("\n".join(lines) + "\n", encoding="utf-8")


def prepare_test_project(repo: Path, target: Path) -> Path:
    """Copy the game (with its .godot import cache, without Git and old builds) and add the probe."""
    shutil.copytree(repo, target, ignore=shutil.ignore_patterns(".git", "build"))
    probe = target / PROBE_RES.removeprefix("res://")
    probe.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(PROBE_GD, probe)
    add_autoload(target / "project.godot", "HarnessProbe", PROBE_RES)
    return target


@contextmanager
def serve(directory: Path) -> Iterator[str]:
    """Static HTTP server on a free localhost port; yields the index.html URL."""
    handler = functools.partial(_QuietHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/index.html"
    finally:
        server.shutdown()
        server.server_close()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (stdlib signature)
        pass


@dataclass
class _Page:
    """One browser page with its console collected."""

    page: Any
    errors: list[str] = field(default_factory=list)
    warnings: int = 0
    load_s: float = 0.0

    def state(self) -> dict[str, Any]:
        import json

        raw = self.page.evaluate("window.harnessState || null")
        return json.loads(raw) if raw else {}

    def screenshot(self, out_dir: Path, name: str) -> dict:
        path = out_dir / "screenshots" / f"{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.page.screenshot(path=str(path))
        colors = self.page.evaluate(_DISTINCT_COLORS_JS, base64.b64encode(data).decode("ascii"))
        size = self.page.viewport_size
        return {"file": f"screenshots/{name}.png", "label": name, "size": [size["width"], size["height"]],
                "distinct_colors": colors, "viewport": "web"}


def _open(browser: Any, url: str, *, viewport: tuple[int, int], mobile: bool) -> _Page:
    options: dict[str, Any] = {"viewport": {"width": viewport[0], "height": viewport[1]}}
    if mobile:
        options.update(is_mobile=True, has_touch=True, device_scale_factor=2)
    page = browser.new_context(**options).new_page()
    wrapped = _Page(page)

    def on_console(msg: Any) -> None:
        if msg.type == "error":
            wrapped.errors.append(msg.text)
        elif msg.type == "warning":
            wrapped.warnings += 1

    page.on("console", on_console)
    page.on("pageerror", lambda exc: wrapped.errors.append(f"uncaught: {exc}"))
    started = time.monotonic()
    page.goto(url)
    page.wait_for_function("window.harnessState && JSON.parse(window.harnessState).ready",
                           timeout=LOAD_TIMEOUT_S * 1000)
    wrapped.load_s = round(time.monotonic() - started, 2)
    page.wait_for_timeout(SETTLE_MS)
    return wrapped


def _console_problems(page: _Page) -> list[dict]:
    return [{"message": f"console error: {text}", "at": None} for text in page.errors]


def _blank(shot: dict) -> dict | None:
    if shot["distinct_colors"] <= 1:
        return {"message": f"{shot['label']}: the screen is a single color (nothing rendered?)", "at": shot["file"]}
    return None


def _result(check_id: str, ok_summary: str, problems: list[dict], started: float, warnings: int = 0) -> CheckResult:
    duration = round(time.monotonic() - started, 2)
    if problems:
        return CheckResult(check_id, "fail", problems[0]["message"], problems, warnings, duration)
    return CheckResult(check_id, "pass", ok_summary, [], warnings, duration)


def check_desktop(browser: Any, url: str, out_dir: Path, shots: list[dict]) -> tuple[CheckResult, _Page | None]:
    started = time.monotonic()
    try:
        page = _open(browser, url, viewport=DESKTOP, mobile=False)
    except Exception as exc:  # Playwright timeout or a crashed page
        return CheckResult("web_desktop", "fail", f"the page did not become ready: {exc}".splitlines()[0]), None
    shot = page.screenshot(out_dir, "web_desktop")
    shots.append(shot)
    problems = _console_problems(page) + [p for p in [_blank(shot)] if p]
    return _result("web_desktop", f"loaded in {page.load_s} s, no console errors", problems, started, page.warnings), page


def check_mobile(browser: Any, url: str, out_dir: Path, shots: list[dict]) -> CheckResult:
    started = time.monotonic()
    try:
        page = _open(browser, url, viewport=PHONE, mobile=True)
    except Exception as exc:
        return CheckResult("web_mobile", "fail", f"the page did not become ready on a phone: {exc}".splitlines()[0])
    problems: list[dict] = []
    page.page.touchscreen.tap(PHONE[0] // 2, PHONE[1] // 2)
    page.page.wait_for_timeout(500)
    platform = page.state().get("platform")
    if platform is not None and not platform.get("touch_mode"):
        problems.append({"message": "Platform.touch_mode is false after a tap: touch controls will not show", "at": None})
    portrait = page.screenshot(out_dir, "web_mobile_portrait")
    page.page.set_viewport_size({"width": PHONE[1], "height": PHONE[0]})
    page.page.wait_for_timeout(SETTLE_MS)
    landscape = page.screenshot(out_dir, "web_mobile_landscape")
    shots += [portrait, landscape]
    problems = _console_problems(page) + problems + [p for p in (_blank(portrait), _blank(landscape)) if p]
    page.page.context.close()
    return _result("web_mobile", f"loaded in {page.load_s} s, touch mode on tap, portrait and landscape",
                   problems, started, page.warnings)


def check_focus(page: _Page) -> CheckResult:
    """Hidden page -> audio muted; visible again -> unmuted (Yandex Games requirement 1.3)."""
    started = time.monotonic()
    page.page.evaluate(_HIDE_PAGE_JS)
    page.page.wait_for_timeout(500)
    hidden = page.state()
    page.page.evaluate(_SHOW_PAGE_JS)
    page.page.wait_for_timeout(500)
    shown = page.state()
    problems = []
    if not hidden.get("muted"):
        problems.append({"message": "audio is not muted while the page is hidden (Platform must mute on focus loss)", "at": None})
    if shown.get("muted"):
        problems.append({"message": "audio stays muted after the page is visible again", "at": None})
    return _result("web_focus", "audio muted while hidden, unmuted on return", problems, started)


def run_web_checks(godot: GodotRunner, repo: Path, out_dir: Path) -> tuple[list[CheckResult], list[dict]]:
    """All web checks; returns the checks and screenshot records (paths relative to out_dir)."""
    ok, reason = playwright_available()
    if not ok:
        return [CheckResult(c, "skipped", f"skipped: {reason}") for c in CHECK_IDS], []
    if not has_web_preset(repo):
        failed = CheckResult("web_export", "fail", f'export_presets.cfg has no "{WEB_PRESET}" preset')
        return [failed] + [CheckResult(c, "skipped", "skipped: no web build") for c in CHECK_IDS[1:]], []

    out_dir.mkdir(parents=True, exist_ok=True)
    shots: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="harness_web_") as tmp:
        project = prepare_test_project(repo, Path(tmp) / "game")
        web_dir = Path(tmp) / "web"
        web_dir.mkdir()
        started = time.monotonic()
        godot.import_project(project)
        run = godot.run(project, ["--export-release", WEB_PRESET, str(web_dir / "index.html")], timeout_s=300)
        (out_dir / "web_export.log").write_text(run.output, encoding="utf-8")
        size_mb = sum(p.stat().st_size for p in web_dir.iterdir()) / 1e6
        problems = [{"message": e.message, "at": e.location} for e in run.errors]
        if run.timed_out:
            problems.insert(0, {"message": "export timed out", "at": None})
        if not (web_dir / "index.html").is_file():
            problems.insert(0, {"message": "export produced no index.html", "at": None})
        export = _result("web_export", f"exported, {size_mb:.1f} MB", problems, started, len(run.warnings))
        export.log = "web_export.log"
        if export.status == "fail":
            return [export] + [CheckResult(c, "skipped", "skipped: web export failed") for c in CHECK_IDS[1:]], []

        from playwright.sync_api import sync_playwright

        with serve(web_dir) as url, sync_playwright() as pw:
            browser = pw.chromium.launch(args=CHROMIUM_ARGS)
            try:
                desktop, page = check_desktop(browser, url, out_dir, shots)
                focus = check_focus(page) if page else CheckResult("web_focus", "skipped", "skipped: page did not load")
                if page:
                    page.page.context.close()  # software WebGL is CPU-bound: one page at a time
                mobile = check_mobile(browser, url, out_dir, shots)
            finally:
                browser.close()
    return [export, desktop, mobile, focus], shots
