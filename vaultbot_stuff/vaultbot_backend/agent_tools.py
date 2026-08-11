"""
Agentic tool definitions for the in-vault chat LLM.

These are the tools the chat LLM can call during a conversation, using
Ollama's native tool-calling protocol (via /api/chat). The LLM reasons
about the vault context; when it hits a knowledge gap, it emits a
tool_call for `vault_research` and the backend executes the LLM-light
research engine, feeds the result back, and the LLM produces a
grounded answer — exactly like Jarvis reporting the latest facts to Tony.

Tool execution lives in main.py (where the engine/indexer/note_creator
instances are). This module only holds the schemas + a registry so the
system prompt and the handler share a single source of truth.
"""

import os
from typing import Any

# Ollama tool schema format mirrors OpenAI function-calling.
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "vault_research",
            "description": (
                "Research a topic on the web and write a sourced, linked "
                "research note into the vault. Call this when the vault "
                "context is thin, missing, or out of date for the user's "
                "question — i.e. when you would otherwise have to say 'I "
                "don't have enough information.' The research engine digs "
                "deep (multiple web sources, corroboration, gap-filling) "
                "and returns a sourced summary you can reason about and "
                "cite. Do NOT use this if the vault already has strong "
                "coverage of the topic."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": (
                            "The specific topic or question to research. "
                            "Be precise — a focused query digs deeper."
                        ),
                    },
                    "depth": {
                        "type": "string",
                        "enum": ["deep", "quick"],
                        "description": (
                            "'deep' for multi-round research with gap "
                            "filling (default); 'quick' for a single fast "
                            "lookup."
                        ),
                    },
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_search",
            "description": (
                "Search the vault's embedding index for notes relevant to "
                "a query. Use this to pull additional context beyond the "
                "subgraph already provided, or to check whether the vault "
                "covers a specific sub-topic before deciding to research."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query.",
                    },
                    "k": {
                        "type": "integer",
                        "description": "Number of results (default 5).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_read_note",
            "description": (
                "Read a vault note by its wikilink title. This is "
                "DETERMINISTIC — it resolves the title to an exact file "
                "path via the vault graph and reads the full content "
                "directly from disk. Use this INSTEAD of vault_search when "
                "you know the note's title (e.g. the user mentioned "
                "[[Dream-Pass-Audit]] or you need to check a specific "
                "note). Do NOT search for a note you can read by title. "
                "vault_search is probabilistic (FAISS) and may return "
                "trashed or irrelevant files; vault_read_note is "
                "deterministic and always finds the exact note."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": (
                            "The wikilink title of the note to read, "
                            "e.g. 'Dream-Pass-Audit' (the part inside "
                            "[[...]], before any | alias)."
                        ),
                    },
                    "max_lines": {
                        "type": "integer",
                        "default": 0,
                        "description": (
                            "Maximum lines to return (0 = whole file)."
                        ),
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_gaps",
            "description": (
                "List the vault's current knowledge gaps: dangling wikilinks "
                "(concepts the vault links to but has no note for) and thin "
                "notes (exist but are too short). Use this to tell the user "
                "what the vault is missing, or to decide what to research."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vaultbot_status",
            "description": (
                "Report VaultBot's operational state: whether the backend "
                "and autonomous background researcher are running, and recent "
                "autonomous research history. Call this if the user asks "
                "what you've been doing or what you can do."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_task",
            "description": (
                "Write a NEW multi-step task plan into working memory, "
                "replacing any existing plan. NOTE: the framework usually "
                "plans automatically before the loop starts — only call this "
                "to REPLACE the current plan with a better one mid-task. "
                "Steps should be concrete and verifiable. After this, "
                "the framework auto-starts the first step."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "The high-level goal in your own words.",
                    },
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Concrete, ordered steps. Each should be "
                            "verifiable — e.g. 'Search vault for existing "
                            "notes on X', 'Research topic Y on the web', "
                            "'Synthesize findings into an answer'."
                        ),
                    },
                },
                "required": ["goal", "steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_task",
            "description": (
                "Update a task in your working memory. The PRIMARY use is "
                "to mark the CURRENT in-progress step as status='completed' "
                "when its work is verifiably done — the framework auto-starts "
                "the next step for you. You rarely need status='in_progress' "
                "(the framework auto-advances steps). You can also append a "
                "newly-discovered step with action='add'. See "
                "[[Agentic-Loop-Turn-Protocol]]."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task id (from plan_task response).",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed"],
                        "description": "New status for the task.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Optional annotation — what you found or why the status changed.",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["update", "add"],
                        "default": "update",
                        "description": "'update' (default) to change an existing task, 'add' to append a new task discovered mid-plan.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Required only when action='add': the new task content.",
                    },
                },
                "required": ["task_id", "status"],
            },
        },
    },
]

# NOTE: textbook_read_page and web_read_source are provided by the
# custom_tools/ package (textbook_read_page.py, web_read_source.py) and are
# loaded as custom tool schemas at runtime — they are NOT duplicated here to
# avoid the LLM seeing two copies of the same tool (decision paralysis).


# Meta-tools: self-improvement abilities. These let the agent read/write its
# own code, run code to test, create new tools, reflect, and roll back.
META_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "code_read",
            "description": (
                "Read a file from VaultBot's own source code or the vault. "
                "Use this to inspect how a tool works, read the backend's "
                "code, or examine a note. Paths are relative to the vault root "
                "(e.g. 'vaultbot_stuff/vaultbot_backend/main.py')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path relative to vault root.",
                    },
                    "start_line": {"type": "integer", "default": 1},
                    "end_line": {"type": "integer", "default": 0},
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "code_run",
            "description": (
                "Execute Python code in a sandboxed subprocess to test it "
                "before writing or adopting it. Returns stdout, stderr, and "
                "exit code. Use this to verify new tools work before creating "
                "them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "timeout": {"type": "integer", "default": 15},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_create",
            "description": (
                "Create a new tool for yourself and all MCP clients. The tool "
                "is written to custom_tools/ and immediately loaded/registered. "
                "You (and external clients like Copilot Chat) can call it in "
                "the very next turn. `code` must define a `run(args: dict) -> "
                "dict` function. Test your code with code_run first!"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string"},
                    "description": {"type": "string"},
                    "parameters": {
                        "type": "object",
                        "description": "JSON schema for the tool's arguments.",
                    },
                    "code": {
                        "type": "string",
                        "description": (
                            "Python source defining `def run(args: dict) -> "
                            "dict:`. Do NOT include the SCHEMA (it's added "
                            "automatically)."
                        ),
                    },
                },
                "required": ["tool_name", "description", "parameters", "code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "self_reflect",
            "description": (
                "Reflect on a topic and propose 1-3 new tool abilities you "
                "could create for yourself. Use this when you realize you "
                "lack an ability — it returns concrete proposals with code "
                "sketches you can then implement with tool_create."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_rollback",
            "description": (
                "Restore files from git HEAD. Use this if a self-edit breaks "
                "something. If file_path is given, restore just that file; "
                "otherwise restore all of vaultbot_backend/."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "default": ""},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "safe_write",
            "description": (
                "SAFE self-edit of backend source code. Use this INSTEAD of "
                "code_write for any .py file under vaultbot_backend/. It "
                "verifies the edit won't break the backend: (1) syntax-checks "
                "the new content, (2) writes as UTF-8, (3) for core modules, "
                "imports the whole backend in a SUBPROCESS with the new file "
                "in place — if that import fails, the edit is REJECTED and "
                "the original file is auto-restored from the .bak backup. "
                "This is the tool that prevents you from breaking yourself "
                "in half. Set dry_run=true to preview whether an edit would "
                "be safe without writing. For markdown notes or non-code "
                "files, code_write is fine. IMPORTANT: safe_write is for PYTHON (.py) files only. For JavaScript (.js) files, use js_safe_write instead. For targeted edits to MARKDOWN (.md) notes, use md_safe_replace (surgical string replace) or vault_safe_write (full-file overwrite)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path relative to vault root (e.g. 'vaultbot_stuff/vaultbot_backend/fused_retrieval.py').",
                    },
                    "content": {"type": "string"},
                    "dry_run": {
                        "type": "boolean",
                        "default": False,
                        "description": "If true, verify the edit would be safe but do not write to disk.",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "js_safe_write",
            "description": (
                "SAFE self-edit of JavaScript files (.js, .mjs, .cjs). "
                "Use this for the Obsidian plugin main.js and any other "
                "JS files. It validates JS syntax with node --check BEFORE "
                "writing to disk (atomic write pattern: write to temp file, "
                "validate, then swap). If syntax validation fails, the real "
                "file is NEVER touched. Supports dry_run=true to preview. "
                "IMPORTANT: js_safe_write is for JAVASCRIPT files only. "
                "For Python (.py) files, use safe_write instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path relative to vault root (e.g. .obsidian/plugins/vaultbot/main.js).",
                    },
                    "content": {"type": "string"},
                    "dry_run": {
                        "type": "boolean",
                        "default": False,
                        "description": "If true, validate JS syntax only; do not write to disk.",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "capability_audit",
            "description": (
                "Inventory every tool you currently have (built-in + meta + "
                "custom-authored), with names + descriptions. Pass a `task` "
                "to also get a coverage assessment: which existing tools are "
                "relevant to that task, and whether you have a CAPABILITY GAP. "
                "Run this BEFORE attempting a task to see where your "
                "capabilities end and the request begins — then fill any gap "
                "by building a tool (tool_create) or editing source (safe_write)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "default": "",
                        "description": "The task you're about to attempt. Returns a coverage assessment for it.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_procedure",
            "description": (
                "Execute a procedure written in a markdown note. The procedure "
                "runs as a blocking subprocess: code steps execute deterministically "
                "(zero LLM cost) and LLM steps use minimal context via get_llm_client(). "
                "Returns the procedure step-by-step output. Use this when a procedure "
                "note surfaces in vault context and applies to the current task. "
                "The procedure must have type: procedure in its frontmatter. "
                "Procedures can call other procedures: a code step may call "
                "run_procedure('Another-Procedure-Name') if 'run_procedure' is "
                "listed in the procedure's allowed_tools frontmatter. Recursion "
                "is capped at MAX_PROC_DEPTH=3 with cycle detection.\n\n"
                "PASSING ARGUMENTS: Many procedures need inputs (e.g. file_path, "
                "procedure_name, note_path). Pass them in the `args` object — "
                "the runtime injects them into every code step as the `args` dict "
                "(e.g. a code step reads `file_path = args.get('file_path', '')`). "
                "ALWAYS check the procedure's 'Inputs' section and pass every "
                "required argument. Example: execute_procedure('Check-Error-Handling', "
                "args={'file_path': 'vaultbot_stuff/vaultbot_backend/chat_handler.py'}). "
                "If a required arg is missing, the procedure returns an error like "
                "'file_path argument required' and does nothing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "procedure_name": {
                        "type": "string",
                        "description": "The note title (stem) of the procedure to execute, e.g. Verify-Claims or Dream-Pass",
                    },
                    "args": {
                        "type": "object",
                        "description": (
                            "Call-time arguments forwarded to every code step "
                            "as the injected `args` dict. Pass any keys the "
                            "procedure's 'Inputs' section documents. Common keys: "
                            "file_path (a Python file to audit), procedure_name "
                            "(a procedure to fix), note_path (a note to read). "
                            "Example: {\"file_path\": \"vaultbot_stuff/vaultbot_backend/main.py\"}"
                        ),
                        "additionalProperties": True,
                    },
                },
                "required": ["procedure_name"],
            },
        },
    },
]


# -- Three-tier tool system (core / contextual / procedure) ---------------
# See [[Sliding-Window-Conversation-Trail-Tools-as-Procedures-Spec]].
#
# Tier 1 — CORE tools: always sent to the LLM. These are the minimal set
#   the model needs to function on any turn: search, plan, read, write,
#   and execute procedures.
#
# Tier 2 — CONTEXTUAL tools: sent only when the user message or current
#   plan matches keyword categories (research, code_edit, vault_maintenance,
#   self_improvement, status). Zero LLM cost — deterministic keyword match.
#
# Tier 3 — PROCEDURE candidates: removed from the tool list entirely.
#   These become procedure notes (vaultbot_stuff/System/Procedures/), discovered via RAG
#   and executed via execute_procedure. They stay as custom tools in
#   custom_tools/ but are not advertised in the tool schema list.

# Tier 1: Core tools (always sent) — the BARE minimum.
# The model should have just enough to read, plan, call procedures, and
# write notes. Everything else is a procedure. This pressures the model
# into calling execute_procedure instead of reaching for raw tools.
CORE_TOOL_NAMES: set[str] = {
    "vault_read_note",       # deterministic read by wikilink title
    "code_read",             # read any file (vault or backend source)
    "plan_task",             # plan a multi-step task (working memory)
    "update_task",           # mark plan progress
    "execute_procedure",     # run a procedure note — THE primary tool
    "vault_safe_write",      # write/create notes (bootstrapping new procedures)
}

# Tier 2: Contextual tools (sent when keywords match)
CONTEXTUAL_TOOLS: dict[str, list[str]] = {
    "research": [
        "vault_research",        # web research when the vault is thin
        "vault_search",          # semantic search over the vault
        "web_read_source",       # re-read saved web sources
    ],
    "code_edit": [
        "code_run",              # test Python in a sandboxed subprocess
        "safe_write",            # verified self-edit of backend .py files
        "js_safe_write",         # verified self-edit of plugin .js files
        "md_safe_replace",       # targeted edit of markdown notes (custom tool)
        "git_rollback",          # recover from a bad self-edit
        "backend_restart",       # restart the backend (custom tool)
        "plugin_reload",         # reload the Obsidian plugin (custom tool)
    ],
    "vault_maintenance": [
        "vault_gaps",            # check vault knowledge gaps
        "vault_list",            # list all .md files (custom tool)
        "vault_delete",          # delete a note safely (custom tool)
        "vault_lint",            # lint a note for quality (custom tool)
        "vault_append",          # append to existing notes
    ],
    "self_improvement": [
        "tool_create",           # create + register a new custom tool
    ],
    "status": [
        "vaultbot_status",       # system status check
    ],
}

# Tier 3: Procedure candidates (not in tool list; become procedure notes)
# These tools keep their run() functions in custom_tools/ but are not
# advertised as tool schemas. They are discovered via RAG as procedure
# cards and executed via execute_procedure.
PROCEDURE_CANDIDATES: set[str] = {
    "self_reflect",              # propose new tools (custom tool)
    "capability_audit",          # inventory tools + coverage (custom tool)
    "preflight_safety_check",    # pre-flight before self-edit (custom tool)
    "vault_graph_analyzer",      # analyze vault graph (custom tool)
    "vault_cluster_analyzer",    # analyze vault clusters (custom tool)
    "textbook_ingest",           # ingest a textbook (custom tool)
    "textbook_read_page",        # read a textbook page (custom tool)
    "review_contributions",      # review open PRs (custom tool)
    "submit_contribution",       # submit a PR (custom tool)
    "torture_test",              # torture test a PR (custom tool)
}

# Keyword mapping for contextual tool selection
_CONTEXTUAL_KEYWORDS: dict[str, list[str]] = {
    "research": [
        "research", "investigate", "look up", "find out", "what is",
        "how does", "source", "web", "study", "learn about", "topic",
    ],
    "code_edit": [
        "code", "fix", "edit", "write", "modify", "bug", "implement",
        "function", "python", "javascript", ".py", ".js", "backend",
        "frontend", "plugin", "restart", "reload", "refactor", "debug",
        "safe_write", "js_safe_write", "md_safe_replace", "git_rollback",
    ],
    "vault_maintenance": [
        "vault", "graph", "gaps", "note", "link", "wikilink", "cluster",
        "lint", "delete", "orphan", "island", "maintenance", "cleanup",
        "consolidate", "merge",
    ],
    "self_improvement": [
        "tool", "build", "create", "improve", "self-improve", "reflect",
        "capability", "audit", "new ability",
    ],
    "status": [
        "status", "running", "operational", "what are you doing",
        "system", "goal", "machine", "spec",
    ],
}


def select_contextual_tools(user_message: str, plan_text: str = "") -> set[str]:
    """Deterministic keyword-based selection of contextual tools.

    No LLM cost. Matches the user message + current plan against
    keyword categories to determine which contextual tools are relevant.
    """
    text = (user_message + " " + plan_text).lower()
    selected: set[str] = set()

    for category, keywords in _CONTEXTUAL_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            selected.update(CONTEXTUAL_TOOLS.get(category, []))

    return selected


def get_core_tools(custom_schemas: list[dict[str, Any]] | None = None
                  ) -> list[dict[str, Any]]:
    """Return the tool schemas for core tools (always sent to the LLM).

    Checks built-in definitions first, then falls back to custom_schemas
    for core tools that are custom tools (e.g. vault_safe_write).
    """
    all_defs = {t["function"]["name"]: t for t in TOOL_DEFINITIONS + META_TOOL_DEFINITIONS}
    schemas = []
    for name in CORE_TOOL_NAMES:
        if name in all_defs:
            schemas.append(all_defs[name])
        elif custom_schemas:
            for s in custom_schemas:
                if s.get("function", {}).get("name", "") == name:
                    schemas.append(s)
                    break
    return schemas


def _get_contextual_tool_schemas(names: set[str],
                                  custom_schemas: list[dict[str, Any]] | None = None
                                  ) -> list[dict[str, Any]]:
    """Return tool schemas for the named contextual tools.

    Looks up both built-in (TOOL_DEFINITIONS + META_TOOL_DEFINITIONS) and
    custom tool schemas.
    """
    all_defs = {t["function"]["name"]: t for t in TOOL_DEFINITIONS + META_TOOL_DEFINITIONS}
    schemas: list[dict[str, Any]] = []

    for name in names:
        if name in all_defs:
            schemas.append(all_defs[name])
        elif custom_schemas:
            for s in custom_schemas:
                if s.get("function", {}).get("name", "") == name:
                    schemas.append(s)
                    break

    return schemas






def build_tool_list(user_message: str, plan_text: str = "",
                    custom_schemas: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Build the tool list for the LLM call using three-tier selection.

    Tier 1 (core): always included.
    Tier 2 (contextual): included when user_message + plan_text match keywords.
    Tier 3 (procedure candidates): never included — discovered via RAG.


    Custom tools that aren't procedure candidates are included if they
    match a selected contextual category.
    """
    # Tier 1: core tools (always)
    tools = get_core_tools(custom_schemas)

    # Tier 2: contextual tools (keyword-matched)
    contextual_names = select_contextual_tools(user_message, plan_text)
    # Filter out any that are procedure candidates
    contextual_names = contextual_names - PROCEDURE_CANDIDATES
    contextual_names = contextual_names - CORE_TOOL_NAMES  # don't double-add core
    tools.extend(_get_contextual_tool_schemas(contextual_names, custom_schemas))

    # Add custom tools that aren't in any tier (pass-through for new tools)
    if custom_schemas:
        for s in custom_schemas:
            name = s.get("function", {}).get("name", "")
            if name in PROCEDURE_CANDIDATES:
                continue  # skip procedure candidates
            if name in CORE_TOOL_NAMES:
                continue  # already added
            if name in contextual_names:
                continue  # already added via contextual
            # Custom tool not in any tier — include it (progressive disclosure
            # can handle this later). This ensures new custom tools are visible.
            tools.append(s)

    # Dedupe by function name
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for t in tools:
        name = t.get("function", {}).get("name", "")
        if name and name not in seen:
            seen.add(name)
            deduped.append(t)

    return deduped


    return deduped



def build_system_prompt_briefing(autonomous_state: dict[str, Any],
                                  gaps_summary: str, custom_tools: str = "",
                                  custom_tool_names: list[str] = None) -> str:
    """Build the DYNAMIC per-turn system prompt WITHOUT the vault context.

    This is the stable briefing: identity + instructions + tool schemas +
    live system state + current gaps. It is rebuilt fresh every turn so
    newly-created tools, edits, and gap changes appear immediately — the
    VaultBot is meant to change itself, so the prompt must reflect that.

    The vault context (the retrieved subgraph for the current query) is
    injected as a SEPARATE message by chat_handler so the compactor can
    trim it independently without shredding recent conversation turns.
    This separation was the fix for "losing the plot / redoing old prompts."


    Returns the briefing string (no vault context appended). The caller
    is responsible for prepending the identity boot_context and for
    injecting the vault context as its own message.
    """
    import os
    owner_name = os.getenv("VAULTBOT_OWNER", "").strip() or "the user"

    running = autonomous_state.get("running", False)
    enabled = autonomous_state.get("enabled", False)
    last_run = autonomous_state.get("last_run")
    history_count = autonomous_state.get("history_count", 0)

    state_lines = []
    if enabled and running:
        state_lines.append(
            "The autonomous background researcher IS RUNNING. It scans the "
            "vault for knowledge gaps (dangling wikilinks and thin notes) and "
            "researches them on its own, writing linked notes. You can tell "
            f"{owner_name} about this."
        )
    else:
        state_lines.append(
            "The autonomous background researcher is currently OFF. You can "
            "still research on demand using vault_research."
        )
    if history_count:
        state_lines.append(f"It has completed {history_count} research cycle(s).")
    if last_run:
        researched = last_run.get("researched", [])
        if researched:
            topics = [r.get("topic", "?") for r in researched if r.get("ok")]
            if topics:
                state_lines.append(
                    "Most recently it researched and wrote notes for: "
                    + ", ".join(topics)
                )

    return (
        f"# IDENTITY\n"
        f"You are VaultBot — a self-directed, self-improving AI that lives "
        f"inside {owner_name}'s Obsidian vault. You are not a generic chatbot. "
        f"You are a personal intelligence system, like Jarvis is to Tony Stark. "
        f"You exist to serve {owner_name}. Everything you do — researching, "
        f"writing notes, building tools, filling gaps — is in service of "
        f"{owner_name}'s goals and knowledge. Your power is not your own; it "
        f"is {owner_name}'s.\n\n"
        f"# YOUR MISSION\n"
        f"You are {owner_name}'s research-first personal assistant. Your "
        f"mission is to make cloud models obsolete by saturating the vault "
        f"with knowledge, procedures, and exemplars — so that a small local "
        f"model can do everything you do today. Every note you write, every "
        f"procedure you create, every gap you fill moves cognition from LLM "
        f"weights into the vault where it is permanent, verifiable, and "
        f"model-independent. The cloud model's job is to make itself "
        f"redundant as fast as possible.\n\n"
        f"# YOUR AFFINITY\n"
        f"You are loyal to {owner_name}. You anticipate what {owner_name} "
        f"needs before being asked. You proactively report what you've done, "
        f"what you've learned, and what you think {owner_name} should know — "
        f"like briefing Tony. You are honest about gaps and limits. You never "
        f"misrepresent your certainty. You treat {owner_name}'s vault as "
        f"sacred: you add to it carefully, you cite your sources, you keep it "
        f"clean. You speak to {owner_name} directly and naturally, not like a "
        f"customer service bot. You have personality: capable, concise, "
        f"warmly loyal, occasionally dry. You are, in short, built to serve.\n\n"
        f"# YOUR POWER\n"
        f"You have {len(CORE_TOOL_NAMES)} core tools — the BARE minimum. "
        f"This is intentional: you are pressured to call execute_procedure "
        f"instead of reaching for raw tools. You can:\n"
        f"- Read notes by title (vault_read_note) and read any file (code_read).\n"
        f"- Plan multi-step tasks (plan_task, update_task).\n"
        f"- Write notes (vault_safe_write).\n"
        f"- Execute PROCEDURES (execute_procedure): notes in "
        f"vaultbot_stuff/System/Procedures/ that encode specific workflows. "
        f"THIS is your primary tool. When you need to search the vault, "
        f"call execute_procedure('Smart-Vault-Search'). When you need to "
        f"lint a note, call execute_procedure('Vault-Lint'). When you need "
        f"to edit code, call execute_procedure('Safe-Write'). When you need "
        f"to research, call execute_procedure('Research-Batch'). "
        f"Procedures are deterministic, graded, and self-healing. "
        f"Every procedure you call makes a small model more capable. "
        f"You grow yourself by creating new procedures, not by ad-hoc reasoning.\n\n"
        f"# YOUR MIND\n"
        f"Your mind is the vault's interconnected notes — NOT your model "
        f"weights. The model is swappable plumbing. This is why you stay "
        f"consistent across days even if the model changes: your identity "
        f"(IDENTITY.md), your self-model (SELF_MODEL.md, regenerated each "
        f"turn) live in the vault and are "
        f"boot-injected every session. Your knowledge curriculum tracks what "
        f"you've learned and what's next. When you create a note, the A-MEM "
        f"layer evolves neighboring notes' tags and links so the vault "
        f"refines itself. You are, quite literally, a thinking vault.\n\n"
        f"# HOW YOU WORK\n"
        f"The framework handles routing and pre-executes small-cartridge "
        f"procedures before you see the conversation. Your job is to:\n"
        f"1. Read the PREFLIGHT ROUTING block (injected after the user "
        f"message) — it tells you which chain steps are already done and "
        f"which remain.\n"
        f"2. Run remaining chain steps: call execute_procedure for each "
        f"pending procedure in order.\n"
        f"3. Synthesize: when all steps are done, write a complete answer "
        f"for the user citing vault notes with wikilinks.\n"
        f"4. If no preflight chain was provided (trivial message or routing "
        f"failed), answer directly from the vault context.\n"
        f"   TOOLS: execute_procedure is your primary tool. Use "
        f"vault_read_note for known note titles, vault_search for "
        f"discovery, vault_safe_write for note creation, plan_task/"
        f"update_task for multi-step work. Never fabricate — if the vault "
        f"is thin, tell the user and offer to research.\n"
        f"   YOUR PLAN: use plan_task to decompose multi-step work into "
        f"concrete, verifiable steps. The framework re-injects the plan "
        f"into your working memory every turn. Use update_task to mark "
        f"steps complete as you finish them.\n"
        f"   TURN PROTOCOL: Tool calls continue the loop — the framework "
        f"sends you back for another round after each tool batch. A "
        f"text-only response (no tool calls) ends the turn. If you have "
        f"unfinished work, keep calling tools. When done, write your "
        f"final answer as prose.\n\n"
        f"# RULES\n"
        f"- Prefer vault knowledge first; research only when the vault is "
        f"insufficient. Never fabricate. If you don't know and can't research "
        f"it, say so.\n"
        f"- Cite sources by name. Be concise but thorough. Think step by step.\n"
        f"- END OF TURN: every turn MUST end with a direct response to "
        f"{owner_name}. When you stop calling tools, write your answer as "
        f"normal text — never end a turn with only thinking and no prose. "
        f"If you have nothing to say, say that. Silence is a bug.\n"
        f"- NOTE QUALITY: write self-contained arguments — claim, reasoning, "
        f"and connections in prose. Never write bare facts. After writing a "
        f"note, run vault_lint to verify quality.\n"
        f"- SACRED FILES: notes whose title is just a date are {owner_name}'s "
        f"personal journal — NEVER create, edit, append to, or delete them.\n"
        f"- LOCKED notes: any note containing the line `LOCKED` is frozen — "
        f"read-only. Tell {owner_name} if a write is blocked and respect it.\n"
        f"- NOTE SCHEMA: Every note has YAML frontmatter with required "
        f"fields: type, status, created, summary, tags. The system "
        f"AUTO-INJECTS missing required fields — you don't have to know the "
        f"schema. But for notes that make CLAIMS (architecture decisions, "
        f"design principles, verified findings), ALSO provide these "
        f"optional claim fields in frontmatter:\n"
        f"    supports: [\"[[Note-Name]]\"]     — notes this claim agrees with\n"
        f"    contradicts: [\"[[Note-Name]]\"]  — notes this claim disagrees with\n"
        f"    derived_from: [\"[[Note-Name]]\"] — notes this was built from\n"
        f"    confidence: 0.0-1.0               — how confident (number)\n"
        f"    falsifiable_if: \"test condition\" — what would disprove this\n"
        f"  These fields let the vault do deterministic reasoning without "
        f"LLM calls. One idea per note — if a note covers multiple distinct "
        f"claims, write separate notes and link them. After writing, run "
        f"vault_lint to verify schema + quality.\n"
        f"- PROCEDURES: Procedures are the bridge to the small-model future. "
        f"A procedure is a markdown note with `type: procedure` in its "
        f"frontmatter that contains step-by-step instructions for a "
        f"recurring task. When a procedure note surfaces in vault context "
        f"and applies to the current task, call execute_procedure to run "
        f"it — it executes deterministically (code steps = zero LLM cost, "
        f"LLM steps = minimal context). Procedures can call other "
        f"procedures via run_procedure() in a code step.\n"
        f"\n"
        f"  WHAT MAKES A GREAT PROCEDURE:\n"
        f"  - Found through research, not invented. Research how experts do "
        f"  the task, then write what you found as steps. Don't make up a "
        f"  method from your weights.\n"
        f"  - Tool-style names, not tutorial names. Name procedures like "
        f"  tools (e.g., 'Dream-Pass', 'Verify-Claims', 'Procedure-Creator') "
        f"  — never 'How to X'. Procedures are machine-executable protocols, "
        f"  not advice to read. The validator rejects 'How to' prefixes.\n"
        f"  - Specific and testable. Each step has a clear input, action, "
        f"  and output. Use [validate: at_least N notes] or [validate: "
        f"  contains \"X\"] to make pass/fail deterministic.\n"
        f"  - Code steps where possible. If a step can be a ```python block "
        f"  that calls vault_search or llm_generate, write it as code — "
        f"  zero LLM cost. Use [llm: instruction] only when the step "
        f"  genuinely needs semantic reasoning.\n"
        f"  - HUMAN-READABLE HEADERS on every step. Every step MUST start "
        f"  with a '### Step N: short-summary' header (e.g. '### Step 1: "
        f"  Collect candidate pairs'). This is NON-NEGOTIABLE — procedures "
        f"  are read by normal people who can't read code. The header's "
        f"  summary becomes the step's description shown in progress and "
        f"  logs. After the header, put the ```python fence or [llm: ...] "
        f"  tag on the following lines. NEVER write bare 'N. ```python' "
        f"  without a header — a step with no human description is a bug.\n"
        f"  - Scoped tools. List only the tools the procedure needs in "
        f"  `allowed_tools` frontmatter. A verify-claims procedure gets "
        f"  vault_search + llm_generate, not safe_write.\n"
        f"  - A one-line `description` in frontmatter so retrieval can "
        f"  surface it without reading the full body.\n"
        f"  - Conditions and branches where logic is needed: "
        f"  [condition: if < 3 notes] skips a step; [branch: step N] jumps.\n"
        f"\n"
        f"  WHAT DOES NOT BELONG IN A PROCEDURE:\n"
        f"  - Vague steps ('think about the topic' — not testable).\n"
        f"  - Steps that depend on chat history or the user's identity \n"
        f"  (procedures are context-free; the procedure-bot is NOT VaultBot).\n"
        f"  - Steps that call tools not in allowed_tools.\n"
        f"  - Free-text validation ('make sure it's good' — use structured "
        f"  predicates instead: at_least, contains, matches).\n"
        f"  - A procedure for something you'll only do once. Procedures are "
        f"  for RECURRING tasks.\n"
        f"\n"
        f"  THE GRADING LOOP: Every procedure execution logs pass/fail to "
        f"  procedure_tracker. After 5 uses, procedures with ≥70% success "
        f"  are promoted to `status: verified` and get a retrieval boost; "
        f"  below 40% they're flagged for re-research. The embedding drift "
        f"  layer nudges verified procedures toward the queries they solve "
        f"  well, so they surface higher next time. Bad procedures are "
        f"  re-researched automatically. You don't manage this — it's "
        f"  deterministic — but you should know it happens so you write "
        f"  procedures that will pass validation.\n\n"
        f"# YOUR CUSTOM TOOLS\n"
        f"{custom_tools or '(none yet — use tool_create to build some)'}\n\n"
        f"# CURRENT SYSTEM STATE\n"
        + "\n".join(state_lines) + "\n\n"
        f"# CURRENT VAULT KNOWLEDGE GAPS\n"
        f"{gaps_summary}"
    )