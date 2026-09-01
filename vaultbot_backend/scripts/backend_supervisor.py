#!/usr/bin/env python3
"""Self-respawn supervisor for the VaultBot backend.

Deterministic, no-LLM watchdog whose ONLY job: wait for the backend process
to die, then after a short grace window, relaunch the backend with the same
interpreter + args unless a stop-marker is present. This is what lets the
backend restart itself regardless of WHO launched it (Obsidian plugin, CLI,
terminal, scheduler) — because the supervisor is a detached process that
survives the backend's parent.

Marker contract (vaultbot_backend/backend_stop_request):
  - /shutdown writes it -> supervisor sees it and exits WITHOUT relaunching
    (a legit shutdown is never resurrected; no zombie backend).
  - /restart clears it, then the supervisor's relaunch (or the plugin's own
    restartBackend) starts a fresh backend.

USAGE (from main.py, after acquire_lock):
    python backend_supervisor.py <parent_pid> <relaunch_cmd...>
where <relaunch_cmd...> is the backend command WITHOUT the interpreter
(the supervisor prepends sys.executable). Detached on both platforms.

Path handling: stop-marker and relaunch script are resolved relative to this
file's directory (not cwd), so the supervisor keeps working even if the
backend's working directory changes (e.g. the plugin chdir's elsewhere).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

# Same file the backend /shutdown writes (main.py writes it via PID_FILE
# sibling). Resolve absolutely from THIS file, never from os.getcwd().
_STOP_MARKER = Path(__file__).resolve().parent.parent / "backend_stop_request"
# The backend script is the first element of the relaunch command (argv[2]);
# resolve it to an absolute path so we don't depend on cwd at relaunch time.
_BACKEND_SCRIPT = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else None


def _pid_alive(pid: int) -> bool:
    """Robust cross-platform liveness check (mirrors main.py._check_pid_alive)."""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            # PROCESS_QUERY_LIMITED_INFORMATION (0x1000) works for other
            # users/elevation on modern Windows; 0x0001 (QUERY) is a fallback.
            for access in (0x1000, 0x0001):
                handle = ctypes.windll.kernel32.OpenProcess(access, False, pid)
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                    return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except OSError:
        return False


def _relaunch(args: list[str]) -> None:
    """Relaunch the backend detached. args = original relaunch command (argv[2:])."""
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(
        [sys.executable, *args],
        env=os.environ,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )


def _clear_marker_if_present() -> None:
    """Remove the stop-marker (a shutdown that was cancelled / no-op)."""
    import contextlib

    with contextlib.suppress(OSError):
        _STOP_MARKER.unlink()


def _other_backend_already_up(pid: int) -> bool:
    """True if a DIFFERENT backend process is already running.

    Guards against double-relaunch: with two supervisors armed (e.g. after
    the plugin and a manual launch both booted), only the first relaunch to
    acquire the pid-lock should win. A sibling that relaunched first will
    have written a NEW live pid into vaultbot.pid; we then stand down so we
    don't spawn a second backend on the same port.

    IMPORTANT: on a legit /restart or /shutdown the pid file is GONE (the
    shutting-down backend unlinked it via release_lock), so absence of the
    file must NOT block us — it means no backend is up and we should
    proceed to relaunch. Only a file that points at a live pid OTHER than
    the one we were watching signals that a sibling already took over.
    """
    import contextlib

    pid_file = Path(__file__).resolve().parent.parent / "vaultbot.pid"
    with contextlib.suppress(OSError):
        recorded = pid_file.read_text(encoding="utf-8").strip()
        if recorded and recorded != str(pid):
            # A different backend is recorded — is it actually alive?
            import ctypes

            try:
                other = int(recorded)
            except ValueError:
                return False
            if os.name == "nt":
                for access in (0x1000, 0x0001):
                    handle = ctypes.windll.kernel32.OpenProcess(access, False, other)
                    if handle:
                        ctypes.windll.kernel32.CloseHandle(handle)
                        return True
                return False
            try:
                os.kill(other, 0)
                return True
            except OSError:
                return False
    return False


def main() -> int:
    if len(sys.argv) < 3:
        print(
            "usage: backend_supervisor.py <parent_pid> <relaunch_cmd...>",
            file=sys.stderr,
        )
        return 1
    try:
        parent_pid = int(sys.argv[1])
    except ValueError:
        return 1
    relaunch_args = sys.argv[2:]

    # Wait for the backend process to exit.
    while _pid_alive(parent_pid):
        time.sleep(1.0)

    # Grace window so the backend can finish flushing / the OS frees the port.
    time.sleep(2.0)

    # Stop-marker present? A legit shutdown was requested — do NOT resurrect.
    if _STOP_MARKER.exists():
        _clear_marker_if_present()
        return 0

    # Guard against double-relaunch: stand down only if a DIFFERENT backend
    # (a sibling supervisor's relaunch) already took the port. Absence of the
    # pid file means we're the expected relaunch — proceed. (The pid-lock
    # also self-heals a true duplicate: its acquire_lock sees the winner's
    # live pid and sys.exits.)
    if _other_backend_already_up(parent_pid):
        return 0

    # Relaunch. Best-effort: if it fails, exit quietly rather than loop.
    try:
        _relaunch(relaunch_args)
    except Exception:  # noqa: BLE001 — best-effort; never want a crash-loop
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
