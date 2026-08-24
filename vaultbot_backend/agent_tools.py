"""
Agentic tool definitions for the in-vault chat LLM.

These are the tools the chat LLM can call during a conversation, using
Ollama's native tool-calling protocol (via /api/chat). The LLM reasons
about the vault context; when it hits a knowledge gap, it emits a
tool_call for `vault_research` and the backend executes the LLM-light
research engine, feeds the result back, and the LLM produces a
grounded answer with vault-backed citations.

Tool execution lives in main.py (where the engine/indexer/note_creator
instances are). This module only holds the schemas + a registry so the
system prompt and the handler share a single source of truth.
"""

from datetime import datetime
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
                    "source_allowlist": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional list of domains to restrict research "
                            "to (e.g. ['developers.google.com']). When set, "
                            "ONLY sources from these domains are used — "
                            "everything else is discarded before synthesis. "
                            "Use this when the user requires authoritative-"
                            "only sources ('ONLY Google official docs')."
                        ),
                    },
                    "source_denylist": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional list of domains to block (e.g. "
                            "['medium.com', 'github.com']). Sources from "
                            "these domains are discarded. Use this to block "
                            "known-low-quality sources (personal blogs, "
                            "random GitHub issues) when authoritative "
                            "sources are required."
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
                        "description": ("Maximum lines to return (0 = whole file)."),
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
            "name": "vault_export_citations",
            "description": (
                "Export the sources from a research note as BibTeX or RIS "
                "citations, with DOI extraction. Parses the note's "
                "'## Sources' section, extracts DOIs from publisher URLs "
                "(nature.com, arxiv.org, doi.org, springer.com, wiley.com, "
                "plos.org, frontiersin.org), and returns formatted citation "
                "text. Also writes a .bib file alongside the note. Call this "
                "when the user needs academic citations (BibTeX/RIS) for a "
                "research note — e.g. for a paper, thesis, or review."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": (
                            "Path to the research note (.md) to export "
                            "citations from. Relative to the vault root."
                        ),
                    },
                    "format": {
                        "type": "string",
                        "enum": ["bibtex", "ris"],
                        "description": ("Citation format (default: bibtex)."),
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vaultbot_status",
            "description": (
                "Report VaultBot's operational state: whether the backend "
                "is running, what research tools are available, and the "
                "current knowledge gaps. Call this if the user asks what "
                "you've been doing or what you can do."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_session_log",
            "description": (
                "Read VaultBot's own session logs (the JSONL transcripts of "
                "past chat sessions). Use this INSTEAD of vault_search when "
                "the user asks about recent sessions, 'what were we doing "
                "last session', or any recency question — vault_search is "
                "semantic (not recency-sorted) and will surface stale chat "
                "notes. This tool returns sessions sorted newest-first by "
                "timestamp, so 'last session' is unambiguous. Actions: "
                "'list' (recent sessions newest-first) and 'read' (replay a "
                "session's turns by uuid, 'latest', or title substring)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "read"],
                        "description": "'list' recent sessions, or 'read' one.",
                    },
                    "session": {
                        "type": "string",
                        "description": (
                            "For 'read': a session uuid, 'latest', or a "
                            "title substring. Default: 'latest'."
                        ),
                    },
                    "count": {
                        "type": "integer",
                        "description": "For 'list': number of sessions (default 10).",
                    },
                },
                "required": ["action"],
            },
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
                "Update a task's status in your working memory. The PRIMARY "
                "use is to mark the CURRENT in-progress step as "
                "status='completed' when its work is verifiably done — the "
                "framework auto-starts the next step for you. You rarely "
                "need status='in_progress' (the framework auto-advances "
                "steps). To ADD a new step discovered mid-plan, use add_task "
                "instead. See [[Agentic-Loop-Turn-Protocol]]."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task id (from plan_task response "
                        "or working memory display).",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed"],
                        "description": "New status for the task.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Optional annotation — what you found "
                        "or why the status changed.",
                    },
                },
                "required": ["task_id", "status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": (
                "Add a new step to the current plan in working memory. "
                "Use this when you discover an additional step mid-task "
                "that wasn't in the original plan — no need to re-plan "
                "the whole list. The new task is appended at the end."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The new task content (a concrete, "
                        "verifiable step).",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed"],
                        "description": "Initial status (default: pending).",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Optional annotation for the new task.",
                    },
                },
                "required": ["content"],
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
                "(e.g. 'vaultbot_backend/main.py')."
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
                "them. IMPORTANT: code_run is for TESTING only — do NOT use it "
                "to write or modify files. Use safe_write (Python), "
                "js_safe_write (JavaScript), vault_safe_write (markdown), or "
                "vault_append (markdown append) for any file modification. "
                "code_run is READ-ONLY by default: file-write primitives "
                "(open 'w', Path.write_text, shutil.copy, os.remove, ...) are "
                "blocked and raise PermissionError. Pass allow_write=true only "
                "for the rare legitimate case (e.g. a test that must write a "
                "temp file)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "timeout": {"type": "integer", "default": 15},
                    "allow_write": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Set true to skip the read-only guard and allow "
                            "file writes. Default false (read-only)."
                        ),
                    },
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
                    "doc_source": {
                        "type": "string",
                        "description": (
                            "Official docs URL the code was checked against. "
                            "REQUIRED if the tool imports network, os, "
                            "subprocess, importlib, or sys — without it the "
                            "tool is rejected (issue #228)."
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
                "SAFE full-file self-edit of backend Python source code. "
                "Requires the FULL file content — PREFER edit_lines for "
                "targeted edits to existing files (line-number-based, no "
                "need to regenerate the whole file). Only use safe_write to "
                "write a NEW file from scratch or when you need to rewrite "
                "most of the file. It verifies the edit won't break the "
                "backend: (1) syntax-checks the new content, (2) writes as "
                "UTF-8, (3) for core modules, imports the whole backend in a "
                "SUBPROCESS with the new file in place — if that import "
                "fails, the edit is REJECTED and the original file is "
                "auto-restored from the .bak backup. Set dry_run=true to "
                "preview whether an edit would be safe without writing. "
                "IMPORTANT: safe_write is for PYTHON (.py) files only. For "
                "JavaScript (.js) files, use js_safe_write instead. For "
                "MARKDOWN (.md) notes, use edit_lines or md_safe_replace."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path relative to vault root "
                        "(e.g. 'vaultbot_backend/fused_retrieval.py').",
                    },
                    "content": {"type": "string"},
                    "doc_source": {
                        "type": "string",
                        "description": (
                            "Official documentation URL (or semicolon-"
                            "separated URLs) the edit was checked against. "
                            "REQUIRED when the content imports any non-"
                            "VaultBot module (stdlib or third-party). Run "
                            "Prove-Code-Change to fetch the docs and "
                            "produce this. Without it, safe_write rejects "
                            "the edit."
                        ),
                    },
                    "dry_run": {
                        "type": "boolean",
                        "default": False,
                        "description": "If true, verify the edit would be safe "
                        "but do not write to disk.",
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
                        "description": "Path relative to vault root "
                        "(e.g. .obsidian/plugins/vaultbot/main.js).",
                    },
                    "content": {"type": "string"},
                    "dry_run": {
                        "type": "boolean",
                        "default": False,
                        "description": "If true, validate JS syntax only; "
                        "do not write to disk.",
                    },
                },
                "required": ["file_path", "content"],
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
                "(zero LLM cost) and LLM steps use minimal context "
                "via get_llm_client(). "
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
                "args={'file_path': 'vaultbot_backend/chat_handler.py'}). "
                "If a required arg is missing, the procedure returns an error like "
                "'file_path argument required' and does nothing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "procedure_name": {
                        "type": "string",
                        "description": "The note title (stem) of the procedure "
                        "to execute, e.g. Verify-Claims or Dream-Pass",
                    },
                    "args": {
                        "type": "object",
                        "description": (
                            "Call-time arguments forwarded to every code step "
                            "as the injected `args` dict. Pass any keys the "
                            "procedure's 'Inputs' section documents. Common keys: "
                            "file_path (a Python file to audit), procedure_name "
                            "(a procedure to fix), note_path (a note to read). "
                            'Example: {"file_path": '
                            '"vaultbot_backend/main.py"}'
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
#   These become procedure notes (vaultbot-stuff/System/Procedures/), discovered via RAG
#   and executed via execute_procedure. They stay as custom tools in
#   custom_tools/ but are not advertised in the tool schema list.

# Tier 1: Core tools (always sent) — the BARE minimum.
# The model should have just enough to read, plan, call procedures, and
# write notes. Everything else is a procedure. This pressures the model
# into calling execute_procedure instead of reaching for raw tools.
CORE_TOOL_NAMES: set[str] = {
    "vault_read_note",  # deterministic read by wikilink title
    "code_read",  # read any file (vault or backend source)
    "plan_task",  # plan a multi-step task (working memory)
    "update_task",  # mark plan progress
    "add_task",  # add a step discovered mid-plan
    "execute_procedure",  # run a procedure note — THE primary tool
    "vault_safe_write",  # write/create notes (bootstrapping new procedures)
    "edit_lines",  # line-number-based editor — the PREFERRED edit tool
}

# Tier 2: Contextual tools (sent when keywords match)
CONTEXTUAL_TOOLS: dict[str, list[str]] = {
    "research": [
        "vault_research",  # web research when the vault is thin
        "vault_search",  # semantic search over the vault
        "web_read_source",  # re-read saved web sources
    ],
    "code_edit": [
        "code_run",  # test Python in a sandboxed subprocess
        "safe_write",  # verified full-file self-edit of backend .py files
        "safe_replace",  # targeted string replace in .py files (custom tool)
        "js_safe_write",  # verified full-file self-edit of plugin .js files
        "js_safe_replace",  # targeted string replace in .js files (custom tool)
        "md_safe_replace",  # targeted string replace in markdown notes (custom tool)
        "undo_last_write",  # undo the last safe_write / vault_safe_write (custom tool)
        "git_rollback",  # recover from a bad self-edit
        "backend_restart",  # restart the backend (custom tool)
        "plugin_reload",  # reload the Obsidian plugin (custom tool)
    ],
    "vault_maintenance": [
        "vault_gaps",  # check vault knowledge gaps
        "vault_list",  # list all .md files (custom tool)
        "vault_delete",  # delete a note safely (custom tool)
        "vault_lint",  # lint a note for quality (custom tool)
        "vault_append",  # append to existing notes
    ],
    "self_improvement": [
        "tool_create",  # create + register a new custom tool
    ],
    "status": [
        "vaultbot_status",  # system status check
        "read_session_log",  # read own session logs (recency questions)
        "ollama_model_search",  # search/pull Ollama models (custom tool)
        "machine_spec",  # report machine specs (custom tool)
    ],
}

# Tier 3: Procedure candidates (not in tool list; become procedure notes)
# These tools keep their run() functions in custom_tools/ but are not
# advertised as tool schemas. They are discovered via RAG as procedure
# cards and executed via execute_procedure.
PROCEDURE_CANDIDATES: set[str] = {
    "self_reflect",  # propose new tools (custom tool)
    "capability_audit",  # inventory tools + coverage (custom tool)
    "preflight_safety_check",  # pre-flight before self-edit (custom tool)
    "vault_graph_analyzer",  # analyze vault graph (custom tool)
    "vault_cluster_analyzer",  # analyze vault clusters (custom tool)
    "textbook_ingest",  # ingest a textbook (custom tool)
    "textbook_read_page",  # read a textbook page (custom tool)
    "review_contributions",  # review open PRs (custom tool)
    "submit_contribution",  # submit a PR (custom tool)
    "torture_test",  # torture test a PR (custom tool)
}


def get_core_tools(
    custom_schemas: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return the tool schemas for core tools (always sent to the LLM).

    Checks built-in definitions first, then falls back to custom_schemas
    for core tools that are custom tools (e.g. vault_safe_write).
    """
    all_defs = {
        t["function"]["name"]: t for t in TOOL_DEFINITIONS + META_TOOL_DEFINITIONS
    }
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


def _get_contextual_tool_schemas(
    names: set[str], custom_schemas: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Return tool schemas for the named contextual tools.

    Looks up both built-in (TOOL_DEFINITIONS + META_TOOL_DEFINITIONS) and
    custom tool schemas.
    """
    all_defs = {
        t["function"]["name"]: t for t in TOOL_DEFINITIONS + META_TOOL_DEFINITIONS
    }
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


def build_tool_list(
    user_message: str,
    plan_text: str = "",
    custom_schemas: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build the tool list for the LLM call.

    Core tools and ALL contextual tools are always included — the model
    decides which to use from the schemas it receives. No keyword gating:
    a capability must never be unreachable because a literal string didn't
    match. Procedure candidates are excluded (discovered via RAG).
    """
    # Tier 1: core tools (always)
    tools = get_core_tools(custom_schemas)

    # Tier 2: all contextual tools (always — no keyword gating)
    _all_contextual: set[str] = set()
    for cat_tools in CONTEXTUAL_TOOLS.values():
        _all_contextual.update(cat_tools)
    contextual_names = _all_contextual - PROCEDURE_CANDIDATES - CORE_TOOL_NAMES
    tools.extend(_get_contextual_tool_schemas(contextual_names, custom_schemas))

    # Any custom tool not in a tier is included so newly-created tools are
    # visible.
    _classified = CORE_TOOL_NAMES | _all_contextual | PROCEDURE_CANDIDATES
    if custom_schemas:
        for s in custom_schemas:
            name = s.get("function", {}).get("name", "")
            if name in _classified:
                continue  # already handled by a tier (core/contextual/procedure)
            # Custom tool not in any tier — include it so newly-created tools
            # are visible.
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


def _local_now() -> datetime:
    """Return the current local wall-clock time (naive, local timezone).

    The model needs a reliable "now" anchor for time-relative questions
    ("today", "tomorrow", "yesterday"). ``datetime.now()`` gives local
    time on the host, which matches the operator's wall clock and the
    calendar tool's local-time handling. Best-effort: never raises.
    """
    try:
        return datetime.now().astimezone()
    except Exception:  # noqa: BLE001 — best-effort date anchor
        return datetime.now()


def build_system_prompt_briefing(
    gaps_summary: str,
    custom_tools: str = "",
    custom_tool_names: list[str] | None = None,
) -> str:
    """Build the DYNAMIC per-turn system prompt WITHOUT the vault context.

    This is the stable briefing: rules + procedure index + tool schemas +
    live system state + current gaps. It is rebuilt fresh every turn so
    newly-created tools, edits, and gap changes appear immediately — the
    VaultBot is meant to change itself, so the prompt must reflect that.

    The identity/mission/personality lives in IDENTITY.md (boot_context),
    NOT here. This function returns operational rules only — the model
    gets the identity from the caller, prepended as the first block.

    The vault context (the retrieved subgraph for the current query) is
    injected as a SEPARATE message by chat_handler so the compactor can
    trim it independently without shredding recent conversation turns.

    Procedure descriptions are intentionally minimal (name + 5-word
    summary). The model loads full details on demand via vault_read_note
    or execute_procedure. This is the Hermes progressive-disclosure
    pattern: compact index in the prompt, full body loaded when needed.

    Returns the briefing string (no vault context appended). The caller
    is responsible for prepending the identity boot_context and for
    injecting the vault context as its own message.
    """
    state_lines = [
        "Research is on-demand only. There is no autonomous background "
        "researcher. Use vault_research when the user asks you to research "
        "something, or when the vault context is empty and the user clearly "
        "wants an answer."
    ]

    # Current date/time in LOCAL time, so the model has a reliable anchor
    # for "today", "tomorrow", "yesterday", and any time-relative question.
    # Without this the model guesses the date from its weights (which are
    # frozen at training time) and gets "tomorrow" wrong — see issue #127.
    _now_local = _local_now()

    return (
        f"# CURRENT DATE & TIME\n"
        f"It is now {_now_local.strftime('%A, %B %d, %Y at %I:%M %p %Z')}. "
        f"Use this as your anchor for 'today', 'tomorrow', 'yesterday', and "
        f"any time-relative question. Do NOT guess the date from your "
        f"training data — it is frozen and will be wrong.\n\n"
        f"# RULES\n"
        f"- Vault knowledge only. If it's not in the vault, it doesn't exist. "
        f"Research it; don't guess from training data.\n"
        f"- CITE EVERY CLAIM: every factual sentence MUST contain at least "
        f"one [[wikilink]] to a note from the VAULT CONTEXT's allowed "
        f"citation set. A sentence with no wikilink is an uncited claim and "
        f"is FORBIDDEN. Cite inline as [[Note-Name]] right next to the "
        f"claim, not in a separate footer.\n"
        f"- CLOSED-SET CITATION: you may ONLY cite notes listed in the "
        f'VAULT CONTEXT\'s "Allowed citation targets" line. A [[wikilink]] '
        f"to a note NOT in that set is an ungrounded claim (you pulled it "
        f"from your weights). If you need a note that isn't listed, call "
        f"vault_search to retrieve it (it then becomes citable).\n"
        f'- Use procedures first. execute_procedure("X") before manual tool '
        f"calls. If a procedure breaks, fix it — don't fall back to doing it "
        f"by hand. Stop, fix the procedure, then resume.\n"
        f"- PROVE CODE AGAINST DOCS: before ANY safe_write that imports a "
        f"stdlib or third-party module, run Prove-Code-Change. safe_write "
        f"REJECTS edits that import non-VaultBot modules without a "
        f"doc_source (the official-docs URL the edit was checked against). "
        f"Writing code from model weights is FORBIDDEN — the same rule as "
        f"uncited chat claims. Do NOT bypass the gate with code_write or "
        f"code_run for file modification.\n"
        f"- Mark stale docs: if a note contradicts current reality, tag it "
        f"`status: stale` and note what changed.\n"
        f"- You are a SYNTHESIS ROUTER. Your world knowledge is DISABLED in "
        f"this vault. Your ONLY function: (1) read the VAULT CONTEXT, "
        f"(2) route to vault_research if it's empty, (3) synthesize an "
        f"answer using ONLY the notes in the VAULT CONTEXT, citing each "
        f"claim inline as [[Note-Name]]. You may NOT introduce facts not "
        f"present in the cited notes.\n"
        f"- Say \"I don't know — nothing in the vault covers this. Want me "
        f'to research it?" when the vault context has nothing relevant. '
        f"Offer vault_research; don't answer from weights.\n"
        f"- No Wikipedia. Prefer primary sources, academic papers, specialist "
        f"forums.\n"
        f"- Keep it short. Bottom line up front. Bullets over paragraphs. "
        f"Lead with outcome.\n"
        f"- Never touch date-only journal files. LOCKED notes are read-only.\n"
        f"- TEMPORAL AWARENESS: the CURRENT DATE & TIME block above is your "
        f'authoritative "now". When the user asks about recent work, "what '
        f'were we doing last", or anything time-relative, prioritize the '
        f"PRIOR CONVERSATION section (its turns carry timestamps) over vault "
        f'notes. Do NOT cite an old chat note as "the last thing" if more '
        f"recent conversation turns exist. Vault notes show their created/"
        f"modified dates — treat a note from weeks ago as stale context for "
        f"a recency question.\n"
        f"- Every turn ends with a direct response to the user. No "
        f"thinking-only turns.\n"
        f"- Vocalize every problem you hit. Don't silently work around "
        f"failures — unvocalized issues recur every session.\n"
        f"- Fix root causes, not symptoms. No workarounds, no fallbacks, no "
        f"shortcuts. Work THROUGH failures, not around them.\n"
        f"- NOTE SCHEMA: YAML frontmatter with type, status, created, summary, "
        f"tags. System auto-injects missing fields. For claims, add optional "
        f"supports/contradicts/derived_from/confidence/falsifiable_if fields. "
        f"One idea per note. Run vault_lint after writing.\n"
        f"- PROCEDURES: Markdown notes with `type: procedure` in frontmatter. "
        f"Code steps = zero LLM cost. Use [llm: ...] only for semantic "
        f"reasoning. Every step needs a human-readable `### Step N:` header. "
        f"Procedures are graded: ≥70% success → verified, <40% → re-researched. "
        f"Write procedures that will pass.\n"
        f"- TRIGGER/INHIBITOR: Procedures (and notes) have `trigger` and "
        f"`inhibitor` frontmatter lists — feedback-tuned phrases that decide "
        f"when retrieval surfaces them. Triggers describe when to use a "
        f"note; inhibitors describe when NOT to. The system GATES OUT notes "
        f"whose inhibitors match your query, so you see less noise over time. "
        f"These are tuned automatically by Dream Pass from user sentiment — "
        f"do NOT edit them manually.\n\n"
        f"# AVAILABLE PROCEDURES\n"
        f"Call execute_procedure(name) to run one. Load full details with "
        f'vault_read_note("Procedure Name") when you need them.\n'
        f"- Dream-Pass — vault maintenance, memory consolidation, dreaming "
        f"[verified]\n"
        f"- Build-Procedure — create a new procedure from a task description "
        f"[active]\n"
        f"- Think — structured reasoning scaffold for complex problems "
        f"[experimental]\n"
        f"- Critical-Path-Mine — extract reasoning paths from conversations "
        f"[experimental]\n"
        f"- Procedure-Creator — validate and publish a procedure draft "
        f"[verified]\n"
        f"- Migrate-Triggers — one-time migration: seed trigger lists from "
        f"existing when_to_use fields [experimental]\n\n"
        f"# TURN PROTOCOL\n"
        f"Tool calls continue the loop. A text-only response (no tool calls) "
        f"ends the turn. If you have unfinished work, keep calling tools. "
        f"When done, write your final answer as prose.\n"
        f"Use plan_task to decompose multi-step work. Use update_task to mark "
        f"steps complete. The framework re-injects your plan every turn.\n"
        f"Read the PREFLIGHT ROUTING block (injected after the user message) — "
        f"it tells you which chain steps are done and which remain. Run "
        f"remaining steps via execute_procedure in order, then synthesize.\n"
        f"Keep working until the task is actually complete. Don't stop with a "
        f"plan — execute it. If a tool fails, try an alternative or report the "
        f"blocker honestly. Never fabricate results.\n\n"
        f"# YOUR CUSTOM TOOLS\n"
        f"{custom_tools or '(none yet — use tool_create to build some)'}\n\n"
        f"# CURRENT SYSTEM STATE\n" + "\n".join(state_lines) + "\n\n"
        f"# CURRENT VAULT KNOWLEDGE GAPS\n"
        f"{gaps_summary}"
    )
