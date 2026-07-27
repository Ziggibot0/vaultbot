# Chat: plan out the implementation

## 2026-07-26 02:51 UTC

**User:** plan out the implementation

**Assistant:** Done. The plan is at **[[Implementation-Plan-Architecture-Modules]]** — 16KB, 18 wikilinks, 0 broken.

## Bottom Line

**5 modules, 5 phases, ~780 lines of new code, ~60 lines of integration. All additive — zero existing code rewritten.**

| Phase | Module | LLM? | Deterministic? | Risk | Lines |
|---|---|---|---|---|---|
| 1 | Context Budgeter | No | Yes | Low | ~150 |
| 2 | Calibration Tracker | No | Yes | Low | ~200 |
| 3 | RAG Evaluator | Optional | Metrics: yes | Low-mod | ~180 |
| 4 | Claim Verifier | Yes | No | Moderate | ~250 |
| 5 | Exemplar Notes | No | N/A | None | 0 (notes) |

## Build Order Rationale

- **Phases 1 & 2 first** — pure deterministic, no LLM, no dependencies. Can be built in parallel.
- **Phase 3 next** — benefits from calibration data (Phase 2) for ground truth
- **Phase 4 after** — needs LLM (costliest), benefits from calibration + retrieval metrics
- **Phase 5 last** — just notes, but should pass all quality gates from prior phases

## Key Design Decisions

- **Context budgeter** wraps FUSED output *before* context building — no changes to the retriever itself
- **Calibration** detects corrections at the start of `handle_chat()` by pattern-matching Sean's messages
- **Claim verifier** is advisory, not blocking — flags issues but never prevents note creation
- **RAG evaluator** logs every retrieval (always), computes metrics only when ground truth exists
- Every module has env vars to enable/disable without code changes

Want me to start building Phase 1?

<details>
<summary>Thinking process</summary>

Sean wants me to plan out the implementation of the architecture notes I've designed. Let me think about what needs to be implemented:

1. **Claim Verification** (`claim_verifier.py`) — extract claims from notes, check entailment against sources
2. **RAG Evaluation** (`rag_eval.py`) — measure retrieval quality (recall@k, precision@k, NDCG)
3. **Context Budgeting** (`context_budgeter.py`) — rank and truncate retrieved notes to fit token budget
4. **Calibration** (`calibration.py`) — use Sean's corrections to calibrate quality gates
5. **Exemplar Notes** — not code, just notes with specific design patterns

Let me first check what's already in the vault about implementation plans, and then check my current capabilities to see what I can actually build.

Let me also look at the existing code to understand the architecture before planning.

I should:
1. Check capability_audit for "implement architecture notes as Python modules"
2. Read the existing source code to understand insertion points
3. Write a comprehensive implementation plan note in the vault

Let me start by auditing capabilities and reading key source files.Good, I have the top-level structure. Let me read more of main.py to understand the FUSED retrieval integration and the tool execution flow, and also look at the existing architecture notes more carefully to understand what each module needs to do.

Let me read more of main.py to find the key integration points - specifically where FUSED retrieval happens, where tool calls are handled, and where validation occurs.Good, I'm getting a clear picture of the codebase. Let me read more of main.py to find the key integration points - specifically where FUSED retrieval results are used, where tool calls are handled, and where the procedure tracker is integrated.Now let me find where the chat loop handles tool calls and where the procedure tracker is integrated. I need to find the key integration points in main.py.Let me find the chat endpoint and where tool calls are handled, and where the procedure tracker integrates.Let me continue reading to find where the procedure tracker is integrated and where tool calls are executed.Now I have a very clear picture of the codebase. Let me continue reading to find the rest of the chat handler and the tool execution function.Now I have a very thorough understanding of the codebase. Let me also look at the research engine to understand how notes are created, and check the existing architecture notes to understand what each module needs to do.

Let me now look at the key integration points I've found:

1. **FUSED retrieval** happens at line ~1180: `fused_retriever.retrieve(user_message, 5, 1)` → returns results with `file_path`, `name`, `score`, `channels`, `snippet`
2. **Procedure context tracking** at line ~1200: `parse_procedures_from_results(results)` → finds procedural notes in context
3. **Tool execution** at line ~1450: `_execute_agent_tool(tool_name, tool_args, session_logger, websocket)` → handles vault_research, vault_search, etc.
4. **Procedure tracking logging** at line ~1460: after vault_lint/safe_write/code_run, logs validation results
5. **Abstract context building** at line ~1230: `build_abstract_context(vault_graph, results, user_message, 5, 2, None)` → builds the context string
6. **Identity boot** at line ~1240: `identity.boot_context()` → injects IDENTITY/SELF_MODEL/GOALS
7. **System prompt** at line ~1270: `build_system_prompt(context, autonomous_state, gaps_summary, ...)`
8. **Embedding drift feedback** at line ~1500: after answer, nudges embeddings based on whether research was needed
9. **Lazy condenser** at line ~1550: defluffs notes after they're queried 3+ times

Now I need to plan the implementation of 5 modules:

1. **claim_verifier.py** — Post-synthesis claim verification
2. **rag_eval.py** — RAG evaluation metrics
3. **context_budgeter.py** — Context window budgeting
4. **calibration.py** — Calibration via operator feedback
5. **Exemplar notes** — Not code, just vault notes

Let me think about the architecture for each:

### 1. claim_verifier.py
- Extracts atomic claims from a note's synthesis section
- Locates cited sources in learningMaterial/web/
- Checks entailment (does the source say what the claim says?)
- Flags hallucinations
- Integration: after vault_research completes, before the note is considered final
- Could be called from the autonomous researcher after writing a note, or as a tool the LLM can call
- Needs LLM for claim extraction and entailment checking (or could use simple heuristics)
- Actually, the research says MiniCheck does this efficiently with grounded entailment models. But we don't have MiniCheck. We'd need to use the LLM client.
- Design: `ClaimVerifier` class with `verify_note(note_path)` method
- Returns: list of claims with verification status (entailed, contradicted, unsourced, unverified)
- Integration point: after `vault_research` writes a note, call `claim_verifier.verify_note(note_path)` and log results

### 2. rag_eval.py
- Measures retrieval quality: recall@k, precision@k, NDCG
- Needs ground-truth relevant notes per query
- Ground truth comes from: Sean's corrections (when he says "you missed X"), or from test queries with known expected results
- Design: `RAGEvaluator` class with `evaluate(query, retrieved, expected)` method
- Also tracks metrics over time to detect regressions
- Integration point: after FUSED retrieval, optionally evaluate and log
- Could be a tool the LLM calls, or a background task

### 3. context_budgeter.py
- Ranks and truncates retrieved notes to fit token budget
- Token estimation: simple char/4 or tiktoken
- Ranking: FUSED score, note type priority, wikilink density, note length
- Truncation: include most important sections of partially-fitting notes
- Integration point: between FUSED retrieval and context building (line ~1230 in main.py)
- Currently `build_abstract_context` handles this somewhat, but a dedicated budgeter would be more explicit

### 4. calibration.py
- Uses Sean's corrections as ground truth
- Tracks when Sean corrects an output and maps it to which quality gate passed/failed
- Computes calibration metrics: precision, recall, F1 of quality gates
- Integration point: when Sean sends a correction (detected via keywords like "no", "wrong", "actually", or explicit feedback)
- Also: periodic calibration report

### 5. Exemplar notes
- Not code — just vault notes with specific design patterns
- Need to create 3-5 exemplar notes tagged as `type: exemplar`
- These serve as few-shot examples for small models

Now let me think about the implementation plan structure:

**Phase 1: Context Budgeter** (most immediately useful, no LLM needed)
- Pure deterministic — token counting, ranking, truncation
- Integration: between FUSED retrieval and context building
- Can be tested immediately

**Phase 2: RAG Evaluator** (needs ground truth, but infrastructure first)
- Build the evaluation framework
- Start with a test query set
- Log metrics over time
- Integration: after FUSED retrieval, log metrics

**Phase 3: Claim Verifier** (needs LLM, but high value)
- Post-synthesis verification
- Integration: after vault_research writes a note
- Uses LLM for claim extraction and entailment checking

**Phase 4: Calibration** (needs accumulated data)
- Track Sean's corrections
- Map to quality gate pass/fail
- Compute calibration metrics
- Integration: in the chat loop, detect corrections

**Phase 5: Exemplar Notes** (no code, just vault content)
- Create 3-5 exemplar notes
- Tag them as `type: exemplar`
- Link to the Exemplar-Note-Design architecture note

Let me write this as a comprehensive implementation plan note in the vault.Now I have a thorough understanding of the codebase. Let me write the implementation plan. I need to create a comprehensive note that covers:

1. What modules to build
2. Integration points in the existing code
3. Dependencies between modules
4. Build order (phases)
5. What NOT to touch
6. Testing strategy for each

Let me write this as a vault note.Good, the token estimation and ranking formula work. Now let me write the comprehensive implementation plan note.Good, the plan is well-formed. Now let me write it to the vault.The plan note passes lint — 18 wikilinks, 0 broken, has frontmatter. The "empty_sections" issue is from code blocks (the dependency graph and class designs), not real empty sections.

Let me give Sean the summary.

</details>