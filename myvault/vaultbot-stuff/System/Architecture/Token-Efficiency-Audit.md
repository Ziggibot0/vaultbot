---
type: system-design
status: complete
baseline: true
created: 2026-07-31
title: Token Efficiency Audit
tags:
  - architecture
  - token-efficiency
  - llm-cost
  - scaling
  - audit
depends_on:
  - "[[Context-Budgeting-for-Vault-Growth]]"
  - "[[Framework-Friction-Fix-Plan]]"
summary: SUMMARY
---

# Token Efficiency Audit

> **<!-- updated 2026-08-11 -->** This audit was written 2026-07-31. Some code references have since changed:
> - `compactor.py` was replaced by `lazy_condenser.py`. References to `compactor.py` and `should_compact()` are historical — the compaction system was redesigned. See `main.py` docstring for current context management (sliding window, not LLM-based compaction).
> - `build_system_prompt()` in `agent_tools.py` — still current as of 2026-08-11.
> - `truncate_tool_result()` in `chat_helpers.py` — still current as of 2026-08-11.
> - `context_budgeter.py` — still current as of 2026-08-11.

## Why This Matters

The mission is to make cloud models obsolete by moving cognition into the vault. But there's a prerequisite: **before you can replace the cloud model with a small local one, you have to stop feeding the cloud model a mountain of tokens it doesn't need.** A small local model has a small context window (8K–32K tokens typically). If the system prompt alone is 16K tokens, no small model can run it. Reducing token usage is the first step toward model independence — not because cloud tokens are expensive (they're cheap), but because the token budget determines what models can run the system at all.

This audit maps every token sent to the LLM on a typical turn, identifies the sinks, and proposes concrete strategies — none of which involve lazy truncation (blindly chopping text at an arbitrary character count and hoping the model can still reason over the fragment).

---

## The Complete Token Flow

Every LLM call in the chat loop sends these components:

| Component | Source | Est. Tokens | Per-Turn Behavior |
|-----------|--------|-------------|-------------------|
| **Instructions** | `build_system_prompt()` in `agent_tools.py` | ~5,000 | Rebuilt every turn, mostly static text |
| **Identity files** | `identity.py` → IDENTITY.md + SELF_MODEL.md + GOALS.md | ~4,000 | IDENTITY.md static; SELF_MODEL.md regenerated each turn (cap 12K chars); GOALS.md changes occasionally |
| **Tool schemas** | `TOOL_DEFINITIONS` + `META_TOOL_DEFINITIONS` + custom tools | ~2,400 | 32 tool schemas, all sent every call, fully static |
| **Vault context** | `build_abstract_context()` → L2 MOC + L1 cards + L0 drill-down | ~5,000 | Multi-resolution, already budgeted by `context_budgeter.py` |
| **Conversation history** | `websocket.conversation_history` | 0 → unbounded | Grows ~4–5K tokens per tool round; compaction disabled |
| **Tool results** | `truncate_tool_result()` caps at 10K chars | ~2,500 per result | Multiple results per round; each capped at 10K chars (~2.5K tokens) |
| **Knowledge gaps** | `vault_gaps` summary in system prompt | ~500 | Rebuilt every turn from dangling links list |
| **Working memory** | `TaskList` injected into system prompt | ~300 | Per-session plan, updated each round |

### First Turn (No History)
~17,000 tokens — system prompt + context + first user message.

### After 5 Tool Rounds
~17,000 base + 5 × (tool_result 2,500 + thinking 1,000 + response 1,000) = **~38,500 tokens**

### After 10 Tool Rounds
~17,000 + 10 × 4,500 = **~62,000 tokens**

### After 20 Tool Rounds
~17,000 + 20 × 4,500 = **~107,000 tokens**

<!-- updated 2026-08-11: compactor.py no longer exists. Compaction was replaced by lazy_condenser.py and then by a sliding-window approach in chat_handler.py. The should_compact() function and its 500K threshold are historical. -->
The compactor is disabled (`should_compact()` returns `False`), so history grows without bound. The threshold was raised to 500K tokens because the model has a 1M context window — but that means a 20-round agentic session sends 100K+ tokens to the API every single call, and a small local model with an 8K window can't even load the system prompt.

---

## Token Sinks Ranked by Impact

### Sink 1: Conversation History (Unbounded Growth)
**Impact:** ~4,500 tokens added per tool round. After 10 rounds, 45K tokens of history sent on every subsequent call. This is the #1 sink by far.

**Root cause:** `compactor.py`'s `should_compact()` hard-returns `False`. The threshold was set to 500K tokens (50% of a 1M context window) to prevent mid-task summarization from shredding tool results. But this means history never compacts — it just accumulates.

> **<!-- updated 2026-08-11 -->** `compactor.py` no longer exists as a standalone file. Compaction was replaced by `lazy_condenser.py` (588 lines), which implements a sliding-window context management approach. The `should_compact()` function is gone. See `main.py` lines 630-635 for the removal note. The core problem (unbounded history growth) is still relevant, but the specific code reference is stale.

**Why it matters for small models:** A small model with 8K context can't even fit the history from a 2-round session. Even with a 32K context model, after 5 rounds the history alone consumes 60% of the context window.

### Sink 2: Tool Results (10K Char Cap, Multiple Per Round)
**Impact:** ~2,500 tokens per tool result. A typical agentic round calls 1–3 tools, so 2.5K–7.5K tokens of tool results per round.

**Root cause:** `truncate_tool_result()` in `chat_helpers.py` caps each result at 10K chars (~2,500 tokens). This is generous — a `vault_research` synthesis, a `code_read` of a large file, or a `vault_graph_analyzer` dump can be 10K chars. The cap prevents a single result from blowing up the payload, but multiple results per round compound.

**The lazy truncation problem:** The truncation is "smart-ish" — it preserves dict structure and truncates per-key, then overall. But it's still blind character chopping. A research synthesis might have the key finding in the last paragraph, and truncation drops it. The model gets a `[...truncated: 5000 chars dropped from 'synthesis'...]` marker but no way to recover the lost content without re-running the tool.

### Sink 3: Tool Schemas (32 Schemas × ~300 Chars Each)
**Impact:** ~2,400 tokens sent on EVERY LLM call, regardless of whether the tool is relevant to the current task.

**Root cause:** All 32 tool schemas (7 built-in + 9 meta + 16 custom) are sent as the `tools` parameter on every API call. The model sees all of them every time, even for a simple "what's the weather" question where only `vault_search` is relevant.

### Sink 4: System Prompt Instructions (~5K Tokens Static)
**Impact:** ~5,000 tokens of instruction text rebuilt every turn, even though it almost never changes.

**Root cause:** `build_system_prompt()` constructs the instructions as a giant f-string, rebuilt on every chat turn. The text is identical across turns except for the owner name, tool count, and custom tool list. This is pure waste — the model has read these instructions 50 times already.

### Sink 5: Identity Files (~4K Tokens, Mostly Static)
**Impact:** ~4,000 tokens for IDENTITY.md + SELF_MODEL.md + GOALS.md.

**Root cause:** All three files are injected into the system prompt every turn. IDENTITY.md is human-seeded and rarely changes. SELF_MODEL.md is regenerated each turn but often says similar things. GOALS.md changes only when a task starts or completes.

### Sink 6: Vault Context (~5K Tokens, Already Well-Managed)
**Impact:** ~5,000 tokens for the multi-resolution context (L2 MOC + L1 cards + L0 drill-down).

**Status:** This is actually the best-managed sink. The `abstract_context.py` multi-resolution approach (L2 bird's-eye → L1 concept cards → L0 drill-down) and the `context_budgeter.py` budgeting are working as designed. The budgeter truncates from the end (lowest-priority L0 detail) when needed. Minor optimization possible but not a primary sink.

### Sink 7: Knowledge Gaps Summary (~500 Tokens)
**Impact:** ~500 tokens listing current dangling wikilinks and thin notes.

**Root cause:** Rebuilt every turn from the gaps list, even when the task has nothing to do with gap-filling. For a casual "what's up" message, the model doesn't need to see 10 dangling wikilinks.

---

## Concrete Strategies

### Tier 1: High Impact, Low Effort (Do First)

#### Strategy 1: Re-enable Compaction at a Reasonable Threshold
**Sink:** Conversation history (Sink 1)
**Effort:** 1 line change in `lazy_condenser.py` <!-- updated 2026-08-11: compactor.py was replaced by lazy_condenser.py -->
**Expected savings:** 50–70% of total tokens after 10+ rounds

Change `should_compact()` to return `True` when the conversation exceeds ~40K tokens (10K chars × 4 chars/token). The compactor already implements the OpenHands Condenser pattern: it keeps the head (system prompt + vault context) and tail (recent turns) verbatim, and summarizes the middle. The key fix: set the threshold to a value appropriate for the target model, not the current cloud model.

For a 1M-token cloud model: 40K threshold is fine.
For a 32K local model: 16K threshold.
For an 8K local model: 6K threshold.

Make the threshold configurable via `VAULTBOT_COMPACT_TOKEN_THRESHOLD` (already exists) and set it based on the model's context window, not a fixed 500K.

#### Strategy 2: Reduce Tool Result Cap to 5K Chars
**Sink:** Tool results (Sink 2)
**Effort:** 1 line change in `chat_helpers.py`
**Expected savings:** ~1,250 tokens per tool result

Change `truncate_tool_result(result, max_chars=10000)` to `max_chars=5000`. Most tool results are actionable in 5K chars. The model can always re-read with narrower parameters (the truncation message already tells it to). This halves the per-result token cost.

#### Strategy 3: Prompt Caching for Static Prefix
**Sink:** Instructions (Sink 4) + Tool schemas (Sink 3) + Identity files (Sink 5)
**Effort:** Medium — requires restructuring the API call to separate static/dynamic parts
**Expected savings:** 80–90% cost reduction on the static prefix (not token reduction, but cost reduction)

If using an OpenAI-compatible backend that supports prompt caching (Anthropic, OpenAI, Google), structure the messages so the static prefix (instructions + tool schemas + identity) is a stable prefix that gets cached. Only the dynamic parts (vault context, conversation history, working memory) change between calls.

This doesn't reduce the token count but reduces the cost to ~1/10th for the cached portion. With ~11K tokens in the static prefix, that's ~10K tokens at 1/10th cost on every call.

#### Strategy 4: Compress System Prompt Instructions
**Sink:** Instructions (Sink 4)
**Effort:** Low — rewrite the f-string in `build_system_prompt()`
**Expected savings:** ~2,500 tokens per call

The instructions are verbose. The HOW YOU WORK section alone is ~1,500 tokens of prose that could be ~500 tokens of bullet points. The YOUR POWER section repeats tool descriptions already in the schemas. The YOUR MISSION section is aspirational text the model doesn't need to read 50 times. Compress to essentials: what to do, in what order, what rules to follow. Move the philosophy into IDENTITY.md (which is already injected separately).

### Tier 2: High Impact, Medium Effort

#### Strategy 5: Tool Schema Selection (Send Only Relevant Tools)
**Sink:** Tool schemas (Sink 3)
**Effort:** Medium — build a keyword-based tool selector
**Expected savings:** ~1,500 tokens per call (from 32 tools to ~10)

Instead of sending all 32 tool schemas every turn, select 8–12 relevant tools based on the user message and current task. A simple keyword matcher:
- "research" → vault_research, vault_search, web_read_source
- "code" / "fix" / "edit" → code_read, code_run, safe_write, js_safe_write, git_rollback
- "plan" / "task" → plan_task, update_task, set_goal
- "vault" / "graph" / "gaps" → vault_search, vault_gaps, vault_graph_analyzer, vault_cluster_analyzer
- Always include: vault_search, plan_task, update_task (core tools)

This is deterministic, no LLM cost. The model can still call any tool — if it tries to call a tool not in the current schema list, the backend can inject that tool's schema on the next round (progressive disclosure).

#### Strategy 6: Structured Tool Result Summaries
**Sink:** Tool results (Sink 2)
**Effort:** Medium — modify tool return types
**Expected savings:** ~1,500 tokens per tool result

Instead of returning raw text from tools, return structured summaries. For example, `vault_research` currently returns the full synthesis text. Instead, return:

```json
{
  "summary": "5 sources, 12 facts. Key finding: ...",
  "facts": ["fact 1", "fact 2", "fact 3"],
  "sources": ["source 1", "source 2"],
  "note_path": "Knowledge/Research/Topic.md",
  "full_note_available": true
}
```

The model gets the actionable summary (facts + sources) without the full prose. If it needs more detail, it can `code_read` the note file. This is the opposite of lazy truncation — it's **structured extraction** that preserves the signal and drops the noise.

#### Strategy 7: Tool-Result-Aware History Compaction
**Sink:** Conversation history (Sink 1)
**Effort:** Medium — modify compactor's `compact()` method
**Expected savings:** ~50% more than generic compaction

When compacting the conversation middle, don't summarize everything equally. Instead:
- **User messages:** keep verbatim (they're short and critical)
- **Assistant responses:** keep verbatim (they're the output the model needs to reference)
- **Tool results:** replace with a one-line summary ("vault_research: 5 sources, 12 facts, note written to X")
- **Tool calls:** keep the tool name + arguments (compact, shows what was done)

This preserves the reasoning thread while aggressively compacting the heaviest component (tool results). The model knows WHAT it did and WHAT it found, without seeing the full 10K-char result again.

### Tier 3: Medium Impact, Low Effort

#### Strategy 8: Lazy Knowledge Gaps Injection
**Sink:** Knowledge gaps (Sink 7)
**Effort:** Low — conditional in `build_system_prompt()`
**Expected savings:** ~500 tokens per call

Only inject the knowledge gaps summary when:
- The task is gap-filling or autonomous research
- The user explicitly asks about gaps
- The model calls `vault_gaps`

For normal chat ("what's up", "explain X", "fix this bug"), skip the gaps section entirely. The model can call `vault_gaps` if it needs them.

#### Strategy 9: Conditional Identity Injection
**Sink:** Identity files (Sink 5)
**Effort:** Low — conditional in system prompt builder
**Expected savings:** ~3,000 tokens per call (after first turn)

IDENTITY.md never changes — send it only on the first turn of a session. SELF_MODEL.md is regenerated each turn but the model just wrote it — it already knows what it says. Only inject SELF_MODEL.md if it changed since last turn (hash comparison). GOALS.md changes rarely — inject only when it changes.

For subsequent turns, inject a one-line summary: "Identity: unchanged. Self-model: regenerated (see prior turn). Goals: [current goal line]."

#### Strategy 10: Tighter L1 Concept Cards
**Sink:** Vault context (Sink 6)
**Effort:** Low — modify card generation in `concept_card.py`
**Expected savings:** ~1,000 tokens per call

L1 concept cards are currently 300–500 chars each. With 20 cards, that's 6–10K chars. Tighten to 150–200 chars each (just the claim + key links, not the full reasoning). The model can drill down to L0 for any note it needs more detail on. 20 × 175 chars = 3.5K chars = ~875 tokens, saving ~1.5K tokens.

### Tier 4: Strategic (Higher Effort, Longer Term)

#### Strategy 11: Per-Step Context Retrieval
**Sink:** Vault context (Sink 6)
**Effort:** High — restructure the chat loop
**Expected savings:** ~3,000 tokens per round (after first)

Instead of retrieving vault context once per user message and sending it on every subsequent round, retrieve context per plan-step. Each step gets only the context relevant to that step. The first round gets the full subgraph; subsequent rounds get a fresh retrieval based on the current step's query.

This means the vault context is always relevant to the current action, not the original question. After 5 rounds, instead of sending the same 5K-token context 5 times, you send 5K once + 1K × 4 targeted retrievals = 9K total instead of 25K.

#### Strategy 12: Two-Phase Tool Loading
**Sink:** Tool schemas (Sink 3)
**Effort:** Medium — restructure tool dispatch
**Expected savings:** ~1,800 tokens on first call

Phase 1: Send only core tools (vault_search, plan_task, update_task, vault_research, code_read, code_run, set_goal). The model plans and starts work.

Phase 2: Based on the plan, inject additional tool schemas. If the plan involves self-editing, add safe_write, js_safe_write, git_rollback, preflight_safety_check. If it involves graph analysis, add vault_graph_analyzer, vault_cluster_analyzer.

This is progressive disclosure — the model sees only what it needs at each phase.

#### Strategy 13: Deduplicate System Prompt vs Identity
**Sink:** Instructions (Sink 4) + Identity (Sink 5)
**Effort:** Low — rewrite
**Expected savings:** ~1,500 tokens

The system prompt instructions repeat information from IDENTITY.md ("You are VaultBot, a self-improving research agent..."). The YOUR POWER section lists tools that are already in the tool schemas. The YOUR MIND section repeats what SELF_MODEL.md says. Remove the redundancy — the identity files already provide this context.

---

## What NOT to Do (Anti-Patterns)

### Lazy Truncation
Blindly chopping text at N characters with a `[...truncated...]` marker. This is what Sean explicitly rejects, and for good reason:
- You don't know what was lost (could be the key finding)
- The model can't reason over what it can't see
- The truncation point is arbitrary — it doesn't respect semantic boundaries
- It's the "lossy as FUCK" approach

### Summarizing Everything with LLM Calls
Using LLM calls to summarize tool results before sending them back. This trades token savings for LLM cost — you spend tokens to save tokens. It also adds latency. Only worth it for truly massive results (>20K chars).

### Removing Context the Model Needs
Cutting vault context, identity, or instructions too aggressively. The model NEEDS context to work. The goal is to remove REDUNDANCY and BLOAT, not signal. A model with no context is just a chatbot — and we're trying to be more than that.

---

## Implementation Priority

| Priority | Strategy | Effort | Impact | Tokens Saved/Turn |
|----------|---------|--------|--------|-------------------|
| P0 | Re-enable compaction (threshold ~40K) | 1 line | Massive | 50-70% after 10+ rounds |
| P0 | Reduce tool result cap to 5K | 1 line | High | ~1,250 per result |
| P1 | Compress system prompt instructions | Low | High | ~2,500 |
| P1 | Tool schema selection | Medium | High | ~1,500 |
| P1 | Prompt caching for static prefix | Medium | High (cost) | ~10K at 1/10th cost |
| P2 | Structured tool result summaries | Medium | Medium | ~1,500 per result |
| P2 | Tool-result-aware compaction | Medium | High | 50% more than generic |
| P2 | Lazy gaps injection | Low | Low | ~500 |
| P2 | Conditional identity injection | Low | Medium | ~3,000 (after turn 1) |
| P3 | Per-step context retrieval | High | Medium | ~3,000/round |
| P3 | Two-phase tool loading | Medium | Medium | ~1,800 on first call |
| P3 | Tighter L1 cards | Low | Low | ~1,000 |
| P3 | Deduplicate prompt vs identity | Low | Low | ~1,500 |

---

## The Small-Model Math

The real test: can a 7B parameter model with an 8K context window run VaultBot?

Current state: **No.** The system prompt alone is ~17K tokens. Even with zero conversation history, the system prompt exceeds an 8K context window by 2×.

After implementing P0 + P1 strategies:
- Compressed instructions: ~2,500 tokens (from 5K)
- Conditional identity (first turn only, or compressed): ~1,500 tokens (from 4K)
- Tool schema selection (10 tools): ~750 tokens (from 2.4K)
- Vault context (already budgeted): ~5,000 tokens
- Working memory: ~300 tokens
- Gaps (lazy): ~0 tokens (skip for normal chat)
- **Total system prompt: ~10,050 tokens**

Still too big for 8K. But with P2 + P3:
- Tighter L1 cards: ~4,000 tokens (vault context reduced)
- Deduplicated prompt: ~8,500 tokens total

Getting close. With aggressive P3 (per-step retrieval, two-phase tools):
- First round: ~6,500 tokens (fits in 8K with room for a user message + response)
- Subsequent rounds: ~3,000 tokens (per-step context, minimal tools)

**This is the path to small-model viability.** Every token saved in the system prompt is a token the small model can use for actual reasoning.

---

## Related

- [[Context-Budgeting-for-Vault-Growth]] — the existing budgeting strategy for vault context
- [[Framework-Friction-Fix-Plan]] — identified context mismanagement as a root cause of framework friction
- [[Small-model-scaffolding-techniques-that-make-dumb-models-perform-well-prompt-sca]] — scaffolding research
- [[Deterministic-Scaffolding-for-Small-Models]] — the broader small-model strategy
- [[Chat-any-other-ways-that-you-could-improve-yourself-so]] — Sean's founding principle: "the power shouldn't come from the LLM"