import sys
import time

import pytest

from harness.platform import proc


def test_run_captures_output():
    result = proc.run([sys.executable, "-c", "print('hi'); import sys; sys.exit(3)"], timeout_s=30)
    assert result.stdout.strip() == "hi"
    assert result.returncode == 3
    assert not result.timed_out
    assert not result.ok


def test_timeout_kills_process_tree(tmp_path):
    marker = tmp_path / "child_alive"
    # Parent starts a child that would write the marker after 3 s; both must die on timeout.
    child = f"import time; time.sleep(3); open({str(marker)!r}, 'w').write('x')"
    parent = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(60)"
    start = time.monotonic()
    result = proc.run([sys.executable, "-c", parent], timeout_s=1, grace_s=1)
    assert result.timed_out
    assert result.returncode is None
    assert time.monotonic() - start < 10
    time.sleep(3)
    assert not marker.exists()


def test_missing_executable():
    with pytest.raises(FileNotFoundError):
        proc.run(["definitely-not-a-real-binary-xyz"], timeout_s=5)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell shim")
def test_render_shim_wraps_display_prefix(tmp_path):
    import subprocess

    from harness.platform.env import write_render_shim

    fake = tmp_path / "fake_godot.sh"
    fake.write_text('#!/bin/sh\necho "$@"\n')
    fake.chmod(0o755)
    shim = write_render_shim(tmp_path / "bin", "godot-render", fake, ["env", "RENDER=1"])
    out = subprocess.run([str(shim), "--path", "a b"], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "--audio-driver Dummy --path a b"
    headless = write_render_shim(tmp_path / "bin2", "godot-render", fake, None)
    assert "--headless" in subprocess.run([str(headless)], capture_output=True, text=True).stdout
