"""
Agent-authored tool: apply_ungating_fix
"""

SCHEMA = {"name": "apply_ungating_fix", "description": "One-shot fixer: (1) copies identity.py.tmp over identity.py to fix the stale seed text bug, (2) modifies agent_tools.py to remove keyword gating and make all tools always available. Run once, then restart backend.", "parameters": {"properties": {}, "type": "object"}}

import shutil
from pathlib import Path

def run(args: dict) -> dict:
    """Apply two fixes: identity.py stale seed fix + agent_tools.py ungating."""
    results = []
    errors = []
    
    # Backend dir is the parent of custom_tools/
    backend_dir = Path(__file__).resolve().parent.parent
    trash_backups = backend_dir / "trash" / "backups"
    trash_backups.mkdir(parents=True, exist_ok=True)
    
    # --- Fix 1: Copy identity.py.tmp -> identity.py ---
    tmp_path = backend_dir / "identity.py.tmp"
    id_path = backend_dir / "identity.py"
    
    if tmp_path.exists():
        # Back up current identity.py to trash/backups/
        backup = trash_backups / "identity.py.bak"
        shutil.copy2(id_path, backup)
        # Copy .tmp over
        shutil.copy2(tmp_path, id_path)
        results.append("identity.py: copied .tmp fix over original (backup in trash/backups/)")
    else:
        errors.append(f"identity.py.tmp not found at {tmp_path}")
    
    # --- Fix 2: Modify agent_tools.py to remove keyword gating ---
    tools_path = backend_dir / "agent_tools.py"
    
    try:
        with open(tools_path, encoding="utf-8") as f:
            content = f.read()
        
        original = content
        
        # 2a: Expand CORE_TOOL_NAMES to include ALL tools
        old_core = '''CORE_TOOL_NAMES: set[str] = {
    "vault_search",       # always needed for retrieval
    "plan_task",           # always needed for multi-step tasks
    "update_task",         # always needed for multi-step tasks
    "execute_procedure",   # always needed to invoke procedures
    "code_read",           # general capability -- reading files
}'''
        
        new_core = '''CORE_TOOL_NAMES: set[str] = {
    # All built-in tools are always available.
    # Keyword gating removed — tools that need gating are procedures, not
    # hidden behind keyword matching. See PROCEDURE_CANDIDATE_NAMES for tools
    # that are surfaced via RAG as procedure description cards instead.
    "vault_search", "plan_task", "update_task",
    "execute_procedure", "code_read",
    # Formerly contextual (research):
    "vault_research", "web_read_source",
    # Formerly contextual (code_edit):
    "code_run", "safe_write", "js_safe_write", "git_rollback",
    "backend_restart", "plugin_reload",
    # Formerly contextual (vault_maintenance):
    "vault_safe_write", "vault_append", "vault_delete",
    # Formerly contextual (self_improvement):
    "tool_create",
}'''
        
        if old_core in content:
            content = content.replace(old_core, new_core)
            results.append("agent_tools.py: expanded CORE_TOOL_NAMES to include all tools")
        else:
            errors.append("agent_tools.py: could not find CORE_TOOL_NAMES block to replace")
        
        # 2b: Empty out CONTEXTUAL_TOOL_CATEGORIES
        old_cats = '''CONTEXTUAL_TOOL_CATEGORIES: dict[str, list[str]] = {
    "research": ["vault_research", "web_read_source"],
    "code_edit": [
        "code_run", "safe_write", "js_safe_write", "git_rollback",
        "backend_restart", "plugin_reload",
    ],
    "vault_maintenance": [
        "vault_safe_write", "vault_append", "vault_delete",
    ],
    "self_improvement": ["tool_create"],
}'''
        
        new_cats = '''# Contextual tool categories removed — all tools are now core (always
# available). Keyword gating was fragile: if the user's message didn't
# contain the right keywords, critical tools like safe_write and code_run
# were invisible to the LLM, making it impossible to fix code problems.
# Tools that should be gated are now procedures (see PROCEDURE_CANDIDATE_NAMES).
CONTEXTUAL_TOOL_CATEGORIES: dict[str, list[str]] = {}'''
        
        if old_cats in content:
            content = content.replace(old_cats, new_cats)
            results.append("agent_tools.py: emptied CONTEXTUAL_TOOL_CATEGORIES (no more keyword gating)")
        else:
            errors.append("agent_tools.py: could not find CONTEXTUAL_TOOL_CATEGORIES block to replace")
        
        # 2c: Simplify build_tool_list to not use category selection
        old_build = '''def build_tool_list(user_message: str, plan_text: str = "",
                    custom_schemas: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Build the tool list for the LLM call using progressive disclosure.

    Core tools are always included. Contextual tools are added based on
    keyword matching. Custom tools are always included (agent-authored
    tools are generally task-specific and the model chose to create them).
    Procedure candidates are NOT included -- they are surfaced via RAG.
    """
    tools = get_core_tools()
    categories = select_contextual_categories(user_message, plan_text)
    for cat in categories:
        tools.extend(get_contextual_tools(cat))

    if custom_schemas:'''
        
        new_build = '''def build_tool_list(user_message: str, plan_text: str = "",
                    custom_schemas: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Build the tool list for the LLM call.

    All built-in tools are always included (keyword gating removed).
    Custom tools are included unless they are procedure candidates.
    Procedure candidates are NOT included -- they are surfaced via RAG.
    """
    tools = get_core_tools()

    if custom_schemas:'''
        
        if old_build in content:
            content = content.replace(old_build, new_build)
            results.append("agent_tools.py: simplified build_tool_list (removed category selection)")
        else:
            errors.append("agent_tools.py: could not find build_tool_list block to replace")
        
        # 2d: Make select_contextual_categories return empty set (backward compat)
        old_select = '''def select_contextual_categories(user_message: str, plan_text: str = "") -> set[str]:
    """Deterministic keyword-based selection of which contextual tool
    categories are relevant for the current task.

    No LLM cost. Matches the user message + current plan against keyword
    categories. Returns a set of category names.
    """
    text = (user_message + " " + plan_text).lower()
    selected: set[str] = set()

    if any(kw in text for kw in [
        "research", "investigate", "look up", "find out",
        "what is", "how does", "source", "web", "article",
    ]):
        selected.add("research")

    if any(kw in text for kw in [
        "code", "fix", "edit", "write", "modify", "bug",
        "implement", "function", "python", "javascript",
        ".py", ".js", "backend", "plugin",
    ]):
        selected.add("code_edit")

    if any(kw in text for kw in [
        "vault", "graph", "gaps", "note", "link",
        "wikilink", "cluster", "lint", "delete", "orphan",
        "island", "merge", "maintenance",
    ]):
        selected.add("vault_maintenance")

    if any(kw in text for kw in [
        "tool", "build", "create", "improve", "self-improve",
        "reflect", "ability", "capability",
    ]):
        selected.add("self_improvement")

    return selected'''
        
        new_select = '''def select_contextual_categories(user_message: str, plan_text: str = "") -> set[str]:
    """Deprecated — all tools are now core (always available).

    Returns an empty set for backward compatibility. Kept as a no-op so
    any callers that reference it don't crash. Will be removed in a
    future cleanup.
    """
    return set()'''
        
        if old_select in content:
            content = content.replace(old_select, new_select)
            results.append("agent_tools.py: made select_contextual_categories a no-op (deprecated)")
        else:
            errors.append("agent_tools.py: could not find select_contextual_categories block to replace")
        
        # Write the modified file if any changes were made
        if content != original:
            # Back up original to trash/backups/
            backup = trash_backups / "agent_tools.py.bak"
            with open(backup, "w", encoding="utf-8") as f:
                f.write(original)
            # Write modified
            with open(tools_path, "w", encoding="utf-8") as f:
                f.write(content)
            results.append("agent_tools.py: written (backup in trash/backups/)")
        else:
            errors.append("agent_tools.py: no changes made (content identical)")
            
    except Exception as exc:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        errors.append(f"agent_tools.py fix failed: {exc}")
    
    return {
        "success": len(errors) == 0,
        "results": results,
        "errors": errors,
    }
