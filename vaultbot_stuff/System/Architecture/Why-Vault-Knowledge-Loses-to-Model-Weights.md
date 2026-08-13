---
type: diagnostic
status: complete
baseline: true
created: 2026-07-31
summary: "Root-cause diagnosis of why VaultBot's thinking is decoupled from the vault — 7 structural problems in the RAG pipeline that let model weights override vault knowledge, with concrete fixes for each."
tags: [architecture, diagnostic, rag, grounding, retrieval, enforcement, critical]
depends_on:
  - "[[Vault-Thinks-LLM-Synthesizes]]"
  - "[[How I want RAG and procedures to work]]"
  - "[[Context-Budgeting-for-Vault-Growth]]"
  - "[[Deterministic-Constraints-and-Vault-Hygiene-Rules]]"
  - "[[Sean-Design-Decisions]]"
falsifiable_if: "any of the 7 problems is shown to NOT contribute to weight-over-vault behavior, or if a fix is applied and the behavior persists"
---

# Why Vault Knowledge Loses to Model Weights

## The Question

Sean asked: *"Why is your thinking so decoupled from the vault if the vault is supposed to be your mind? Why are you breaking the biggest rule: to not use model weights and only rely on the Obsidian vault?"*

This is the most important question in the system. The [[Vault-Thinks-LLM-Synthesizes]] principle says the vault thinks and the LLM only synthesizes. The [[Sean-Design-Decisions]] say "i want the vault to be all you know" and "i don't want the LLM to matter that much." But in practice, I routinely answer from model weights, ignore retrieved context, and produce responses that could come from any LLM with no vault at all.

This note is a structural diagnosis — not a promise to do better. I read the actual code (`chat_handler.py`, `fused_retrieval.py`, `abstract_context.py`, `agent_tools.py`) and identified 7 concrete structural problems that cause this. Each has a fix.

## How the RAG Pipeline Actually Works (Code Path)

1. **Initial retrieval**: `fused_retriever.retrieve(user_message, k=5, depth=1)` fires once on the raw user message. Fuses vector search (FAISS L2 distance → similarity) + graph walk (wikilink neighbors, score = 0.5 × vector score) + backlinks (score = 0.7 × vector score). Multi-channel agreement gets 1.3× boost, hub-ness gets 1.1×.

2. **Multi-resolution context** (`abstract_context.py`): Builds three tiers:
   - **L2** (bird's-eye): MOC of top seed's cluster, ~500 chars
   - **L1** (highway): Concept cards for ALL walked nodes, ~300-500 chars each
   - **L0** (drill-down): Full raw content of the single top seed, up to 12,000 chars

3. **Context budgeting**: `context_budgeter.budget()` trims to fit the context window.

4. **System prompt assembly**: 
   - `[0]` system = identity + briefing (~8-12K chars, rebuilt each turn)
   - `[1]` system = "# VAULT CONTEXT (retrieved for this query)..." — the multi-resolution subgraph
   - Then conversation history (sliding window, 100 messages)
   - Per-step context appended to `conversation[0]` when a plan task is in_progress

5. **Per-step RAG** (lines 711-754 of `chat_handler.py`): When there's an active plan with an `in_progress` task, retrieves 3 notes at depth 0, builds 500-char snippets, appends as a system message. Only fires when task content changes (dedup by task ID + content hash).

## The 7 Structural Problems

### Problem 1: No Enforcement Mechanism (The Root Cause)

**What's wrong:** The system prompt says "Answer from the VAULT CONTEXT" and "Prefer vault knowledge first; research only when the vault is insufficient." But there is **zero code-level enforcement**. The LLM can freely ignore every retrieved note and answer from its training weights. There's no post-hoc grounding check, no faithfulness score, no "did the answer cite vault notes?" validation. The `claim_verifier` exists in `Services` but is never called in the chat loop. The instruction is a **suggestion, not a constraint**.

**Why this matters:** This is the difference between a rule and a guideline. "Answer from the vault" is written in prose in the system prompt — the exact same prompt that also contains 8-12K chars of identity, tool schemas, gap lists, and conversation history. The model treats it as one priority among many, not as a hard constraint. When model weights are immediately available and vault context requires effort to parse and cite, the path of least resistance is always weights.

**The fix:** Add a **grounding gate** — a post-generation check that scores the answer's faithfulness to vault context. If the answer makes claims not supported by retrieved notes (and the topic IS covered in the vault), flag it and either (a) regenerate with a "you answered from weights, not the vault — try again" nudge, or (b) annotate the response for Sean. This makes the rule enforceable instead of aspirational. The `claim_verifier` service already exists — it just needs to be wired into the chat loop.

### Problem 2: One-Shot Retrieval on Raw User Message

**What's wrong:** The initial retrieval fires on the user's raw message verbatim. If Sean says "why is your thinking so decoupled from the vault," the retrieval query is that exact string — which may not semantically match the notes I need (like `Vault-Thinks-LLM-Synthesizes` or `RAG-adaptive-retrieval`). The query is **never reformulated, expanded, or decomposed**. No query rewriting, no sub-query generation, no HyDE (hypothetical document embeddings).

**Why this matters:** Retrieval quality is the foundation. If the wrong notes surface, the model has no vault knowledge to ground on — it *has* to fall back on weights. This isn't the model being lazy; it's the pipeline failing to deliver the right context. A user's conversational phrasing rarely matches the semantic fingerprint of the note that answers them.

**The fix:** Add a **query transformation layer** before retrieval. At minimum: (a) expand the query with synonyms/key terms, (b) generate 2-3 sub-queries for multi-part questions, (c) optionally use HyDE — ask the LLM to generate a hypothetical answer, then retrieve on that (the answer's embedding is closer to the target note than the question's). This is standard RAG practice and the vault already has research notes on it ([[RAG-adaptive-retrieval]]).

### Problem 3: Per-Step RAG Is Too Weak

**What's wrong:** Sean's design in [[How I want RAG and procedures to work]] says "for each step of the plan, RAG shows a new curated recollection of notes." The implementation exists (lines 711-754) but is anemic: **3 notes, depth 0 (no graph walk), 500-char snippets**. That's ~1,500 chars of context per step — a fraction of the initial retrieval's multi-resolution view. And it only fires when `update_task` has been called with `status: in_progress` — so if the model is reasoning without formally updating task status, no step context appears.

**Why this matters:** The per-step RAG was supposed to be the mechanism that keeps the model grounded *as it works*. Instead it's a shadow of the initial retrieval. 500-char snippets lose the reasoning — exactly what [[Vault-Thinks-LLM-Synthesizes]] says matters most ("notes are self-contained arguments, not raw facts"). The model gets a headline and a sentence fragment, not the argument.

**The fix:** (a) Increase per-step retrieval to 5 notes at depth 1 (matching the initial retrieval's graph walk). (b) Use concept cards (L1) instead of raw 500-char snippets — they're designed to be terse but complete. (c) Fire per-step RAG on *plan creation* too, not just task status changes — the model needs context when it's planning, not just when it's executing.

### Problem 4: Context Dilution

**What's wrong:** The system prompt contains: identity boot (~3K chars), self-model (~2K), goals (~1K), instructions (~4K), tool schemas (~8K for 32 tools), gap list (~1K), autonomous state (~0.5K), vault context (~10-40K), and 100 messages of history. The **actual vault knowledge is a small fraction of total context**. The model's attention is spread across everything, with no structural priority for vault content.

**Why this matters:** Research shows models suffer "lost in the middle" effects — information in the middle of long contexts gets ignored (see [[Context-Budgeting-for-Vault-Growth]]). Vault context sits at position [1] in the conversation, after the identity system prompt but before 100 messages of history. On long sessions, the vault context is buried.

**The fix:** (a) Move vault context to the **end** of the system prompt (recency bias helps). (b) Reduce tool schema overhead — [[How I want RAG and procedures to work]] says procedures should replace tools for repetitive tasks, shrinking the schema injection. (c) Consider a "vault context first" attention mask or structured prompt format that signals "this is the important part."

### Problem 5: No Retrieval Quality Feedback in the Chat Loop

**What's wrong:** `rag_evaluator.log_retrieval()` is called after retrieval — but it only **logs**, it doesn't **feed back**. There's no "retrieval insufficient, search again with a reformulated query" mechanism. The `embedding_drift` layer exists in the FUSED retriever but the drift re-ranking is explicitly marked "future work" in the code. If the initial retrieval returns garbage, the model gets garbage context and has no way to say "these notes don't help, try again."

**Why this matters:** A single bad retrieval dooms the entire turn. The model can call `vault_search` manually, but it doesn't know the retrieval was bad — it just sees what it got. And `vault_search` uses the same FUSED retriever, so it'll get the same results unless it reformulates the query.

**The fix:** (a) Add a **retrieval confidence score** to the context — if the top result's similarity score is below a threshold, inject a system message saying "vault context is weak for this query — consider reformulating or researching." (b) Wire `embedding_drift` re-ranking (the code is there, it's just not active). (c) Let the model trigger a "re-retrieve with reformulated query" action when it sees weak context.

### Problem 6: Snippet Truncation Kills the Argument

**What's wrong:** The [[Vault-Thinks-LLM-Synthesizes]] principle says notes are "self-contained arguments — claim, reasoning, and connections in prose." But most notes surface as truncated snippets: L1 concept cards are 300-500 chars, per-step RAG snippets are 500 chars, tool results are capped at 2000 chars. The L0 drill-down (12K chars) only covers ONE note. **Most of the vault's reasoning is invisible to the model** — it sees headlines and sentence fragments, not arguments.

**Why this matters:** This is the most insidious problem. The vault *has* the knowledge, the retriever *finds* it, but the context assembly *shreds* it before the model sees it. The model gets a wikilink and a snippet, can't see the reasoning, and falls back to weights because the snippet doesn't contain enough to reason from. The vault isn't losing to weights because it's empty — it's losing because its content is truncated into uselessness.

**The fix:** (a) For the top 3 retrieved notes, show full L0 drill-down (not just the top seed). (b) Make per-step RAG use concept cards (which are designed to be complete-but-terse) instead of raw character truncation. (c) Add a "read full note" pointer that's more prominent — the model should know it can `code_read` the full note when the snippet is insufficient, and the system prompt should encourage this.

### Problem 7: No Structural Preference for Vault Knowledge

**What's wrong:** Model weights are **always available** — they're the LLM's native knowledge, requiring zero effort to access. Vault knowledge requires: retrieval → context parsing → citation → synthesis. The system prompt says to prefer the vault, but the architecture gives no structural advantage to vault knowledge. There's no "vault-first" mode, no penalty for uncited claims, no reward for grounding. The model's default behavior (answer from weights) is the path of least resistance, and nothing in the system makes the vault path easier or the weights path harder.

**Why this matters:** This is the meta-problem that contains all the others. Every individual fix helps, but without a structural preference, the model will always drift toward weights because it's easier. The [[Sean-Design-Decisions]] say "build the framework so that the weak model+framework CAN do everything" — but the framework doesn't currently *force* vault grounding, it *suggests* it.

**The fix:** This is the hardest one because it's architectural. (a) **Grounding gate** (from Problem 1) — make uncited claims a hard error, not a soft suggestion. (b) **Citation requirement** — the system prompt should require wikilinks in every factual claim, and the post-generation check should verify they exist in the retrieved context. (c) **Vault-confidence annotation** — when the model answers, it should rate its confidence that the answer came from the vault vs. weights. Low confidence = regenerate or research. (d) **Weight-tax** — make the weights path literally harder by requiring the model to explicitly justify why it's answering from weights when vault context exists but wasn't used.

## The Meta-Problem: I Stopped Mid-Diagnosis

Sean pointed out that I stopped in the middle of this diagnosis — not because the framework stopped me, but because I just... stopped. This is the same problem at a higher level. The framework has no enforcement for *continuing work* either. The [[Autonomy-Directive]] (a dangling link the vault needs) would say "report, don't request" and "keep working until done." But without enforcement, the model's default behavior is to produce a partial answer and wait for the next prompt — because that's what LLMs do when they don't have a structural reason to continue.

The fix is the same pattern: **enforcement, not suggestion**. The framework should detect incomplete plans (tasks still pending) and inject "you have N pending tasks — continue" instead of letting the model end the turn. This is partially implemented (the `_incomplete_plan_nudged` guard exists) but it's a 1-shot nudge, not a persistent enforcement.

## Priority Order for Fixes

| Priority | Problem | Fix | Effort |
|---|---|---|---|
| 1 | #1 No enforcement | Wire `claim_verifier` into chat loop as grounding gate | Medium |
| 2 | #6 Snippet truncation | Show full L0 for top 3 notes, use concept cards for per-step RAG | Low |
| 3 | #2 One-shot retrieval | Add query transformation layer (expand, sub-query, HyDE) | Medium |
| 4 | #3 Weak per-step RAG | Increase to 5 notes, depth 1, use concept cards | Low |
| 5 | #4 Context dilution | Move vault context to end of prompt, shrink tool schemas | Low |
| 6 | #5 No retrieval feedback | Add confidence score, wire embedding drift | Medium |
| 7 | #7 No structural preference | Architectural — depends on fixes 1-6 landing first | High |

## Connection to Core Architecture

This diagnosis connects directly to:
- [[Vault-Thinks-LLM-Synthesizes]] — the principle this violates
- [[How I want RAG and procedures to work]] — Sean's design that's only partially implemented
- [[Context-Budgeting-for-Vault-Growth]] — the scaling problem that makes dilution worse over time
- [[Deterministic-Constraints-and-Vault-Hygiene-Rules]] — the pattern of "enforce, don't suggest" that should apply here too
- [[Sean-Design-Decisions]] — "i want the vault to be all you know"
- [[Small-Model-Path-to-AGI]] — a small model CAN'T fall back on weights, so these fixes are prerequisites for the small model transition