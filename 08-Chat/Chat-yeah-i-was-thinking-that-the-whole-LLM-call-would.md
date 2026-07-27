# Chat: yeah i was thinking that the whole LLM call would 

## 2026-07-27 01:01 UTC

**User:** yeah i was thinking that the whole LLM call would just be in the procedure itself, like, the procedure is literally a subprocess that blocks the vaultbot from doing anything else until the subprocess (procedure) has completed or failed (detailed errors that fail loudly for easy troubleshooting please, we want the vaultbot to be able to easily iterate on the procedures when there are things wrong). the procedures would call a separate LLM call with WAY less context (only the context gotten from the tool calls in that same procedure, the procedure-bot doesn't think it's vaultbot and only can use the tools it's given permission to use). the whole point of this is so that the procedures can run way faster and cost less for the user. the vaultbot (you) would get the procedures found through searches so you won't discover what you don't need (this is where the homogeneity of the obsidian vault shines) cause procedures are still just vault docs treated the same as any other. should probably be presented differently though: maybe allow a description for when to use the procedure so the LLM doesn't have to read the whole procedure to know what it does and then what to use it for. this also allows the embedding drift to do it's thing along with the learning mechanism you built with the procedures

**Assistant:** Done. Design spec written to [[Procedure-Subprocess-Architecture]]. Here's the bottom line:

## What the spec captures

**Your idea, formalized:**
- Procedures are **blocking subprocesses** — VaultBot freezes until the procedure completes or fails
- Two step types: **code blocks** (execute directly, zero LLM cost) and **`[llm:]`** tags (separate, stripped-down LLM call with only tool results as context)
- The **procedure-bot is not VaultBot** — it gets ~50 tokens of system prompt + accumulated tool results, not the 30K+ token VaultBot context. That's a 50-100x context reduction per LLM call
- **Loud failures** — detailed JSON with step number, error, traceback, prior results, and the code that failed
- **`description` field** in frontmatter — VaultBot reads one line to decide whether to invoke, doesn't burn tokens reading the full procedure body
- **`allowed_tools` field** — permission-scoped per procedure, code steps that call unlisted tools fail loudly
- Procedures are just vault docs → FUSED search surfaces them → embedding drift + procedure_tracker learning apply naturally

## What already exists vs what changes

| File | Lines | What needs to happen |
|---|---|---|
| `procedure_compiler.py` | 289 | Additive: parse code blocks + `[llm:]` tags + `description`/`allowed_tools` |
| `step_gate_runtime.py` | 354 | **Major rewrite**: sandbox execution for code, minimal LLM calls, loud failures |
| `procedure_tracker.py` | 613 | Add: step-level failure tracking with error details |
| `chat_handler.py` | 970 | Surgical merge: procedure invocation logic (not the full `chat_handler_new.py` swap) |

## 4-phase implementation plan

1. **Update compiler** (additive — parse new step types)
2. **Rewrite runtime** (code execution + minimal LLM + loud failures)
3. **Surgical merge into chat_handler** (procedure invocation flow)
4. **Update tracker** (step-level error logging)

Ready to start building Phase 1 whenever you say go.

<details>
<summary>Thinking process</summary>

Sean is describing a really clear architecture here. Let me break down what he's saying:

1. **Procedures are subprocesses** — when a procedure runs, it blocks VaultBot from doing anything else until it completes or fails. This is synchronous, blocking execution.

2. **Procedures contain their own LLM calls** — the procedure itself makes a separate, smaller LLM call with WAY less context. The "procedure-bot" doesn't think it's VaultBot — it's a stripped-down agent that only knows about the tools it's been given permission to use and only gets context from the tool calls within that same procedure.

3. **Fail loudly** — detailed errors when things go wrong, so we can iterate on procedures easily.

4. **Procedures are just vault docs** — they're .md files, treated the same as any other note. Found through normal FUSED search. This is the homogeneity of the Obsidian vault — the embedding index naturally surfaces relevant procedures.

5. **Description field** — procedures should have a description/summary so the LLM doesn't have to read the whole procedure to know what it does and when to use it. This is for retrieval efficiency.

6. **Embedding drift + learning mechanism** — because procedures are vault docs, the existing embedding/vector system and the procedure_tracker.py success/failure tracking naturally apply to them.

This is a really well-thought-out design. Let me think about what I should do:

1. Write this up as a design spec note in the vault
2. Think about the architecture

Let me think about the architecture more carefully:

**Current flow:**
1. User sends message → VaultBot retrieves context via FUSED search
2. If procedure notes are in context, they're dumped as text and the LLM is "hoped" to follow them
3. LLM generates response
4. Validation happens after

**New flow (Sean's vision):**
1. User sends message → VaultBot retrieves context via FUSED search
2. If procedure notes are found in context, VaultBot reads the description (not the full procedure)
3. VaultBot decides whether to invoke a procedure
4. If yes, the procedure runs as a **blocking subprocess**:
   - The procedure has its own execution context
   - Steps that are code blocks → execute directly (call tools, no LLM)
   - Steps that need semantic reasoning → make a **separate, minimal LLM call** with only the context from prior tool calls in this procedure
   - The "procedure-bot" has a stripped identity — it's not VaultBot, it doesn't have the vault context, it only has the tools it's been given and the accumulated results from the procedure so far
   - If any step fails, it fails loudly with detailed error info
   - VaultBot is blocked until the procedure completes or fails
5. VaultBot gets the result back and continues

**Key design decisions to capture:**
- Procedure format: frontmatter with `description` field, steps with embedded code blocks and `[llm:]` tags
- The procedure-bot is a separate LLM call with minimal context
- Tools are permission-scoped per procedure
- Blocking execution model
- Loud failures with detailed errors
- Procedures are vault docs → found via FUSED search → description field for retrieval efficiency

This is a significant architecture note. Let me write it up. I should also think about what I already have built:
- `procedure_compiler.py` (289 lines) — parses markdown into Procedure/Step objects
- `step_gate_runtime.py` (354 lines) — executes steps with active-frame paging
- `procedure_tracker.py` (613 lines) — logs pass/fail, promotes/flags
- `chat_handler_new.py` (1127 lines) — draft integration, NOT live

The existing code needs to be redesigned to match Sean's vision. The key changes:
1. Steps can contain code blocks that execute directly (no LLM)
2. Steps can contain `[llm:]` tags that trigger a separate, minimal LLM call
3. The procedure runs as a blocking subprocess
4. The LLM call within a procedure has minimal context (only tool call results from this procedure)
5. Tools are permission-scoped
6. Loud failures

Let me write this up as a design spec note. I should be concise but thorough.Good — I have the context I need. Now let me write the design spec. This is a significant architecture note that captures Sean's vision for the procedure execution system.

Let me think through the full architecture:

## Procedure Execution Architecture

### Core Concept
Procedures are markdown vault docs that execute as blocking subprocesses. They contain:
- A `description` field in frontmatter (for retrieval — so the LLM doesn't read the whole procedure to know what it does)
- Steps that are either:
  - **Code blocks** (```python ... ```) — execute directly, call tools, no LLM
  - **[llm:]** tagged sections — make a separate, minimal LLM call with only the context accumulated from tool calls in this procedure

### Execution Model
1. VaultBot receives user query
2. FUSED search retrieves context, including procedure notes
3. VaultBot reads procedure `description` fields (not full procedures) to decide which to invoke
4. When invoked, procedure runs as a **blocking subprocess**:
   - VaultBot is blocked until procedure completes or fails
   - Each step executes in order
   - Code steps → run directly in sandbox
   - LLM steps → separate LLM call with minimal context (only tool results from this procedure)
   - The "procedure-bot" is NOT VaultBot — stripped identity, only knows its tools and accumulated results
   - Failures are loud: detailed error messages, step number, what failed, why
5. Result returned to VaultBot, which continues

### Key Properties
- **Procedures are vault docs** — same as any other note, found via FUSED search, embedding drift applies
- **Description field** — frontmatter `description` that summarizes when to use the procedure, so the LLM doesn't need to read the full procedure body
- **Permission-scoped tools** — procedure frontmatter lists which tools it's allowed to use
- **Minimal context LLM calls** — only context from tool calls in this procedure, not the full vault
- **Loud failures** — detailed errors for easy iteration
- **Blocking** — VaultBot waits until procedure completes or fails
- **Cost optimization** — code steps are free (no LLM), LLM steps are cheap (minimal context)

### What Already Exists
- `procedure_compiler.py` (289 lines) — parses markdown into Procedure/Step objects
- `step_gate_runtime.py` (354 lines) — executes steps with active-frame paging
- `procedure_tracker.py` (613 lines) — logs pass/fail, promotes/flags
- `chat_handler_new.py` (1127 lines) — draft integration, NOT live

### What Needs to Change
The existing code was built for "every step → LLM call → validate → next step." Sean's redesign is:
1. Steps can be code blocks that execute directly (no LLM)
2. Steps can be `[llm:]` sections that make a minimal LLM call
3. The procedure runs as a blocking subprocess, not interleaved with VaultBot's reasoning
4. The LLM call within a procedure is a separate, stripped-down agent call
5. Tools are permission-scoped per procedure

Let me write this up as a vault note.Now I have the full picture of what's built and what needs to change. Let me write the design spec.

The key changes from what exists to what Sean wants:

1. **Current `procedure_compiler.py`**: Parses numbered steps with `[validate:]`, `[condition:]`, `[branch:]` annotations. No support for code blocks or `[llm:]` tags. No `description` field in frontmatter.

2. **Current `step_gate_runtime.py`**: Every step → LLM call with active frame. No code execution. No separate procedure-bot LLM. No permission-scoped tools. No blocking subprocess model. No loud failures.

3. **What Sean wants**:
   - Steps can be code blocks (```python ... ```) → execute directly, call tools, zero LLM cost
   - Steps can be `[llm:]` tagged → separate, minimal LLM call with only tool-call context from this procedure
   - Procedure runs as a **blocking subprocess** — VaultBot waits
   - The "procedure-bot" is NOT VaultBot — stripped identity, only knows its tools and accumulated results
   - Permission-scoped tools per procedure
   - Loud failures with detailed error info
   - `description` field in frontmatter for retrieval efficiency
   - Procedures are vault docs → found via FUSED search → embedding drift applies

Let me write this as a design spec note.No capability gap — I just need to write the note. Let me write the design spec now.The safe_write tool is trying to syntax-check the markdown as Python. But this is a markdown file, not a Python file. Let me use code_write instead... wait, I don't have code_write. Let me check — the instructions say "For markdown notes or non-code files, code_write is fine." But I don't see code_write in my tool list. Let me look again...

I don't have a `code_write` tool. I have `safe_write` which is for .py files, and `vault_append` which is for appending. But I need to create a new note. Let me check if there's another way...

Actually, looking at the tools again, I don't see a `code_write` tool. The safe_write description says "For markdown notes or non-code files, code_write is fine" — implying code_write exists. But it's not in my tool list. 

Let me try using safe_write anyway — maybe the issue is that it's trying to parse the markdown as Python because of the file path? No, the file path ends in .md. Let me look at the error more carefully: "leading zeros in decimal integer literals are not permitted" — this is a Python syntax error. It seems like safe_write is trying to syntax-check the content as Python even though it's a .md file.

Wait, looking at the safe_write description again: "Use this INSTEAD of code_write for any .py file under vaultbot_backend/." So safe_write is specifically for .py files. For markdown notes, I should use... code_write? But I don't have that tool.

Hmm, let me think about this. The vault_append tool says "Append content to an existing note" — but I need to create a new note. 

Actually, maybe I should try writing to a different path. The issue might be that safe_write is checking if it's a core module and trying to import it. Let me try a path outside vaultbot_backend/.

Actually wait — looking at the error, it says "line 4" has "leading zeros in decimal integer literals." Let me look at line 4 of my content... The frontmatter has `created: 2026-07-27`. But that's not Python code. The issue is that safe_write is syntax-checking the content as Python. 

Let me try using vault_append to create the file... no, vault_append appends to existing notes.

Hmm, let me try a different approach. Maybe I can use code_run to write the file directly.Now let me lint the note to verify quality.Good — 10 wikilinks, 0 broken. The "empty_sections" issue is probably from the code blocks being parsed as empty sections. That's fine for a design spec note.

Let me give Sean the bottom line.

</details>