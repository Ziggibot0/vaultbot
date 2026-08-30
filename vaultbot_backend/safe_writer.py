"""Safe-write utilities extracted from self_improver.py.

These functions provide hardened file writing for the agent's self-edit
path:

  - ``safe_write`` — Python safe-write with AST syntax check, import-target
    verification, subprocess import check, and pytest gate.
  - ``js_safe_write`` — JS safe-write with ``node --check`` syntax
    validation and require() load testing.
  - ``resolve_path`` / ``backup_path`` / ``safe_name`` — shared path
    resolution and backup helpers used by both safe-write variants.

The verification callables (import check, pytest, JS load, etc.) are
passed as parameters so callers (and tests) can substitute mock
implementations. The backend directory, vault root, and trash directory
are also passed explicitly — no module-level globals are read, so tests
that monkeypatch ``self_improver.BACKEND_DIR`` / ``BACKEND_ROOT`` still
control where files land.
"""

import ast
import contextlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from subprocess_utils import run as _subprocess_run


def safe_name(name: str) -> str:
    """Sanitize a tool name for use as a filename."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def resolve_path(
    file_path: str, backend_root: Path, allow_create: bool = False
) -> Path | None:
    """Resolve a path relative to the vault root, restricted to the vault
    directory so the agent can't write outside it."""
    if not file_path:
        return None
    from workspace import WorkspaceError, workspace_registry

    try:
        selected = workspace_registry.get()
    except WorkspaceError:
        return None
    if selected is not None:
        return workspace_registry.resolve_project_path(
            file_path, allow_create=allow_create
        )
    candidate = (backend_root / file_path).resolve()
    # Must be inside the vault root.
    try:
        candidate.relative_to(backend_root.resolve())
    except ValueError:
        return None
    if not allow_create and not candidate.exists():
        return None
    return candidate


def backup_path(target: Path, backend_root: Path, trash_dir: Path) -> Path:
    """Return the backup path for a target file, routed to trash/backups/.

    Uses the target's relative path from the vault root to create a unique
    backup name, so two files with the same name in different directories
    don't collide.  Example: vaultbot_backend/main.py
    -> trash/backups/vaultbot_backend/main.py.bak
    """
    try:
        rel = target.resolve().relative_to(backend_root.resolve())
    except ValueError:
        # Target is outside vault root — fall back to filename only.
        rel = Path(target.name)
    bak = trash_dir / str(rel).replace("\\", "/").replace("/", "_").replace("..", "_")
    return bak.with_suffix(bak.suffix + ".bak")


def detect_external_imports(content: str, internal_modules: set[str]) -> list[str]:
    """Return the top-level modules imported by ``content`` that are NOT
    VaultBot-internal.

    "Internal" means: a relative import (``from . import x``), or a
    top-level module whose name is in ``internal_modules`` (the backend's
    own ``.py`` stems plus the ``routers`` / ``custom_tools`` / ``identity``
    packages). Everything else — stdlib (``os``, ``json``, ``re``, ...) and
    third-party (``requests``, ``bs4``, ``numpy``, ...) — is "external" and
    must be doc-proven before a write.

    Returns a deduped, order-preserving list of external module names.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []
    external: list[str] = []
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in internal_modules and top not in seen:
                    seen.add(top)
                    external.append(top)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # relative import — internal
            if node.module is None:
                continue
            top = node.module.split(".")[0]
            if top not in internal_modules and top not in seen:
                seen.add(top)
                external.append(top)
    return external


def safe_write(
    file_path: str,
    content: str,
    dry_run: bool,
    backend_dir: Path,
    backend_root: Path,
    trash_dir: Path,
    core_files: set[str],
    log_fn,
    verify_import_targets_fn,
    copy_backend_fn,
    verify_import_fn,
    run_pytest_fn,
    doc_source: str | list[str] | None = None,
) -> dict[str, Any]:
    """Write a file with safety verification. Use this INSTEAD of
    code_write when editing backend source code (.py files under
    vaultbot_backend/). For markdown notes or non-code files, code_write
    is fine.

    Safety checks (in order, fail-fast):
      1. Syntax: the content must parse as valid Python (ast.parse).
      1b. Doc-source gate: any import of a non-VaultBot module (stdlib
         or third-party) requires a ``doc_source`` URL proving the edit
         was checked against official docs. An edit that can't cite a
         source is rejected — the code analogue of the chat closed-set
         citation gate. Run Prove-Code-Change to satisfy it.
      2. Encoding: written as UTF-8 (avoids the mojibake corruption
         the agent's code_write introduced on 2026-07-25).
      3. Import verification: if the target is a core backend module,
         a SUBPROCESS imports the backend entry point (main.py) with
         the new file in place. If that subprocess fails to import,
         the edit is rejected and the original file is restored. This
         catches both "I deleted a module another file imports" and
         "I changed a signature a caller depends on" — the two ways
         the agent broke itself.
      4. Pytest gate (soft): if the import check PASSED, run `pytest -q`
         against the same backend dir (the tmp copy for dry_run, the
         live backend dir for a real write). If any test FAILS, the
         edit is rejected and (for a real write) auto-rolled-back.
         If pytest itself cannot run (not installed, import error,
         etc.), the check is recorded as `skipped: <reason>` and the
         write proceeds — the import check is the hard gate; pytest
         is a softer gate enforced only when it can actually run.
      5. Auto-rollback: on any check failure after the file is on
         disk, the pre-edit backup (.bak) is restored immediately.

    Args:
      file_path: path relative to vault root.
      content: the new file content.
      doc_source: optional URL (or list of URLs) naming the official
        documentation the edit was checked against. Required when the
        content imports any non-VaultBot module (stdlib or third-party).
      dry_run: if True, run all checks but do NOT write to disk.
        Returns the verification result so the agent can preview
        whether an edit would be safe before committing it.
      backend_dir: the backend directory (for core-file detection).
      backend_root: the vault root (for path resolution).
      trash_dir: where .bak files go.
      core_files: set of core file names that trigger import verification.
      log_fn: callable (event, data) for logging.
      verify_import_targets_fn: callable (content, backend_dir_str) -> (bool, str|None).
      copy_backend_fn: callable (tmpdir, target_name, content) -> None.
      verify_import_fn: callable (backend_dir_str) -> (bool, str|None).
      run_pytest_fn: callable (backend_dir_str, target_file) -> (bool, str|None).

    Returns a dict with: status ("written" | "dry_run_ok" |
    "rejected"), the checks performed, and on rejection the error
    that would have broken the backend.
    """
    full = resolve_path(file_path, backend_root, allow_create=True)
    if not full:
        return {"error": f"path not allowed: {file_path}"}
    is_core = full.parent.resolve() == backend_dir and full.name in core_files
    # --- 0. JS guard: reject JS files (use js_safe_write instead) ---
    if full.suffix in (".js", ".mjs", ".cjs"):
        return {
            "status": "rejected",
            "error": f"safe_write is for Python (.py) files only. "
            f"Got '{full.suffix}' — use js_safe_write for "
            f"JavaScript files.",
            "hint": "Call js_safe_write with the same file_path and "
            "content. js_safe_write validates JS syntax with "
            "node --check before writing.",
        }

    # --- 0b. Markdown guard: reject .md files (use md_safe_replace or
    # vault_safe_write instead) ---
    # Without this, ast.parse() on markdown content either succeeds on
    # empty strings (writing 0 bytes — see session 15e346b7) or fails with
    # a confusing SyntaxError on em-dashes and other non-ASCII characters.
    if full.suffix == ".md":
        return {
            "status": "rejected",
            "error": (
                "safe_write is for Python (.py) files only. "
                "Got '.md' — use md_safe_replace for targeted edits "
                "or vault_safe_write for full-file writes of markdown "
                "notes. safe_write runs Python syntax validation "
                "(ast.parse) which will reject markdown content."
            ),
            "hint": (
                "For a section edit: call md_safe_replace with "
                "file_path, old_str, new_str. "
                "For a full rewrite: call vault_safe_write with "
                "file_path and content."
            ),
        }

    # --- 0c. Empty content guard: reject empty content (silent 0-byte
    # write prevention) ---
    # If the caller passes old_str/new_str (md_safe_replace params) instead
    # of content, args.get('content', '') returns '' — ast.parse('') succeeds,
    # and the file is overwritten with 0 bytes. This guard prevents that.
    if not content:
        return {
            "status": "rejected",
            "error": (
                "safe_write received empty content. This usually means "
                "you passed old_str/new_str (md_safe_replace parameters) "
                "instead of content. safe_write requires a 'content' "
                "parameter with the FULL file content."
            ),
            "hint": (
                "Check your parameters: safe_write takes file_path + "
                "content (not old_str/new_str). If you want to do a "
                "targeted string replacement, use md_safe_replace for "
                ".md files or safe_replace for .py files."
            ),
        }

    checks: dict[str, Any] = {}

    # --- 1. Syntax check (no disk touch) ---
    try:
        ast.parse(content)
        checks["syntax"] = "ok"
    except SyntaxError as e:
        checks["syntax"] = f"FAIL: {e}"
        return {
            "status": "rejected",
            "checks": checks,
            "error": f"SyntaxError: {e.msg} (line {e.lineno})",
            "hint": "Fix the syntax error; nothing was written.",
        }

    # --- 1a. Doc-source gate (semantic provenance, no disk touch) ---
    # The edit must PROVE its external API usage against real docs, not
    # model weights. Any import of a non-VaultBot module (stdlib or
    # third-party) requires a `doc_source` (URL or list of URLs) naming
    # the official documentation the edit was checked against. This is
    # the code analogue of the chat closed-set citation gate: an edit
    # that can't point at a source is rejected, exactly like an uncited
    # chat claim. Run Prove-Code-Change to satisfy this automatically.
    _internal = {p.stem for p in backend_dir.glob("*.py")}
    _internal |= {"routers", "custom_tools", "identity"}
    _external = detect_external_imports(content, _internal)
    if _external and not doc_source:
        checks["doc_source"] = f"FAIL: {len(_external)} external import(s) uncited"
        log_fn(
            "safe_write_doc_source_rejected",
            {"file_path": str(full), "external_imports": _external},
        )
        return {
            "status": "rejected",
            "checks": checks,
            "error": (
                f"Unproven external API usage: {', '.join(_external)}. "
                "This edit imports modules outside VaultBot's own code "
                "without citing the documentation it was checked against."
            ),
            "hint": (
                "Run Prove-Code-Change to fetch the official docs for "
                "these modules, verify the edit against them, and attach "
                "a doc_source. Then re-call safe_write with "
                "doc_source=<official docs URL>. Writing from model "
                "weights is not allowed."
            ),
        }
    if _external:
        checks["doc_source"] = "ok"

    # --- 1b. Import-target check (any backend .py, before disk touch) ---
    # Catches "I wrote `from chat_helpers import run_with_heartbeat` but
    # the function doesn't exist" — even for files NOT in _CORE_FILES.
    # Runs against the CURRENT backend (no disk mutation yet), so it's
    # safe in dry_run and real-write modes alike.
    is_backend_py = full.suffix == ".py" and backend_dir in full.resolve().parents
    if is_backend_py:
        ok, err = verify_import_targets_fn(content, str(backend_dir))
        checks["import_refs"] = "ok" if ok else f"FAIL: {err}"
        if not ok:
            log_fn(
                "safe_write_import_refs_rejected",
                {"file_path": str(full), "error": err},
            )
            return {
                "status": "rejected",
                "checks": checks,
                "error": err,
                "hint": (
                    "The edit imports a name that doesn't exist "
                    "on the target module yet. Did you finish "
                    "writing the function/class you're importing? "
                    "Nothing was written."
                ),
            }

    # --- dry_run: stop here, report whether it WOULD be safe ---
    if dry_run:
        # For a dry run of a core file, do the import check against a
        # temp copy so we don't disturb the live file.
        if is_core and full.exists():
            tmpdir = tempfile.mkdtemp(prefix="vaultbot_dryrun_")
            try:
                # Copy the backend dir, swap in the new content, import.
                copy_backend_fn(tmpdir, full.name, content)
                ok, err = verify_import_fn(tmpdir)
                checks["import_check"] = "ok" if ok else f"FAIL: {err}"
                if not ok:
                    return {
                        "status": "dry_run_rejected",
                        "checks": checks,
                        "would_break_backend": True,
                        "error": err,
                    }
                # Import passed — run the soft pytest gate against the
                # same tmp copy. A failure is reported as
                # dry_run_rejected (no disk touch in dry_run mode).
                p_ok, p_out = run_pytest_fn(tmpdir, full.name)
                if p_out and not p_ok:
                    checks["pytest"] = f"FAIL: {p_out[:500]}"
                    return {
                        "status": "dry_run_rejected",
                        "checks": checks,
                        "would_break_backend": True,
                        "error": p_out[:500],
                    }
                checks["pytest"] = (
                    "ok" if p_ok else f"skipped: {(p_out or 'unknown')[:200]}"
                )
                return {
                    "status": "dry_run_ok",
                    "checks": checks,
                    "would_break_backend": False,
                    "error": None,
                }
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
        checks["import_check"] = "skipped (not a core backend file)"
        return {
            "status": "dry_run_ok",
            "checks": checks,
            "would_break_backend": False,
        }

    # --- Markdown guard (same as code_write) ---
    if full.suffix == ".md":
        from vault_guard import VaultWriteForbidden, assert_writable

        try:
            assert_writable(full)
        except VaultWriteForbidden as e:
            return {"error": f"write blocked: {e.reason}", "file_path": str(full)}

    # --- 2. Write (UTF-8) + backup ---
    try:
        full.parent.mkdir(parents=True, exist_ok=True)
        had_backup = False
        if full.exists():
            bak = backup_path(full, backend_root, trash_dir)
            bak.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(full, bak)
            had_backup = True
        full.write_text(content, encoding="utf-8")
        checks["encoding"] = "utf-8 ok"
    except Exception as e:  # noqa: BLE001 — best-effort, returns error to caller — see CONTRIBUTING.md no-silent-fallbacks
        return {
            "status": "rejected",
            "checks": checks,
            "error": f"write failed: {e}",
        }

    # --- 3. Import verification (subprocess, core files only) ---
    if is_core:
        ok, err = verify_import_fn(str(backend_dir))
        checks["import_check"] = "ok" if ok else f"FAIL: {err}"
        if not ok:
            # 4. Auto-rollback: restore the .bak so the live backend
            # is not left with a broken file.
            if had_backup:
                try:
                    shutil.copy2(bak, full)
                    checks["auto_rollback"] = "restored from .bak"
                except Exception as rb_err:  # noqa: BLE001 — best-effort, returns error to caller — see CONTRIBUTING.md no-silent-fallbacks
                    checks["auto_rollback"] = f"FAILED: {rb_err}"
            else:
                # No prior file existed; delete the broken new file.
                try:
                    full.unlink()
                    checks["auto_rollback"] = "deleted new file (no prior .bak)"
                except Exception as rb_err:  # noqa: BLE001 — best-effort, returns error to caller — see CONTRIBUTING.md no-silent-fallbacks
                    checks["auto_rollback"] = f"FAILED: {rb_err}"
            # Clean up the backup after rollback (success or failure).
            if had_backup:
                with contextlib.suppress(Exception):
                    bak.unlink()
            log_fn(
                "safe_write_rejected",
                {"file_path": str(full), "error": err, "checks": checks},
            )
            return {
                "status": "rejected",
                "checks": checks,
                "error": err,
                "hint": (
                    "The edit would break the backend on restart. "
                    "The original file was restored. Fix the error "
                    "and try again, or use git_rollback if needed."
                ),
            }

    # --- 3b. Pytest gate (soft; core files only) ---
    # Only run pytest if the import check passed (don't waste time
    # running tests on a file that doesn't even import). A pytest
    # failure rejects the edit and auto-rolls-back from .bak (same
    # path as the import-check failure above). If pytest itself cannot
    # run (not installed, import error, etc.), record `skipped: ...`
    # and proceed — the import check is the hard gate.
    if is_core and checks.get("import_check") == "ok":
        try:
            p_ok, p_out = run_pytest_fn(str(backend_dir), full.name)
        except Exception as e:  # noqa: BLE001 — best-effort, returns error to caller — see CONTRIBUTING.md no-silent-fallbacks
            checks["pytest"] = f"skipped: could not run pytest: {e}"
            p_ok = False  # FAIL LOUD: if pytest can't run, the edit is rejected
            p_out = None
        if p_out and not p_ok:
            checks["pytest"] = f"FAIL: {p_out[:500]}"
            # Auto-rollback from .bak (same path as import failure).
            if had_backup:
                try:
                    shutil.copy2(bak, full)
                    checks["auto_rollback"] = "restored from .bak"
                except Exception as rb_err:  # noqa: BLE001 — best-effort, returns error to caller — see CONTRIBUTING.md no-silent-fallbacks
                    checks["auto_rollback"] = f"FAILED: {rb_err}"
            else:
                try:
                    full.unlink()
                    checks["auto_rollback"] = "deleted new file (no prior .bak)"
                except Exception as rb_err:  # noqa: BLE001 — best-effort, returns error to caller — see CONTRIBUTING.md no-silent-fallbacks
                    checks["auto_rollback"] = f"FAILED: {rb_err}"
            # Clean up the backup after rollback.
            if had_backup:
                with contextlib.suppress(Exception):
                    bak.unlink()
            log_fn(
                "safe_write_pytest_rejected",
                {"file_path": str(full), "error": p_out[:500], "checks": checks},
            )
            return {
                "status": "rejected",
                "checks": checks,
                "error": p_out[:500],
                "hint": (
                    "The edit passed the import check but "
                    "failed a pytest run. The original file "
                    "was restored. Fix the failing test and "
                    "try again, or use git_rollback if needed."
                ),
            }
        checks["pytest"] = "ok" if p_ok else f"skipped: {(p_out or 'unknown')[:200]}"

    log_fn(
        "safe_write",
        {
            "file_path": str(full),
            "length": len(content),
            "is_core": is_core,
            "checks": checks,
        },
    )
    # Clean up backup on success.
    if had_backup:
        with contextlib.suppress(Exception):
            bak.unlink()
    return {
        "status": "written",
        "file_path": str(full),
        "bytes": len(content),
        "checks": checks,
        "is_core": is_core,
    }


def js_safe_write(
    file_path: str,
    content: str,
    dry_run: bool,
    backend_root: Path,
    trash_dir: Path,
    log_fn,
    verify_js_load_fn,
) -> dict[str, Any]:
    """Write a JavaScript file with syntax validation. Use this for
    .js, .mjs, and .cjs files — especially the Obsidian plugin's
    main.js. It validates JS syntax with `node --check` BEFORE
    writing to disk (atomic write pattern), so the real file is
    never in a broken state.

    Safety checks (in order, fail-fast):
      1. Extension guard: must be .js, .mjs, or .cjs. Rejects .py
         files (use safe_write for Python).
      2. Syntax check: writes content to a temp file, then runs
         `node --check` on it. If node reports a SyntaxError, the
         edit is rejected and the real file is never touched.
      3. Atomic write: only if syntax check passes, backup the
         original to .bak, write the new content as UTF-8, verify
         the write by re-reading, and clean up the .bak on success.
      4. Auto-rollback: if the write verification fails, restore
         from .bak immediately.

    Args:
      file_path: path relative to vault root (e.g.
        '.obsidian/plugins/vaultbot/main.js').
      content: the new JS file content.
      dry_run: if True, run the syntax check only; do NOT write.
      backend_root: the vault root (for path resolution).
      trash_dir: where .bak files go.
      log_fn: callable (event, data) for logging.
      verify_js_load_fn: callable (content) -> (bool, str|None) for
        JS load testing.

    Returns a dict with: status ("written" | "dry_run_ok" |
    "rejected"), the checks performed, and on rejection the error.
    """
    full = resolve_path(file_path, backend_root, allow_create=True)
    if not full:
        return {"error": f"path not allowed: {file_path}"}

    # --- 1. Extension guard ---
    if full.suffix not in (".js", ".mjs", ".cjs"):
        return {
            "status": "rejected",
            "error": f"js_safe_write is for JavaScript (.js, .mjs, "
            f".cjs) files only. Got '{full.suffix}' — use "
            f"safe_write for Python (.py) files.",
            "hint": "Call safe_write with the same file_path and "
            "content for Python files.",
        }

    checks: dict[str, Any] = {}

    # --- 2. Syntax check via node --check (atomic: temp file first) ---
    node_path = shutil.which("node")
    if not node_path:
        checks["syntax"] = "skipped: node not found"
        return {
            "status": "rejected",
            "checks": checks,
            "error": "Node.js not found on PATH. Cannot validate "
            "JS syntax. Install Node.js or set PATH.",
            "hint": "Node.js is required for js_safe_write.",
        }

    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".js", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
    except Exception as e:  # noqa: BLE001 — best-effort, returns error to caller — see CONTRIBUTING.md no-silent-fallbacks
        return {
            "status": "rejected",
            "checks": checks,
            "error": f"failed to create temp file: {e}",
        }

    try:
        result = _subprocess_run(
            [node_path, "--check", tmp_path],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        os.unlink(tmp_path)
        return {
            "status": "rejected",
            "checks": checks,
            "error": "node --check timed out (15s)",
        }
    except Exception as e:  # noqa: BLE001 — best-effort, returns error to caller — see CONTRIBUTING.md no-silent-fallbacks
        os.unlink(tmp_path)
        return {
            "status": "rejected",
            "checks": checks,
            "error": f"node --check failed to run: {e}",
        }

    if result.returncode != 0:
        # Extract the useful part of the error (skip node internals)
        err_lines = result.stderr.strip().split("\n")
        syntax_err = "\n".join(
            line
            for line in err_lines
            if not line.startswith("    at ") and not line.startswith("Node.js")
        )
        checks["syntax"] = f"FAIL: {syntax_err}"
        os.unlink(tmp_path)
        log_fn(
            "js_safe_write_rejected",
            {"file_path": str(full), "syntax_error": syntax_err[:500]},
        )
        return {
            "status": "rejected",
            "checks": checks,
            "error": syntax_err[:500],
            "hint": "Fix the JS syntax error; nothing was written. "
            "The real file was never touched.",
        }

    checks["syntax"] = "ok (node --check passed)"
    os.unlink(tmp_path)

    # --- 2b. Load test: require() the module with a stub for 'obsidian'
    # and a hard timeout. Catches load-time hangs (infinite recursion /
    # infinite loops at module top level) that `node --check` (syntax
    # only) misses. The Obsidian plugin's main.js requires 'obsidian'
    # (the plugin API, unavailable outside Obsidian), so we inject a
    # Proxy-based no-op stub into the child process's module cache before
    # requiring the file. A load that throws a non-Obsidian error OR
    # doesn't exit within the timeout is rejected.
    load_ok, load_err = verify_js_load_fn(content)
    if not load_ok:
        checks["load_check"] = f"FAIL: {load_err}"
        log_fn(
            "js_safe_write_load_rejected",
            {"file_path": str(full), "load_error": (load_err or "")[:500]},
        )
        return {
            "status": "rejected",
            "checks": checks,
            "error": (load_err or "load check failed")[:500],
            "hint": (
                "The file has valid syntax but fails to load "
                "(hangs or throws at module level). This is "
                "often an infinite recursion or loop at the "
                "top level. Nothing was written."
            ),
        }
    checks["load_check"] = "ok (require exited cleanly)"

    # --- dry_run: stop here, report it WOULD be safe ---
    if dry_run:
        return {
            "status": "dry_run_ok",
            "checks": checks,
            "would_break_plugin": False,
            "error": None,
        }

    # --- 3. Atomic write: backup, write, verify ---
    had_backup = False
    try:
        full.parent.mkdir(parents=True, exist_ok=True)
        if full.exists():
            bak = backup_path(full, backend_root, trash_dir)
            bak.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(full, bak)
            had_backup = True
        full.write_text(content, encoding="utf-8")
        checks["encoding"] = "utf-8 ok"
    except Exception as e:  # noqa: BLE001 — best-effort, returns error to caller — see CONTRIBUTING.md no-silent-fallbacks
        return {
            "status": "rejected",
            "checks": checks,
            "error": f"write failed: {e}",
        }

    # Verify the write
    try:
        written = full.read_text(encoding="utf-8")
        if written != content:
            raise OSError("write verification mismatch")
        checks["write_verified"] = "ok"
    except Exception as e:  # noqa: BLE001 — best-effort, returns error to caller — see CONTRIBUTING.md no-silent-fallbacks
        checks["write_verified"] = f"FAIL: {e}"
        if had_backup:
            try:
                shutil.copy2(bak, full)
                checks["auto_rollback"] = "restored from .bak"
            except Exception as rb_err:  # noqa: BLE001 — best-effort, returns error to caller — see CONTRIBUTING.md no-silent-fallbacks
                checks["auto_rollback"] = f"FAILED: {rb_err}"
        # Clean up the backup after rollback.
        if had_backup:
            with contextlib.suppress(Exception):
                bak.unlink()
        log_fn(
            "js_safe_write_verify_failed",
            {"file_path": str(full), "error": str(e), "checks": checks},
        )
        return {
            "status": "rejected",
            "checks": checks,
            "error": f"write verification failed: {e}",
            "hint": "The original file was restored from .bak.",
        }

    # Clean up backup on success
    if had_backup:
        with contextlib.suppress(Exception):
            bak.unlink()

    log_fn(
        "js_safe_write",
        {"file_path": str(full), "length": len(content), "checks": checks},
    )
    return {
        "status": "written",
        "file_path": str(full),
        "bytes": len(content),
        "checks": checks,
    }
