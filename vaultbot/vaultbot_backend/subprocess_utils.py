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


# ── Secret-scrubbed environment for LLM-spawned subprocesses ───────────────
# Any code the agent generates (code_run, procedure code steps, subagent
# research) runs in a subprocess that must NOT inherit API keys, tokens, or
# passwords. scrubbed_env() returns a copy of os.environ with every variable
# whose name matches a secret pattern removed. Pattern-based (not a hardcoded
# list) so new providers added to providers.json / .env are auto-protected.
#
# Matching patterns (case-insensitive suffix):
#   *_API_KEY, *_KEY, *_SECRET, *_TOKEN, *_PASSWORD, *_PASSPHRASE, *_CREDENTIAL
#
# Everything else (PATH, PYTHONPATH, VAULT_PATH, PROCEDURE_*, SYSTEMROOT,
# etc.) is passed through unchanged. Callers that need a specific secret
# (none currently do — the backend talks to providers from the parent
# process, never from an LLM-spawned child) can re-add it explicitly.

import os
import re

# Suffix patterns on env-var names that mark a secret. Compiled once.
# Case-insensitive. A name matches if it ENDS with one of these (the
# leading underscore is part of the suffix, e.g. "LLM_API_KEY" ends with
# "_API_KEY"). "KEY" alone is too broad (e.g. "KEYBOARD_LAYOUT") so every
# pattern includes a leading underscore.
_SECRET_SUFFIX_RE = re.compile(
    r"(?:_API_KEY|_KEY|_SECRET|_TOKEN|_PASSWORD|_PASSPHRASE|_CREDENTIAL)$",
    re.IGNORECASE,
)


def scrubbed_env() -> dict[str, str]:
    """Return a copy of ``os.environ`` with all secret-shaped keys removed.

    Use this as the ``env=`` argument for any subprocess that executes
    LLM-authored code (``code_run``, procedure code steps, subagent
    research scripts). The parent process keeps its secrets; the child
    never sees them.

    Secret pattern: any env var whose name ends (case-insensitive) with
    ``_API_KEY``, ``_KEY``, ``_SECRET``, ``_TOKEN``, ``_PASSWORD``,
    ``_PASSPHRASE``, or ``_CREDENTIAL``. This is a denylist-by-suffix so
    new providers don't need a code change to be protected.

    Returns a NEW dict; the caller may add non-secret overrides (PYTHONPATH,
    VAULT_PATH, PROCEDURE_*) before passing it to ``subprocess.run``.
    """
    return {
        name: value
        for name, value in os.environ.items()
        if not _SECRET_SUFFIX_RE.search(name)
    }


# ── Resource limits for LLM-spawned subprocesses ───────────────────────────
# Defense-in-depth against runaway LLM-authored code: a memory cap (RLIMIT_AS)
# stops an infinite allocation from OOM-killing the host, a CPU-seconds cap
# (RLIMIT_CPU) stops a busy loop from pegging a core forever, and a fork cap
# (RLIMIT_NPROC) stops a fork bomb. The wall-clock timeout the caller already
# passes is the primary guard; these are belt-and-suspenders.
#
# POSIX only. On Windows the resource module is unavailable, so the helper is
# a no-op — the wall-clock timeout is the only limit there. A Windows Job
# Object wrapper is a documented future hardening item (see SECURITY.md).
#
# This is NOT a full sandbox: the child still has filesystem and network
# access. The combination of subprocess isolation + secret-scrubbed env +
# resource limits + timeout raises the bar, but a determined attacker who
# can inject prompts into the agent could still exfiltrate vault files. The
# threat model (SECURITY.md) records the residual risk and the roadmap.

if sys.platform != "win32":
    import resource as _resource

    # 512 MiB address-space cap. Matches the cap already used ad-hoc in some
    # call sites; tunable via VAULTBOT_CODE_RUN_MEM_MB.
    _MEM_BYTES = int(os.environ.get("VAULTBOT_CODE_RUN_MEM_MB", "512")) * 1024 * 1024
    # 15 CPU-seconds (the wall-clock timeout is typically 15-120s; CPU time
    # is the portion actually spent on-core, so this catches busy loops
    # without killing legitimate I/O-bound waits). Tunable.
    _CPU_SECONDS = int(os.environ.get("VAULTBOT_CODE_RUN_CPU_SECONDS", "15"))
    # 64 processes — generous for normal tool use, stops a naive fork bomb.
    _NPROC = int(os.environ.get("VAULTBOT_CODE_RUN_NPROC", "64"))


def _posix_preexec() -> None:
    """``preexec_fn`` implementation (POSIX only). Applies RLIMIT_AS, CPU, NPROC."""
    # RLIMIT_AS: total address space (virtual memory).
    _resource.setrlimit(_resource.RLIMIT_AS, (_MEM_BYTES, _MEM_BYTES))
    # RLIMIT_CPU: CPU seconds (soft, hard). SIGXCPU kills the child on exceed.
    _resource.setrlimit(_resource.RLIMIT_CPU, (_CPU_SECONDS, _CPU_SECONDS))
    # RLIMIT_NPROC: max processes this user may spawn. Catches fork bombs.
    # Some platforms (e.g. macOS) don't support RLIMIT_NPROC — ignore if so.
    try:
        _resource.setrlimit(_resource.RLIMIT_NPROC, (_NPROC, _NPROC))
    except (ValueError, OSError):
        # Unsupported or the limit is lower than the parent's current count
        # (e.g. the backend itself already has many threads). Degrade
        # gracefully — the timeout and mem cap are still in effect.
        pass


# The value to pass as ``preexec_fn=`` to subprocess.run / Popen. On POSIX
# this is the callable that applies resource limits; on Windows it is None
# (subprocess REJECTS a non-None preexec_fn on Windows — the parameter
# itself is unsupported, not just the function body). Callers can pass this
# unconditionally: ``preexec_fn=preexec_fn``.
preexec_fn = _posix_preexec if sys.platform != "win32" else None
