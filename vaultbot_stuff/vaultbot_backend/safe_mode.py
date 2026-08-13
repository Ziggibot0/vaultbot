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
  - code_write, safe_write, js_safe_write — modify backend source
  - code_run — execute arbitrary Python
  - tool_create — create new agent tools
  - git_rollback — modify files via git
  - backend_restart, plugin_reload — restart processes
  - vault_delete — delete vault files
  - apply_ungating_fix — one-shot code patcher
  - submit_contribution — push to GitHub
  - review_contributions, torture_test — interact with GitHub PRs

SAFE TOOLS (always allowed)
---------------------------
  - vault_search, vault_read_note, vault_gaps, vaultbot_status
  - vault_research (web research, controlled separately)
  - vault_safe_write, vault_append (write notes, not code)
  - plan_task, update_task, execute_procedure
  - code_read (read-only)
  - thought, ask_user
  - web_read_source, textbook_ingest, textbook_read_page
  - vault_lint, vault_list, vault_graph_analyzer, vault_cluster_analyzer
  - preflight_safety_check (read-only diagnostic)
  - machine_spec, ollama_model_search
  - md_safe_replace, safe_replace (text-only, not code)
"""

from __future__ import annotations

import os

# Tools that are BLOCKED in Safe Mode. These can modify the backend,
# execute arbitrary code, delete data, or interact with external services
# in ways that could be destructive.
_DANGEROUS_TOOLS: frozenset[str] = frozenset(
    {
        "code_write",
        "safe_write",
        "js_safe_write",
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


def is_safe_mode() -> bool:
    """Return True if VaultBot is running in Safe Mode.

    Safe Mode is ON by default. Set VAULTBOT_SAFE_MODE=0 to disable it
    (Developer Mode).
    """
    val = os.environ.get("VAULTBOT_SAFE_MODE", "true").strip().lower()
    # Default to safe mode unless explicitly disabled.
    return val not in ("0", "false", "off", "no", "developer")


def is_tool_allowed(tool_name: str) -> bool:
    """Return True if the given tool is allowed in the current mode.

    In Safe Mode, dangerous tools are blocked. In Developer Mode,
    all tools are allowed.
    """
    if not is_safe_mode():
        return True
    return tool_name not in _DANGEROUS_TOOLS


def blocked_tool_message(tool_name: str) -> str:
    """Return a user-friendly message explaining why a tool is blocked."""
    return (
        f"Tool '{tool_name}' is disabled in Safe Mode. "
        f"This tool can modify backend code, execute arbitrary commands, "
        f"or delete data. To enable it, switch to Developer Mode in "
        f"VaultBot Settings → Safety → uncheck 'Safe Mode'."
    )
