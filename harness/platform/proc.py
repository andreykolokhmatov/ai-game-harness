"""Running external processes with a timeout and killing the whole process tree.

Platform-specific parts (process groups, signals) live only here.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

IS_WINDOWS = sys.platform == "win32"


@dataclass(frozen=True)
class ProcResult:
    returncode: int | None  # None when the process was killed on timeout
    stdout: str
    stderr: str
    timed_out: bool
    duration_s: float

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.returncode == 0


def pid_alive(pid: int) -> bool:
    """True if a process with this pid exists on this machine."""
    if pid <= 0:
        return False
    if IS_WINDOWS:
        # os.kill on Windows terminates the process, so ask the OS directly.
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by another user
    return True


def new_group_kwargs() -> dict:
    """Popen kwargs that start the child in its own process group."""
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def interrupt(proc: subprocess.Popen) -> None:
    """Soft stop (Ctrl+C equivalent) for a child started with new_group_kwargs()."""
    if proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(proc.pid, signal.SIGINT)
    except (ProcessLookupError, OSError):
        pass


def terminate_tree(proc: subprocess.Popen, grace_s: float = 5.0) -> None:
    """Ask the process group to stop, then kill it after grace_s."""
    if proc.poll() is not None:
        return
    if IS_WINDOWS:
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        except OSError:
            pass
        try:
            proc.wait(timeout=grace_s)
            return
        except subprocess.TimeoutExpired:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=grace_s)
            return
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
    proc.wait()


def run(
    cmd: Sequence[str],
    *,
    timeout_s: float,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    grace_s: float = 5.0,
) -> ProcResult:
    """Run cmd, capture text output, kill the process tree on timeout.

    Raises FileNotFoundError if the executable does not exist.
    """
    start = time.monotonic()
    proc = subprocess.Popen(
        list(cmd),
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **new_group_kwargs(),
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
        timed_out = False
    except subprocess.TimeoutExpired:
        terminate_tree(proc, grace_s)
        stdout, stderr = proc.communicate()
        timed_out = True
    return ProcResult(
        returncode=None if timed_out else proc.returncode,
        stdout=stdout or "",
        stderr=stderr or "",
        timed_out=timed_out,
        duration_s=time.monotonic() - start,
    )
