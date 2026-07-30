"""subprocess_utils.py — hide console windows on Windows for all subprocess calls.

WHY THIS EXISTS
----------------
On Windows, every ``subprocess.run()`` call without ``creationflags=
CREATE_NO_WINDOW`` spawns a visible console window that flashes on screen.
VaultBot makes many subprocess calls (code_run, safe_write verification,
subagent research, procedure execution, preflight checks, ollama probes).
Each one was popping a black cmd.exe window that stole focus from Obsidian.

Instead of adding ``creationflags=`` to every call site individually (error-
prone, easy to forget on new code), this module provides drop-in ``run()``
and ``Popen()`` wrappers that inject the flag automatically on Windows and
are transparent pass-throughs on other platforms.

USAGE
-----
    # Before:
    import subprocess
    result = subprocess.run([sys.executable, script], capture_output=True, ...)
    proc = subprocess.Popen([sys.executable, script], stdout=subprocess.PIPE, ...)

    # After:
    from subprocess_utils import run as subprocess_run, Popen as subprocess_popen
    result = subprocess_run([sys.executable, script], capture_output=True, ...)
    proc = subprocess_popen([sys.executable, script], stdout=subprocess.PIPE, ...)

``subprocess.TimeoutExpired``, ``subprocess.Popen`` and other exceptions are
still raised from the real ``subprocess`` functions inside the wrappers, so
existing ``except`` blocks work unchanged.
"""

from __future__ import annotations

import subprocess
import sys

# On Windows, CREATE_NO_WINDOW (0x08000000) prevents a console window from
# flashing for every subprocess.run/Popen call. On other platforms the flag
# does not exist and is a no-op (0).
if sys.platform == "win32":
    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
else:
    CREATE_NO_WINDOW = 0


def run(*args, **kwargs):
    """Drop-in replacement for ``subprocess.run`` that hides console windows on Windows.

    Identical signature to ``subprocess.run`` — just injects
    ``creationflags=CREATE_NO_WINDOW`` (on Windows) if the caller did not
    already specify ``creationflags``. On non-Windows platforms this is a
    transparent pass-through.
    """
    if sys.platform == "win32" and "creationflags" not in kwargs:
        kwargs["creationflags"] = CREATE_NO_WINDOW
    return subprocess.run(*args, **kwargs)


def Popen(*args, **kwargs):
    """Drop-in replacement for ``subprocess.Popen`` that hides console windows on Windows.

    Identical signature to ``subprocess.Popen`` — just injects
    ``creationflags=CREATE_NO_WINDOW`` (on Windows) if the caller did not
    already specify ``creationflags``. On non-Windows platforms this is a
    transparent pass-through.
    """
    if sys.platform == "win32" and "creationflags" not in kwargs:
        kwargs["creationflags"] = CREATE_NO_WINDOW
    return subprocess.Popen(*args, **kwargs)