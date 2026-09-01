"""
safe_mode.py — Safe Mode / Developer Mode gate for VaultBot.

WHY THIS EXISTS
---------------
VaultBot can modify its own source code, create new tools, execute arbitrary
Python, restart the backend, and delete vault files. For a non-technical user
who just wants a research assistant, these capabilities are dangerous and
unnecessary. Safe Mode disables self-modification and destructive operations
by default. The user must explicitly opt into Developer Mode to unlock them.

MODES
-----
- **Safe Mode** (default): The agent can read files, search the vault, research
  the web, write notes (via vault_safe_write), and append to notes. It CANNOT
  modify backend code, create tools, execute code, restart the backend, or
  delete files.
- **Developer Mode**: All tools are available. The user explicitly opted in.

CONFIGURATION
-------------
Set via the VAULTBOT_SAFE_MODE env var or the plugin settings GUI:
  - "true" / "1" / "on"  → Safe Mode (default)
  - "false" / "0" / "off" → Developer Mode

The plugin passes this to the backend via the VAULTBOT_SAFE_MODE env var
when spawning the backend process. It can also be set in .env for manual
backend starts.

DANGEROUS TOOLS (blocked in Safe Mode)
--------------------------------------
These tools can modify the backend, execute arbitrary code, or delete data:
  - safe_write, js_safe_write — modify backend/plugin source (full file)
  - safe_replace, js_safe_replace — targeted string replace in .py/.js source
  - code_run — execute arbitrary Python
  - tool_create — create new agent tools
  - git_rollback — modify files via git
  - backend_restart, plugin_reload — restart processes
  - vault_delete — delete vault files
  - apply_ungating_fix — one-shot code patcher
  - submit_contribution — push to GitHub
  - review_contributions, torture_test — interact with GitHub PRs

CONTENT-AWARE GATE (is_file_edit_allowed)
-----------------------------------------
edit_lines is a dual-use tool: it edits .md notes (safe) AND .py/.js source
(dangerous). It is NOT in _DANGEROUS_TOOLS because blocking it entirely
would prevent note editing. Instead, edit_lines.run() calls
is_file_edit_allowed(file_path) to block edits to source-code extensions
(.py, .js, .ts, etc.) while allowing .md and other non-code edits.

SAFE TOOLS (always allowed)
---------------------------
  - vault_search, vault_read_note, vault_gaps, vaultbot_status
  - vault_research (web research, controlled separately)
  - vault_safe_write, vault_append (write notes, not code)
  - md_safe_replace (markdown-only, .md extension enforced)
  - edit_lines for .md files (extension-aware gate blocks .py/.js)
  - plan_task, update_task, add_task, execute_procedure
  - code_read (read-only)
  - thought, ask_user
  - web_read_source, textbook_ingest, textbook_read_page
  - vault_lint, vault_list, vault_graph_analyzer, vault_cluster_analyzer
  - preflight_safety_check (read-only diagnostic)
  - machine_spec, ollama_model_search
  - undo_last_write (restores vault notes from trash, not code)
"""

from __future__ import annotations

import os

# Tools that are BLOCKED in Safe Mode. These can modify the backend,
# execute arbitrary code, delete data, or interact with external services
# in ways that could be destructive.
#
# NOTE: edit_lines is NOT here because it's dual-use (edits .md notes AND
# .py/.js source). It uses is_file_edit_allowed() for extension-aware gating.
# NOTE: code_write is a legacy name that no longer has a handler; kept for
# belt-and-suspenders in case a stale schema references it.
_DANGEROUS_TOOLS: frozenset[str] = frozenset(
    {
        "code_write",  # legacy — no handler, but block if ever called
        "safe_write",
        "js_safe_write",
        "safe_replace",
        "js_safe_replace",
        "code_run",
        "tool_create",
        "git_rollback",
        "backend_restart",
        "plugin_reload",
        "vault_delete",
        "apply_ungating_fix",
        "submit_contribution",
        "review_contributions",
        "torture_test",
    }
)

# File extensions that are considered SOURCE CODE for Safe Mode purposes.
# Editing these files = modifying VaultBot's own code (dogfooding), which
# Safe Mode must prevent. Used by is_file_edit_allowed() and edit_lines.
_SOURCE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".pyw",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".jsx",
        ".tsx",
        ".json",  # config files (manifest.json, package.json, tsconfig)
        ".toml",  # pyproject.toml, poetry config
        ".yaml",
        ".yml",  # CI workflows, docker-compose
        ".sh",
        ".bash",  # shell scripts (setup.sh, test_install.sh)
        ".ps1",  # PowerShell scripts (setup.ps1)
        ".env",  # environment config
        ".cfg",
        ".ini",  # config files
        ".lock",  # lock files (requirements, package-lock)
    }
)


def is_safe_mode() -> bool:
    """Return True if VaultBot is running in Safe Mode.

    Safe Mode is ON by default. Set VAULTBOT_SAFE_MODE=0 to disable it
    (Developer Mode). Reads the live value (runtime override from the
    settings GUI, else the spawn-time env) via live_config.
    """
    from live_config import is_safe_mode as _live

    return _live()


def is_tool_allowed(tool_name: str) -> bool:
    """Return True if the given tool is allowed in the current mode.

    In Safe Mode, dangerous tools are blocked. In Developer Mode,
    all tools are allowed.
    """
    if not is_safe_mode():
        return True
    return tool_name not in _DANGEROUS_TOOLS


def is_file_edit_allowed(file_path: str) -> bool:
    """Return True if editing the given file path is allowed in the current mode.

    This is the CONTENT-AWARE gate for dual-use tools like edit_lines that
    can edit both notes (.md) and source code (.py, .js, etc.).

    In Safe Mode: source-code extensions are BLOCKED (no dogfooding).
    In Developer Mode: all files are allowed.
    """
    if not is_safe_mode():
        return True

    _, ext = os.path.splitext(file_path)
    return ext.lower() not in _SOURCE_EXTENSIONS


def blocked_file_edit_message(file_path: str) -> str:
    """Return a user-friendly message explaining why a file edit is blocked."""
    _, ext = os.path.splitext(file_path)
    return (
        f"Editing '{file_path}' is blocked in Safe Mode — {ext} files are "
        f"source code. Safe Mode prevents VaultBot from modifying its own "
        f"code (no dogfooding). To edit source files, switch to Developer "
        f"Mode in VaultBot Settings → Safety → uncheck 'Safe Mode'. "
        f"For markdown notes (.md), use vault_safe_write or md_safe_replace."
    )


def blocked_tool_message(tool_name: str) -> str:
    """Return a user-friendly message explaining why a tool is blocked."""
    return (
        f"Tool '{tool_name}' is disabled in Safe Mode. "
        f"This tool can modify backend code, execute arbitrary commands, "
        f"or delete data. To enable it, switch to Developer Mode in "
        f"VaultBot Settings → Safety → uncheck 'Safe Mode'."
    )
