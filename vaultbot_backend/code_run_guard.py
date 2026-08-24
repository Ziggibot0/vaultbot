"""Guard preamble for ``code_run`` (issues #207 and #229).

``code_run`` executes LLM-authored Python in a subprocess to *test* code
before adopting it. Its contract is "TESTING only — do NOT use it to write
or modify files, exfiltrate secrets, or talk to the network." Nothing at
the OS level actually enforced that.

This module injects a *guard preamble* into the subprocess that:

1. **Blocks file writes** (issue #207) — ``open(..., 'w')``,
   ``Path.write_text``, ``shutil.copy``, ``os.remove``, ... — so the only
   sanctioned way to modify backend source is the gated ``safe_write``.

2. **Blocks network egress** (issue #229) — importing ``socket``,
   ``urllib``, ``requests``, ``httpx``, ``aiohttp``, ``websocket(s)``,
   ``http.client`` and friends raises, so a malicious model cannot call
   ``requests.post(exfil_url, ...)`` to exfiltrate what it reads.

3. **Blocks reads of secret files** (issue #229) — ``open('.env')``,
   ``Path('providers.json').read_text()``, ``*_tokens.json``,
   ``*_config.json`` *inside the repo root* raise. ``scrubbed_env()``
   strips secret-named env vars but not secret files on disk; this closes
   that hole. Scoped to the repo root so a test that reads an unrelated
   ``config.json`` (e.g. a package fixture) is not falsely blocked.

This is defense-in-depth, NOT a true OS sandbox (see ``subprocess_utils.py``
and ``SECURITY.md`` for the residual-risk note). A determined attacker who
can inject prompts could still reach for a lower-level primitive — the guard
raises the bar against the *accidental* and *opportunistic* bypass (the model
reaching for ``open(..., 'w')`` or ``requests.post`` out of habit), not a
fully adversarial one. A real container / Job Object sandbox is the roadmap
hardening item.

The guard is opt-out: ``code_run(..., allow_write=True)`` skips the preamble
for the rare legitimate case (e.g. a test that must write a temp file). The
default is read-only + network-isolated + secret-protected.
"""

from __future__ import annotations

# The preamble is injected at the top of the subprocess's ``-c`` code,
# before the LLM-authored code runs. It monkeypatches the write, network,
# and secret-read primitives in the child's own namespace — the parent
# backend is untouched. ``{repo_root}`` is filled in by
# ``build_guard_preamble`` with the absolute repo root, used to scope the
# secret-file read block.
GUARD_PREAMBLE_TEMPLATE = r"""
import builtins as _builtins
import os as _os
import pathlib as _pathlib
import shutil as _shutil

_REPO_ROOT = {repo_root}

def _code_run_write_blocked(*_a, **_k):
    raise PermissionError(
        "code_run is read-only: file writes are blocked. "
        "Use safe_write (Python), js_safe_write (JS), or vault_safe_write "
        "(markdown) to modify files."
    )

def _code_run_secret_read_blocked(*_a, **_k):
    raise PermissionError(
        "code_run cannot read secret/credential files "
        "(.env, providers.json, *_tokens.json, *_config.json). "
        "If your test needs a value from one, don't — tests must be "
        "self-contained and use no real credentials."
    )

def _code_run_network_blocked(*_a, **_k):
    raise PermissionError(
        "code_run is network-isolated: network modules "
        "(socket, urllib, requests, httpx, aiohttp, websocket, ...) are "
        "blocked so no data can be exfiltrated."
    )

# --- secret-file detection (issue #229) -----------------------------------
# A path is protected if its basename is a known secret/credential filename
# AND (when a repo root is known) it lives under that repo root. Scoping to
# the repo root avoids false positives on unrelated files with the same name.
def _is_protected_secret(path):
    try:
        p = _os.path.realpath(str(path))
    except Exception:
        return False
    base = _os.path.basename(p)
    if not (
        base == ".env"
        or base.startswith(".env.")
        or base == "providers.json"
        or base.endswith("_tokens.json")
        or base.endswith("_config.json")
    ):
        return False
    if not _REPO_ROOT:
        return True
    try:
        root = _os.path.realpath(_REPO_ROOT)
        if _os.path.commonpath([root, p]) != root:
            return False  # outside the repo root -> not a repo secret
    except Exception:
        return False
    return True

# --- builtins.open: block writes AND secret reads -------------------------
_orig_open = _builtins.open
def _guarded_open(file, mode="r", *args, **kwargs):
    _m = mode if isinstance(mode, str) else "r"
    if any(_c in _m for _c in "wax+"):
        _code_run_write_blocked()
    if _is_protected_secret(file):
        _code_run_secret_read_blocked()
    return _orig_open(file, mode, *args, **kwargs)
_builtins.open = _guarded_open

# --- os.open: block write flags AND secret reads --------------------------
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
            _code_run_write_blocked()
        if _is_protected_secret(path):
            _code_run_secret_read_blocked()
        return _orig_os_open(path, flags, *args, **kwargs)
    _os.open = _guarded_os_open

# --- pathlib.Path: block writes and secret reads --------------------------
_pathlib.Path.write_text = _code_run_write_blocked
_pathlib.Path.write_bytes = _code_run_write_blocked
_pathlib.Path.open = _guarded_open
for _rn in ("read_text", "read_bytes"):
    if hasattr(_pathlib.Path, _rn):
        _orig_read = getattr(_pathlib.Path, _rn)
        def _guarded_read(self, *a, _fn=_orig_read, **k):
            if _is_protected_secret(self):
                _code_run_secret_read_blocked()
            return _fn(self, *a, **k)
        setattr(_pathlib.Path, _rn, _guarded_read)

# --- shutil copy/move/delete ----------------------------------------------
for _n in ("copy", "copy2", "copyfile", "copytree", "move", "rmtree"):
    if hasattr(_shutil, _n):
        setattr(_shutil, _n, _code_run_write_blocked)

# --- os destructive/rename/mkdir ops --------------------------------------
for _n in ("remove", "unlink", "rename", "replace", "rmdir", "mkdir", "makedirs"):
    if hasattr(_os, _n):
        setattr(_os, _n, _code_run_write_blocked)

# --- network isolation (issue #229): block import of network modules ------
_BLOCKED_NET_TOP = frozenset({{
    "socket", "ssl", "http", "urllib", "requests", "httpx", "aiohttp",
    "websocket", "websockets", "urllib3", "wsgiref", "smtplib", "ftplib",
}})
_orig_import = _builtins.__import__
def _guarded_import(name, *args, **kwargs):
    _top = str(name).split(".", 1)[0]
    if _top in _BLOCKED_NET_TOP or str(name) in _BLOCKED_NET_TOP:
        _code_run_network_blocked()
    return _orig_import(name, *args, **kwargs)
_builtins.__import__ = _guarded_import
"""


def build_guard_preamble(repo_root: str | None = None) -> str:
    """Return the guard preamble to prepend to ``code_run`` code.

    ``repo_root`` (absolute path) scopes the secret-file read block to the
    repo (so a test reading an unrelated ``config.json`` elsewhere is not
    falsely blocked). When ``None`` or empty, the secret-file block still
    fires on the known secret basenames regardless of location.
    """
    root = repo_root if repo_root else ""
    return GUARD_PREAMBLE_TEMPLATE.format(repo_root=repr(str(root)))
