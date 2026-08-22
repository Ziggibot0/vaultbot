"""Read-only guard for ``code_run`` (issue #207, Gap 2).

``code_run`` executes LLM-authored Python in a subprocess to *test* code
before adopting it. Its documented contract is "TESTING only — do NOT use
it to write or modify files." But nothing actually enforced that: a
determined model could still write backend source via ``open(..., 'w')``
or ``Path.write_text``, bypassing the doc-source gate on ``safe_write``.

This module closes that hole by injecting a guard preamble into the
subprocess that blocks the common file-write primitives, so the only way
to modify backend source is through the gated ``safe_write``.

This is defense-in-depth, NOT a full sandbox (see ``subprocess_utils.py``
and ``SECURITY.md`` for the residual-risk note). A determined attacker who
can inject prompts could still exfiltrate files via ``subprocess`` or
``os.write`` on a raw fd — the guard raises the bar against the *accidental*
bypass (the model reaching for ``open(..., 'w')`` out of habit), not the
adversarial one.

The guard is opt-out: ``code_run(..., allow_write=True)`` skips the
preamble for the rare legitimate case (e.g. a test that must write a temp
file). The default is read-only.
"""

from __future__ import annotations

# The preamble is injected at the top of the subprocess's ``-c`` code,
# before the LLM-authored code runs. It monkeypatches the write primitives
# in the child's own namespace — the parent backend is untouched.
GUARD_PREAMBLE = r"""
import builtins as _builtins
import os as _os
import pathlib as _pathlib
import shutil as _shutil

def _code_run_blocked(*_a, **_k):
    raise PermissionError(
        "code_run is read-only: file writes are blocked. "
        "Use safe_write (Python), js_safe_write (JS), or vault_safe_write "
        "(markdown) to modify files."
    )

# --- builtins.open: reject write/append/update modes ---
_orig_open = _builtins.open
def _guarded_open(file, mode="r", *args, **kwargs):
    _m = mode if isinstance(mode, str) else "r"
    if any(_c in _m for _c in "wax+"):
        _code_run_blocked()
    return _orig_open(file, mode, *args, **kwargs)
_builtins.open = _guarded_open

# --- os.open: reject write/creat/trunc/append flags ---
if hasattr(_os, "open"):
    _orig_os_open = _os.open
    _WRITE_FLAGS = (
        getattr(_os, "O_WRONLY", 0)
        | getattr(_os, "O_RDWR", 0)
        | getattr(_os, "O_CREAT", 0)
        | getattr(_os, "O_APPEND", 0)
        | getattr(_os, "O_TRUNC", 0)
    )
    def _guarded_os_open(path, flags, *args, **kwargs):
        if flags & _WRITE_FLAGS:
            _code_run_blocked()
        return _orig_os_open(path, flags, *args, **kwargs)
    _os.open = _guarded_os_open

# --- pathlib.Path write methods ---
_pathlib.Path.write_text = _code_run_blocked
_pathlib.Path.write_bytes = _code_run_blocked
_pathlib.Path.open = _guarded_open

# --- shutil copy/move/delete ---
for _n in ("copy", "copy2", "copyfile", "copytree", "move", "rmtree"):
    if hasattr(_shutil, _n):
        setattr(_shutil, _n, _code_run_blocked)

# --- os destructive/rename/mkdir ops ---
for _n in ("remove", "unlink", "rename", "replace", "rmdir", "mkdir", "makedirs"):
    if hasattr(_os, _n):
        setattr(_os, _n, _code_run_blocked)
"""


def build_guard_preamble() -> str:
    """Return the read-only guard preamble to prepend to ``code_run`` code."""
    return GUARD_PREAMBLE
