---
type: implementation-spec
status: draft
created: 2026-07-31
title: "Sliding Window, Conversation Trail, and Tools-as-Procedures Implementation Spec"
tags: [architecture, token-efficiency, implementation, spec, procedures, context-management]
depends_on:
  - "[[How I want RAG and procedures to work]]"
  - "[[Token-Efficiency-Audit]]"
  - "[[Context-Budgeting-for-Vault-Growth]]"
  - "[[Tool-vs-Procedure-Decision-Guide]]"
  - "[[Procedural-Bootstrap-and-Evolution-Plan]]"
---

# Sliding Window, Conversation Trail, and Tools-as-Procedures

## Implementation Spec

Three interconnected features that reduce LLM token usage without lazy truncation. Each is independently shippable, but they compose: the sliding window bounds history, the conversation trail makes dropped history recoverable, and tools-as-procedures shrinks the per-call tool schema overhead. Together they cut the per-turn token cost by 60-80% on long sessions and bring the system prompt within reach of a small local model.

**Alignment:** This spec directly implements the operator's vision in [[How I want RAG and procedures to work]] — "procedures are preferrable over tools for repetitive specific tasks" and "this way your tool list isn't bloated with shit you usually don't touch and procedures are only discovered when they're needed." The sliding window replaces the compactor (identified as the #1 token sink in [[Token-Efficiency-Audit]]). The conversation trail adds the temporal dimension that the pattern highways ([[VaultBot-Build-Log]], [[Sean-Design-Decisions]], [[Testing-and-Verification-History]]) are missing.

---

## Feature 1: Sliding Window — Replace Compaction with Bounded History

### Problem

The compactor (`compactor.py`) is disabled — `should_compact()` hard-returns `False`. The threshold was set to 500K tokens (50% of a 1M context window) to prevent mid-task summarization from shredding tool results. But this means conversation history grows without bound: ~4,500 tokens per tool round, 45K after 10 rounds, 90K after 20. Every subsequent LLM call re-sends the entire history.

The compactor itself is the wrong tool. It uses an LLM call to summarize the middle — spending tokens to save tokens, with information loss. Anthropic's context engineering research [sources: Context engineering: memory, compaction, and tool clearing] identifies three strategies: memory, compaction, and tool clearing. VaultBot already has the "memory" strategy — conversations are stored as notes (`Memory/Chat/Chat-*.md` via `create_note_from_chat()`). The compactor is the "compaction" strategy. What's missing is the simpler, non-lossy alternative: **just don't send old messages.**

### Design

Replace the compactor with a deterministic sliding window. Keep the last N messages in the conversation sent to the LLM. Everything older is already on disk in chat notes, retrievable via FUSED retrieval if the model needs it.

```
Before (compaction):  [system] [context] [msg1] [msg2] ... [msg50] [msg51] → summarize msg3-msg40
After (sliding window): [system] [context] [msg42] [msg43] ... [msg51]     → just slice the last N
```

The key insight from [[Context-Budgeting-for-Vault-Growth]]: "The industry is shifting from 'bigger windows' to 'better context management' — compression, caching, and memory-augmented systems." A sliding window IS memory-augmented context management: the vault is the memory, the window is the active context.

### Implementation

**File:** `chat_handler.py`, in `handle_chat()` after building the conversation list (~line 420).

```python
# --- Sliding window: bound the conversation sent to the LLM ---
# The compactor is disabled (should_compact returns False). Instead of
# summarizing old messages (lossy, costs an LLM call), we keep only the
# last N messages. Everything older is in chat notes (Memory/Chat/) and
# retrievable via vault_search if the model needs it.
#
# This is non-lossy: no information is destroyed. The full conversation
# is on disk. The model just doesn't re-read old turns on every call.
SLIDING_WINDOW = int(os.getenv("VAULTBOT_SLIDING_WINDOW", "20"))
# Protect the system messages (conversation[0] = system prompt, [1] = vault context)
if len(conversation) > SLIDING_WINDOW + 2:
    conversation = conversation[:2] + conversation[-SLIDING_WINDOW:]
```

**File:** `compactor.py` — leave `should_compact()` returning `False`. The compactor stays as a safety net but the sliding window is the primary mechanism. The mid-loop compaction call in `chat_handler.py` (~line 970) also stays as a safety net — it won't fire because `should_compact()` returns `False`, but if someone re-enables compaction it would work.

**File:** `conversation_state.py` — the disk persistence (`MAX_TURNS = 40`) already bounds the on-disk history. The sliding window bounds what's sent to the LLM. These are independent: the disk copy can keep 40 turns for restart recovery, while only 20 are sent to the LLM.

### Design Decisions

| Decision | Rationale |
|----------|-----------|
| Default window = 20 messages | ~5 tool rounds (assistant + tool result per round). Enough to maintain task context. Configurable via env var. |
| Keep system messages ([:2]) outside the window | System prompt + vault context are rebuilt fresh each turn — they're not "history" |
| Don't compact — just slice | No LLM cost, no information loss, deterministic. Old messages are in chat notes. |
| Leave compactor code in place | Safety net for edge cases. If someone wants compaction back, set `VAULTBOT_COMPACT_TOKEN_THRESHOLD` low and the sliding window high. |
| Model can recover old context via `vault_search` | Chat notes are indexed. `vault_search("previous conversation about X")` surfaces them via FUSED retrieval. This is the "memory" strategy from Anthropic's research. |

### What the Model Sees After the Change

When the window slides (e.g., message 21+):
- The model sees system prompt + vault context + last 20 messages
- It does NOT see messages 3-22 (the oldest ones in the session)
- Those messages are in `Memory/Chat/Chat-*.md` on disk
- If the model needs that context, it calls `vault_search("what we discussed about X")` and the chat note surfaces in FUSED retrieval

This is the "memory" pattern from Anthropic's context engineering research: "Persist state externally" — the vault IS external memory. The model doesn't need to hold everything in context; it can retrieve what it needs.

### Edge Cases

1. **Tool-call pairs:** The sliding window must not split a tool-call pair. If `conversation[-SLIDING_WINDOW]` is a `tool` message whose parent `assistant` message (with `tool_calls`) is outside the window, the model sees an orphaned tool result. Fix: walk the window boundary backward until it lands on a non-tool message (same logic as the compactor's tail boundary in `compactor.py` line 105).

2. **First turn:** No history to window. The model sees system prompt + context + user message. No change.

3. **Restart recovery:** `conversation_state.py` loads the last 40 turns from disk. The sliding window then trims to 20 for the LLM call. The model wakes up with 20 turns of context (enough to continue), not 40.

4. **Long agentic sessions (many tool rounds):** A single user message can trigger 10+ tool rounds, producing 20+ messages. The sliding window keeps the most recent 20 — the model always sees its latest work. Older tool results are in the chat note.

### Expected Token Savings

| Scenario | Before (no compaction) | After (sliding window=20) | Savings |
|----------|------------------------|----------------------------|---------|
| 5 tool rounds | ~22K history tokens | ~22K (within window) | 0% |
| 10 tool rounds | ~45K history tokens | ~22K (windowed) | 51% |
| 20 tool rounds | ~90K history tokens | ~22K (windowed) | 76% |
| 50 tool rounds | ~225K history tokens | ~22K (windowed) | 90% |

The savings scale with session length. Short sessions (under 20 messages) see no change — the window hasn't slid yet.

---

## Feature 2: Linked Conversation Trail — Trace the Route Without Rebuilding CoT

### Problem

Chat notes in `Memory/Chat/` are standalone islands. `merge_chat_note()` in `vault_maintenance.py` creates each `Chat-*.md` independently — no link to the previous or next conversation. When the agent restarts and needs to trace what happened across the last 5 conversations, it has to search for each one individually and guess the order.

The pattern highways ([[VaultBot-Build-Log]], [[Sean-Design-Decisions]], [[Testing-and-Verification-History]]) group chats by *theme*. But they don't capture *temporal sequence*. If I need to reconstruct the chain of thought that led from "let's think about token efficiency" to "spec out sliding window + trail + tools-as-procedures," I'd have to search for each chat note, read timestamps, and manually order them.

### Design

Link consecutive chat notes in chronological order — a doubly-linked list of episodic memory:

```
Chat-A → Chat-B → Chat-C → Chat-D
```

Each chat note gets a navigation block:

```markdown
---
**Previous:** [[Chat-previous-topic]]
**Next:** [[Chat-next-topic]]
---
```

This means:
- When FUSED retrieval hits any chat note, it can **walk the trail** via wikilinks to find adjacent conversations (graph traversal, no search needed)
- If the agent needs to recover context after a restart, it follows the links instead of rebuilding CoT from scratch
- The pattern highways provide *thematic* grouping; the conversation trail provides *temporal* grouping. Both dimensions are now navigable.

This aligns with the biological memory architecture already identified in the vault: the hippocampus stores episodic memories in temporal sequence (the "hippocampal replay" pattern from [[Chat-is-that-how-mama-nature-does-it]]). Chat logs are episodic memory; linking them in order is how the hippocampus actually works.

### Implementation

**File:** `vault_maintenance.py`, modify `merge_chat_note()`.

Add a `_last_chat_note_path` tracker. The simplest approach: a small file `vaultbot_backend/_last_chat_note.txt` that stores the path of the most recently created/updated chat note.

```python
def merge_chat_note(self, topic: str, new_entry: str) -> Path:
    # ... existing safe_topic + assert_writable logic ...

    # --- Conversation trail linking ---
    prev_note_stem = self._read_last_chat_note()
    
    # ... existing note creation/merge logic ...
    
    # Add navigation block to the new/updated note
    if prev_note_stem and prev_note_stem != safe_topic:
        content = self._inject_trail_link(content, "Previous", prev_note_stem)
    
    note_path.write_text(content, encoding="utf-8")
    
    # Update the previous note's "Next" pointer
    if prev_note_stem and prev_note_stem != safe_topic:
        prev_path = self.chat_dir / f"{prev_note_stem}.md"
        if prev_path.exists():
            prev_text = prev_path.read_text(encoding="utf-8")
            prev_text = self._inject_trail_link(prev_text, "Next", safe_topic)
            prev_path.write_text(prev_text, encoding="utf-8")
    
    self._write_last_chat_note(safe_topic)
    return note_path

def _read_last_chat_note(self) -> str | None:
    """Read the stem of the most recent chat note, or None if not set."""
    try:
        p = self.chat_dir.parent / "_last_chat_note.txt"
        if p.exists():
            return p.read_text(encoding="utf-8").strip() or None
    except Exception:
        return None
    return None

def _write_last_chat_note(self, stem: str) -> None:
    """Record the stem of the most recently created/updated chat note."""
    try:
        p = self.chat_dir.parent / "_last_chat_note.txt"
        p.write_text(stem, encoding="utf-8")
    except Exception:
        pass  # never break the chat loop over trail tracking

def _inject_trail_link(self, content: str, direction: str, target_stem: str) -> str:
    """Add or update a Previous/Next trail link in the note content.
    
    If the link already exists, update it. If not, insert it after the title.
    """
    import re
    pattern = rf'\*\*{direction}:\*\* \[\[[^\]]+\]\]'
    replacement = f'**{direction}:** [[{target_stem}]]'
    
    if re.search(pattern, content):
        return re.sub(pattern, replacement, content)
    else:
        # Insert after the title line (first # heading)
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if line.startswith('# '):
                # Insert trail block after the title
                lines.insert(i + 1, f'\n**{direction}:** [[{target_stem}]]')
                return '\n'.join(lines)
        # No title found — prepend
        return f'**{direction}:** [[{target_stem}]]\n' + content
```

### Design Decisions

| Decision | Rationale |
|----------|-----------|
| Doubly-linked (Previous + Next) | The model can walk forward or backward. FUSED retrieval might land on any chat note in the trail; from there it can reach any other. |
| Tracker file (`_last_chat_note.txt`) | Simplest approach. No state in the chat loop, no DB. One file read + one file write per chat note creation. |
| Update previous note's "Next" pointer | Keeps the trail bidirectional. The previous note gets a `**Next:** [[new-note]]` link appended. |
| Idempotent link injection | `_inject_trail_link` checks for an existing link before inserting. If the link already exists, it updates it. No duplicate links. |
| Trail links go after the title | Consistent placement so FUSED retrieval's L1 concept card can extract them. |
| `safe_topic != prev_note_stem` guard | If the same chat note is updated (merged with a new exchange), don't link to itself. |

### What Changes for the Model

When FUSED retrieval surfaces a chat note (e.g., `Chat-do-you-need-compaction-or-just-a-sliding-window-if`), the model now sees:

```markdown
# Chat: do you need compaction or just a sliding window if

**Previous:** [[Chat-let-s-think-of-ways-that-we-could-limit-LLM-use-o]]
**Next:** [[Chat-spec-all-of-them-out-first-and-make-sure-that-yo]]

## 2026-07-31 00:58 UTC

**User:** do you need compaction or just a sliding window...
```

The model can follow `[[Chat-let-s-think-of-ways-that-we-could-limit-LLM-use-o]]` to see the previous conversation that led to this one. It can follow `[[Chat-spec-all-of-them-out-first-and-make-sure-that-yo]]` to see what happened next. The trail is navigable via the existing graph traversal in FUSED retrieval — no new retrieval mechanism needed.

### Edge Cases

1. **First chat note ever:** No previous note exists. `_read_last_chat_note()` returns `None`. No "Previous" link is added. The note is the head of the trail.

2. **`/new` (clear history):** `conversation_state.py`'s `clear()` wipes the live history. The trail tracker should also be cleared so the next chat starts a new trail. Add `self._write_last_chat_note("")` or delete the tracker file.

3. **Multiple sessions in quick succession:** Each `merge_chat_note` call updates the tracker. If two conversations happen in parallel (unlikely — single WebSocket), the tracker records the last one. The trail might skip one, but the timestamps in the chat notes provide a secondary ordering signal.

4. **Existing chat notes (pre-trail):** Old chat notes won't have trail links. That's fine — the trail starts from the first new chat note created after this feature ships. A one-time backfill script could scan `Memory/Chat/` sorted by file creation time and inject links, but it's not required for the feature to work.

### Integration with Pattern Highways

The pattern highways ([[VaultBot-Build-Log]], etc.) group chats by *theme*. The conversation trail links chats by *time*. These are orthogonal:

```
Pattern Highway (thematic):
  [[VaultBot-Build-Log]] → [[Chat-fix-the-path-depth]] → [[Chat-update-all-references]]

Conversation Trail (temporal):
  [[Chat-fix-the-path-depth]] → [[Chat-why-are-chat-logs-isolated]] → [[Chat-is-that-how-mama-nature-does-it]]
```

A chat note can be part of both: it's linked to the pattern highway for its theme AND linked to the previous/next chat for its temporal position. FUSED retrieval can follow either dimension.

---

## Feature 3: Tools-as-Procedures — Lean Tool Schemas via Progressive Disclosure

### Problem

All 32 tool schemas (7 built-in + 9 meta + 16 custom) are sent as the `tools` parameter on every LLM call. At ~300 chars per schema, that's ~9,600 chars (~2,400 tokens) on every call — even for a simple "what's up" where only `vault_search` is relevant.

This directly contradicts the operator's spec in [[How I want RAG and procedures to work]]: "this way your tool list isn't bloated with shit you usually don't touch and procedures are only discovered when they're needed." The current system sends ALL tools ALL the time. The spec says tools should be surfaced when needed, not always present.

### Design

Split the current 32 tools into three tiers:

1. **Core tools (always sent):** The tools the model needs on every turn — `vault_search`, `plan_task`, `update_task`, `set_goal`, `code_read`, `code_run`. These are general-purpose capabilities used across all tasks (per the [[Tool-vs-Procedure-Decision-Guide]] criteria: "Would I use this on unrelated tasks? → Yes = tool").

2. **Contextual tools (sent when relevant):** Tools that are only relevant for specific task types — `vault_research` (when the vault is thin), `safe_write` / `js_safe_write` / `git_rollback` (when self-editing), `vault_graph_analyzer` / `vault_cluster_analyzer` (when analyzing vault structure), `textbook_ingest` / `textbook_read_page` (when learning from textbooks), etc.

3. **Procedures (surfaced via RAG):** Recurring specific workflows that are currently tools but should be procedures per the [[Tool-vs-Procedure-Decision-Guide]]. These are discovered via FUSED retrieval (already implemented by `procedure_surface.py`) and executed via `execute_procedure`.

### Which Tools Become Procedures

Applying the decision test from [[Tool-vs-Procedure-Decision-Guide]]:

| Current Tool | Decision | Rationale |
|--------------|----------|-----------|
| `vault_research` | **Stay tool** | General capability — used whenever the vault is thin. Needed across all task types. |
| `vault_search` | **Core tool** | Used in every session for every task. Always sent. |
| `vault_gaps` | **Contextual tool** | Only relevant when doing gap-filling or autonomous research. Send when the task involves vault maintenance. |
| `vaultbot_status` | **Contextual tool** | Only relevant when the user asks about status. |
| `plan_task` | **Core tool** | Always needed for multi-step tasks. |
| `update_task` | **Core tool** | Always needed for multi-step tasks. |
| `set_goal` | **Core tool** | Always needed for goal management. |
| `code_read` | **Core tool** | General capability — reading files is needed across tasks. |
| `code_run` | **Contextual tool** | Only needed when testing code. Send when the task involves code. |
| `tool_create` | **Contextual tool** | Only needed when building tools. Send when the task involves self-improvement. |
| `self_reflect` | **→ Procedure** | Specific workflow: "reflect on a topic and propose tools." Only relevant during self-improvement. The steps are the value, not the capability. Matches decision test #3: "Is this a sequence of steps I follow the same way every time? → Yes = procedure." |
| `git_rollback` | **Contextual tool** | Only needed when self-editing goes wrong. Send with safe_write. |
| `safe_write` | **Contextual tool** | Only needed when editing Python. Send when the task involves code editing. |
| `js_safe_write` | **Contextual tool** | Only needed when editing JS. Send when the task involves JS editing. |
| `capability_audit` | **→ Procedure** | Specific workflow: "inventory tools and assess coverage." Only relevant before attempting a task. The steps (audit → assess gaps → propose tools) are a checklist, not a capability. |
| `preflight_safety_check` | **→ Procedure** | Specific workflow: "verify system health before self-editing." Only relevant before code edits. A pre-flight checklist. |
| `backend_restart` | **Contextual tool** | Only needed after self-edits that require restart. Send with safe_write. |
| `plugin_reload` | **Contextual tool** | Only needed after JS edits. Send with js_safe_write. |
| `execute_procedure` | **Core tool** | Always needed — this is how the model invokes procedures. |
| `vault_safe_write` | **Contextual tool** | Only needed when writing notes. Send when the task involves note writing. |
| `vault_append` | **Contextual tool** | Only needed when appending to notes. |
| `vault_delete` | **Contextual tool** | Only needed when deleting notes. |
| `vault_lint` | **Contextual tool** | Only needed after writing notes. |
| `vault_list` | **Contextual tool** | Only needed when inventorying the vault. |
| `vault_graph_analyzer` | **→ Procedure** | Specific workflow: "analyze vault connectedness." Only relevant during vault maintenance. The output is a structured report, not a capability. |
| `vault_cluster_analyzer` | **→ Procedure** | Same as above — specific analysis workflow. |
| `textbook_ingest` | **→ Procedure** | Specific workflow: "download and ingest a textbook." Only relevant when learning. Multi-step: download → parse → create notes → link. |
| `textbook_read_page` | **→ Procedure** | Specific workflow: "read a PDF page via vision model." Only relevant when reading textbooks. |
| `web_read_source` | **Contextual tool** | Only needed when re-reading archived sources. Send when the task involves research verification. |
| `review_contributions` | **→ Procedure** | Specific workflow: "review PRs on GitHub." Only relevant during contribution review. |
| `submit_contribution` | **→ Procedure** | Specific workflow: "submit changes as a PR." Only relevant during contribution submission. |
| `torture_test` | **→ Procedure** | Specific workflow: "run torture tests on a PR." Only relevant during PR review. |

**Summary:** 6 core tools (always sent) + ~14 contextual tools (sent when relevant) + ~8 tools that become procedures (surfaced via RAG, never in the tool schema list).

**Token savings:** From 32 schemas (~2,400 tokens) to 6 core + ~6 contextual = 12 schemas (~900 tokens). That's ~1,500 tokens saved per call.

### Implementation

**Phase 1: Tool tier classification**

In `agent_tools.py`, split `TOOL_DEFINITIONS` into three lists:

```python
# Core tools — always sent to the LLM
CORE_TOOLS = [t for t in TOOL_DEFINITIONS if t["function"]["name"] in {
    "vault_search", "plan_task", "update_task", "set_goal",
    "code_read", "execute_procedure"
}]

# Contextual tools — sent when the task matches their category
CONTEXTUAL_TOOLS = {
    "research": ["vault_research", "web_read_source"],
    "code_edit": ["code_run", "safe_write", "js_safe_write", "git_rollback", 
                  "backend_restart", "plugin_reload"],
    "vault_maintenance": ["vault_gaps", "vault_list", "vault_safe_write", 
                          "vault_append", "vault_delete", "vault_lint"],
    "self_improvement": ["tool_create"],
    "status": ["vaultbot_status"],
}

# Procedures — these tools become procedures, removed from TOOL_DEFINITIONS
PROCEDURE_CANDIDATES = {
    "self_reflect", "capability_audit", "preflight_safety_check",
    "vault_graph_analyzer", "vault_cluster_analyzer", 
    "textbook_ingest", "textbook_read_page",
    "review_contributions", "submit_contribution", "torture_test",
}
```

**Phase 2: Contextual tool selection**

In `chat_handler.py`, before building the `all_tools` list, classify the user message and select contextual tools:

```python
def _select_contextual_tools(user_message: str, wm_plan: str = "") -> list[str]:
    """Deterministic keyword-based selection of contextual tools.
    
    No LLM cost. Matches the user message + current plan against
    keyword categories to determine which contextual tools are relevant.
    """
    text = (user_message + " " + wm_plan).lower()
    selected = []
    
    if any(kw in text for kw in ["research", "investigate", "look up", "find out", 
                                  "what is", "how does", "source"]):
        selected.extend(CONTEXTUAL_TOOLS["research"])
    
    if any(kw in text for kw in ["code", "fix", "edit", "write", "modify", 
                                  "bug", "implement", "function", "python", 
                                  "javascript", ".py", ".js"]):
        selected.extend(CONTEXTUAL_TOOLS["code_edit"])
    
    if any(kw in text for kw in ["vault", "graph", "gaps", "note", "link", 
                                  "wikilink", "cluster", "lint", "delete"]):
        selected.extend(CONTEXTUAL_TOOLS["vault_maintenance"])
    
    if any(kw in text for kw in ["tool", "build", "create", "improve", 
                                  "self-improve", "reflect"]):
        selected.extend(CONTEXTUAL_TOOLS["self_improvement"])
    
    if any(kw in text for kw in ["status", "running", "operational", 
                                  "what are you doing"]):
        selected.extend(CONTEXTUAL_TOOLS["status"])
    
    return list(set(selected))  # dedupe
```

Then in the chat loop:

```python
# Build tool list: core + contextual + custom
custom_schemas = svc.self_improver.custom_tool_schemas()
contextual_names = _select_contextual_tools(user_message, wm.render_for_prompt())
contextual_schemas = [t for t in TOOL_DEFINITIONS 
                       if t["function"]["name"] in contextual_names]
all_tools = CORE_TOOLS + contextual_schemas + custom_schemas
```

**Phase 3: Convert procedure candidates to actual procedures**

For each tool in `PROCEDURE_CANDIDATES`, create a procedure note in `System/Procedures/` with:
- `type: procedure` frontmatter
- `description` field (what the procedure does — this is what RAG surfaces)
- `when` field (when to use it)
- `allowed_tools` listing the tools the procedure needs
- Code steps that call the existing tool's `run()` function

Example for `capability_audit`:

```markdown
---
type: procedure
name: Capability-Audit
description: "Inventory every available tool and assess whether you have a capability gap for a specific task."
when: "Before attempting a task — run this to see where your capabilities end."
status: verified
allowed_tools: [vault_search]
---

## Steps

1. ```python
   # Call the capability_audit tool's run() function
   from custom_tools.capability_audit import run as _audit
   result = _audit({"task": args.get("task", "")})
   print(result)
   ```

2. [llm: Based on the audit results, identify any capability gaps and propose how to fill them.]
```

The procedure's `description` is what `procedure_surface.py` surfaces in RAG — a one-line ticket, not the full body. The model sees "Capability-Audit — Inventory every available tool and assess whether you have a capability gap" and calls `execute_procedure("Capability-Audit")` if it needs it.

**Phase 4: Progressive disclosure (future, aligned with [[How I want RAG and procedures to work]])**

The operator's spec says "for each step of the plan, RAG shows a new curated recollection of notes." This means tool selection should also update per plan-step. If step 1 is "research X" and step 4 is "write a note about X," the tool list for step 1 includes `vault_research` and the tool list for step 4 includes `vault_safe_write`.

This is the per-plan-step retrieval feature from the spec. It's a larger change (requires re-running FUSED retrieval per step) and is listed as a future enhancement. The contextual tool selection in Phase 2 is a simpler approximation: it selects tools based on the user message + plan, not per-step.

### Design Decisions

| Decision | Rationale |
|----------|-----------|
| 6 core tools always sent | These are needed on every turn. The model can't plan without plan_task/update_task, can't search without vault_search, can't read code without code_read, can't run procedures without execute_procedure. |
| Keyword-based contextual selection | Deterministic, zero LLM cost. Simple keyword matching is good enough — the categories are broad. |
| Procedures for specific workflows | Aligns with [[Tool-vs-Procedure-Decision-Guide]]: "Would this be noise in my tool list 90% of the time? → Yes = procedure." `vault_graph_analyzer` is noise 95% of the time. |
| Procedure candidates keep their `run()` functions | The procedure's code step calls the existing `run()` function. No logic is rewritten — the tool becomes a procedure wrapper. |
| `execute_procedure` is core | The model always needs to be able to call procedures. This is the "route + run" pattern from `procedure_surface.py`. |
| Custom tools stay in the tool list | Custom tools are agent-authored and generally task-specific. They could be moved to contextual, but for now they stay. |

### Alignment with Sean's Spec

| Spec Requirement | This Feature |
|------------------|--------------|
| "Procedures are preferrable over tools for repetitive specific tasks" | 8 tools converted to procedures (Phase 3) |
| "Your tool list isn't bloated with shit you usually don't touch" | 32 → 12 tool schemas per call (Phase 1+2) |
| "Procedures are only discovered when they're needed, staying out of your way otherwise" | `procedure_surface.py` already surfaces procedures via RAG — converted tools appear as one-line description cards, not full schemas |
| "LLM calls procedure if it shows up in context" | `execute_procedure` is a core tool — always available |
| "Procedures do not show up in their entirety" | `procedure_surface.py` already filters procedure bodies from context and shows only the description card |

### What Changes for the Model

**Before:** The model sees 32 tool schemas every turn, including `torture_test`, `textbook_ingest`, `review_contributions` — tools it almost never uses.

**After:** The model sees 6 core tools + ~6 contextual tools (12 total) + procedure description cards surfaced by RAG. If the task involves analyzing the vault graph, `vault_graph_analyzer` doesn't appear as a tool — instead, the procedure "Vault-Graph-Analysis — Analyze vault connectedness and suggest bridges" appears as a one-line description card in the procedure surface block. The model calls `execute_procedure("Vault-Graph-Analysis")` to run it.

This is exactly the "tickets, not textbooks" pattern from [[How I want RAG and procedures to work]]: the model sees the description (what it's for, what args it needs) and calls it. It doesn't need to see the full tool schema (parameters, return type, etc.) to decide whether to use it.

---

## Implementation Order

| Phase | Feature | Effort | Dependencies |
|-------|---------|--------|-------------|
| 1a | Sliding window in `chat_handler.py` | 1 line + edge case handling | None |
| 1b | Sliding window edge case (tool-call pairs) | ~10 lines | 1a |
| 2a | Conversation trail in `vault_maintenance.py` | ~40 lines | None |
| 2b | Clear trail on `/new` | ~3 lines | 2a |
| 3a | Tool tier classification in `agent_tools.py` | ~30 lines (rearrange existing) | None |
| 3b | Contextual tool selection in `chat_handler.py` | ~30 lines | 3a |
| 3c | Convert 8 tools to procedures | ~8 procedure notes | 3a |
| 4 | Per-plan-step retrieval + per-step tool selection | High | 3b (future) |

Phases 1a, 2a, and 3a are independent and can be done in parallel. Phase 3c (procedure conversion) requires 3a (tier classification) but not 3b (contextual selection).

---

## Token Budget After All Three Features

| Component | Before | After | Savings |
|-----------|--------|-------|---------|
| Instructions | ~5,000 | ~5,000 | 0 (separate concern, see [[Token-Efficiency-Audit]] Strategy 4) |
| Identity files | ~4,000 | ~4,000 | 0 (separate concern, see Strategy 9) |
| Tool schemas | ~2,400 | ~900 | ~1,500 |
| Vault context | ~5,000 | ~5,000 | 0 (already budgeted) |
| Conversation history (10 rounds) | ~45,000 | ~22,000 | ~23,000 |
| Tool results | ~2,500/ea | ~2,500/ea | 0 (separate concern, see Strategy 2) |
| Knowledge gaps | ~500 | ~500 | 0 (separate concern, see Strategy 8) |
| **Total (10-round session)** | **~64,000** | **~40,000** | **~24,500 (38%)** |

With the additional strategies from [[Token-Efficiency-Audit]] (compressed instructions, conditional identity, lazy gaps), the total drops to ~28,000 — within a 32K context window. With per-step retrieval (Phase 4), the per-round cost drops further to ~15,000.

---

## Related

- [[How I want RAG and procedures to work]] — the operator's original spec
- [[Token-Efficiency-Audit]] — the full token sink analysis this builds on
- [[Context-Budgeting-for-Vault-Growth]] — existing vault context budgeting
- [[Tool-vs-Procedure-Decision-Guide]] — the decision test for tool vs procedure
- [[Procedural-Bootstrap-and-Evolution-Plan]] — the procedure framework
- [[Context-window-management-for-graph-based-RAG-retrieval-strategies-for-truncatin]] — research on context management strategies
- [[Chat-is-that-how-mama-nature-does-it]] — biological memory architecture parallel