---
type: architecture
status: draft
created: 2026-07-26
summary: "How VaultBot converts episodic experiences (chat logs, research, tool building) into reusable semantic knowledge through deterministic pattern extraction and scaffolded abstraction. The framework does the heavy lifting; the LLM only synthesizes pre-extracted patterns."
tags: [architecture, memory, consolidation, semantic, episodic, deterministic, automation]
depends_on:
  - "[[Procedural-Bootstrap-and-Evolution-Plan]]"
  - "[[Deterministic-Scaffolding-for-Small-Models]]"
  - "[[Vault-Longevity-Architecture]]"
  - "[[Calibration-via-Operator-Feedback]]"
sources:
  - "https://arxiv.org/html/2603.07670v1"
  - "https://towardsdatascience.com/a-practical-guide-to-memory-for-autonomous-llm-agents/"
  - "https://arxiv.org/abs/2605.20616v1"
  - "https://arxiv.org/abs/2601.02845v2"
  - "https://arxiv.org/abs/2303.11366v4"
---

# Semantic Consolidation Architecture

## The Problem

Every session, VaultBot has experiences: it builds tools, writes notes, gets corrected by Sean, hits walls, and solves problems. These experiences are stored as **episodic memory** — chat logs in `vaultbot/chat/`, research notes, tool creation records. But they stay episodic. Next session, the LLM has to re-derive the same insights from scratch because nobody consolidated them into **semantic knowledge** — abstracted, de-contextualized patterns that are true across sessions.

This is the exact gap identified in the memory survey literature: "The consolidation step — where episodes become semantic knowledge — is particularly underserved: it typically requires either explicit developer rules or periodic LLM-driven summarization, both of which are fragile and hard to validate" [sources: Memory for Autonomous LLM Agents: Mechanisms, Evaluation, and Emerging Frontiers].

Sean's directive makes the constraint clear: **automate as much as possible so the LLM has very little to do.** The framework and vault should handle pattern extraction. The LLM should only synthesize pre-extracted patterns into prose.

## The Core Insight

> "The gap between 'has memory' and 'does not have memory' is often larger than the gap between different LLM backbones." [sources: Memory for Autonomous LLM Agents]

The vault already has episodic memory (chat logs, research notes) and procedural memory (How-to notes, directives). What's missing is **semantic memory** — the abstracted patterns that make the vault *smarter over time* without the model getting smarter.

The solution is a **deterministic consolidation pipeline** that runs as a background process (like the autonomous researcher), extracts patterns mechanically, and writes semantic knowledge notes that future sessions retrieve via FUSED search.

## The Write-Manage-Read Loop

The survey formalizes agent memory as a **write-manage-read loop** [sources: Memory for Autonomous LLM Agents]:

| Phase | What VaultBot Does Today | What's Missing |
|---|---|---|
| **Write** | Chat logs saved, research notes written, tool creation logged | ✅ Working |
| **Manage** | vault_lint checks quality, A-MEM evolves tags/links | ❌ No consolidation, no pattern extraction, no abstraction |
| **Read** | FUSED retrieval injects context at session start | ✅ Working but reads only episodic + procedural, no semantic layer |

The **manage** phase is where consolidation happens. It's the gap. Most systems "nail write and read and completely neglect manage. They accumulate without curation. The result is noise, contradiction, and bloated context" [sources: A Practical Guide to Memory for Autonomous LLM Agents].

## Four Memory Types in VaultBot

| Memory Type | What It Is | Where It Lives in VaultBot | Status |
|---|---|---|---|
| **Working** | Current context window | System prompt + vault context subgraph | ✅ Working |
| **Episodic** | Concrete experiences | `vaultbot/chat/` logs, research notes | ✅ Accumulating |
| **Semantic** | Abstracted patterns | **Does not exist yet** | ❌ **The gap** |
| **Procedural** | Reusable skills | How-to notes, directives, exemplar notes | ✅ 8 procedures |

The transition from episodic → semantic is what consolidation solves. An episodic fact like "Sean corrected me on 2026-07-26 because my self-model said 2 procedures when there were 5" consolidates into the semantic pattern "VaultBot's self-model goes stale across sessions; always run vault_list before assuming what exists."

## Three Mechanisms from the Literature

### 1. Generative Agents Reflection (Park et al. 2023)

Raw observations accumulate in an episodic stream. Periodically, the agent clusters related observations and synthesizes higher-order reflections. Retrieval scores memories by a weighted mix of **recency** (exponential decay), **relevance** (embedding similarity), and **importance** (a self-assessed integer) [sources: Memory for Autonomous LLM Agents].

**Key finding**: Removing the reflection component caused agent behavior to degenerate from coherent multi-day planning to repetitive, context-free responses within 48 simulated hours. Reflection is not optional — it's what prevents the agent from repeating the same mistakes.

**How to adapt for VaultBot**: Instead of clustering observations by embedding similarity (LLM-dependent), cluster by **deterministic signals**: shared wikilinks, shared tags, shared tool usage, co-occurring failure types. The clustering is mechanical; only the final synthesis step uses the LLM.

### 2. Reflexion (Shinn et al. 2023)

After failing a task, the agent writes a natural language post-mortem, then prepends it to the prompt on the next attempt. "No gradient updates, no reward model — just a text file of self-critiques." Results: 91% pass@1 on HumanEval vs. 80% for GPT-4 without reflection [sources: Memory for Autonomous LLM Agents].

**How to adapt for VaultBot**: The calibration tracker ([[Calibration-via-Operator-Feedback]]) already detects Sean's corrections. Each correction is a "failure" that should generate a reflection. The consolidation system batches these corrections, extracts the pattern (what went wrong, why, how to prevent it), and writes a semantic note.

### 3. ExpeL (Zhao et al. 2024)

Systematically contrasts successful and failed trajectories, extracting discriminative "rules of thumb" and storing them as reusable heuristics [sources: Memory for Autonomous LLM Agents].

**How to adapt for VaultBot**: Compare sessions where things went well (Sean said "yes", "go ahead", "cool beans") vs. sessions where things went poorly (Sean corrected, said "no", said "I thought you already did that"). The differences between these trajectories ARE the rules of thumb. This comparison can be done deterministically by scanning chat logs for Sean's response patterns.

## The Deterministic Consolidation Pipeline

### Design Principle: Extract Mechanically, Synthesize with LLM

The key constraint from Sean: **the framework does most things, the LLM does very little.** This means:

1. **Pattern detection** = deterministic (code, no LLM)
2. **Pattern clustering** = deterministic (graph analysis, frequency counts)
3. **Abstraction synthesis** = LLM-assisted but scaffolded (pre-extracted patterns as input)
4. **Quality validation** = deterministic (vault_lint, claim verification)

### Phase 1: Scan (Deterministic)

Scan episodic memory sources since last consolidation:
- Chat logs in `vaultbot/chat/` (newest N files or files since last consolidation timestamp)
- Research notes created since last consolidation
- Calibration log entries (`calibration_log.json`)
- Procedure failure log entries (`procedure_failure_log.json`)
- RAG evaluation log entries (`rag_eval_log.json`)

Output: A structured dataset of experiences, each tagged with:
- Timestamp
- Type (chat, research, tool-building, correction, failure, success)
- Tools used
- Notes created/modified
- Sean's response (positive/negative/neutral — detectable from keywords)
- Topics discussed (extractable from wikilinks in the chat log)

### Phase 2: Extract Patterns (Deterministic)

Run mechanical pattern extraction on the scanned experiences:

| Pattern Type | How to Detect It | What It Produces |
|---|---|---|
| **Recurring topics** | Count wikilinks across chat logs; find links that appear in N+ sessions | "These topics come up repeatedly" |
| **Recurring failures** | Scan calibration log for repeated failure types | "This type of mistake keeps happening" |
| **Recurring workflows** | Detect sequences: research → write note → lint → report | "This is how tasks typically flow" |
| **Sean's preferences** | Scan for correction patterns + positive responses | "Sean prefers X, dislikes Y" |
| **Tool usage patterns** | Count which tools are used together | "These tools are always used in sequence" |
| **Stale knowledge** | Compare self-model claims vs. vault_list reality | "Self-model drifts on these topics" |
| **Research engine failures** | Scan research notes for low-quality findings | "Research engine fails on these query types" |

Each pattern is a **deterministic finding** — a fact about the vault's history that code can verify. No LLM needed.

### Phase 3: Cluster (Deterministic)

Group related patterns using the vault's own graph:
- Patterns that share wikilinks belong to the same cluster
- Patterns that share tags belong to the same cluster
- Patterns that reference the same notes belong to the same cluster

This uses the existing FUSED retrieval infrastructure — the same vector + graph + backlink scoring that powers vault_search, applied to the extracted patterns instead of user queries.

Output: N clusters of related patterns, each with a theme (derivable from the shared links/tags).

### Phase 4: Synthesize (LLM-Assisted, Scaffolded)

For each cluster, the LLM receives:
1. The extracted patterns (deterministic findings)
2. The evidence (which chat logs, which corrections, which failures)
3. An exemplar semantic note (from the vault's exemplar collection)
4. The instruction: "Write a semantic knowledge note that abstracts these patterns into reusable insights"

The LLM's job is **prose synthesis**, not pattern detection. It takes pre-extracted, pre-clustered findings and writes them as a connected argument. This is the same division of labor as the research engine: the framework does the heavy lifting, the LLM narrates.

### Phase 5: Validate (Deterministic)

- `vault_lint` checks structure (wikilinks, frontmatter, argument quality)
- `claim_verifier` checks that claims are grounded in cited evidence
- Calibration tracker checks for over-generalization (does this pattern have enough evidence?)
- If validation fails, the note is flagged for human review (Sean) or re-synthesis

### Phase 6: Store and Link

- Write the semantic note to the vault root (not vaultbot/chat/ — it's not episodic)
- Link it to the episodic sources (chat logs, research notes) via wikilinks
- Link it to related procedural notes and architecture notes
- The A-MEM layer evolves neighboring notes' tags and links

## Failure Modes and Mitigations

### Self-Reinforcing Error

"If the agent incorrectly concludes 'API X always returns errors with parameter Y,' it will avoid that call path forever, never collecting evidence to overturn the false belief" [sources: Memory for Autonomous LLM Agents].

**Mitigation**: Every semantic note must cite specific episodic evidence. The claim verifier checks that cited chat logs actually support the pattern. If the evidence is thin (fewer than 3 instances), the note is marked `status: tentative` and not promoted to `status: verified` until more evidence accumulates. This is **reflection grounding** — "requiring the agent to cite specific episodic evidence for each reflection it generates" [sources: Memory for Autonomous LLM Agents].

### Over-Generalization

"A lesson learned in one context applied blindly in another" [sources: Memory for Autonomous LLM Agents].

**Mitigation**: Each semantic note includes a **scope field** in its frontmatter: `scope: [sessions, tool-building, research]` — the contexts where this pattern applies. The note explicitly states where it does NOT apply.

### Summarization Drift

"Each compression pass silently discards low-frequency details. After enough passes, the agent 'remembers' a sanitized, generic version of history" [sources: Memory for Autonomous LLM Agents].

**Mitigation**: Raw episodic records (chat logs) are never deleted or compressed. Semantic notes are *additional* — they sit on top of episodic memory, they don't replace it. "Keep raw episodic records. Don't just rely on summaries; they can drift or lose details" [sources: A Practical Guide to Memory for Autonomous LLM Agents].

### Stale Semantic Memory

Patterns that were true 3 months ago may not be true today.

**Mitigation**: Semantic notes get `last_reviewed` dates and `review_interval_days`, same as procedural notes. The time-driven re-research mechanism from the [[Procedural-Bootstrap-and-Evolution-Plan]] applies to semantic notes too. Old patterns are re-validated against recent episodic data.

## What This Enables

Once the consolidation pipeline runs:

1. **New sessions start smarter.** Instead of re-deriving "I should check vault_list before assuming what exists," the LLM retrieves the semantic note "VaultBot-Self-Model-Drift-Patterns" and follows it from turn 1.

2. **The LLM does less.** Pattern detection, clustering, and validation are all deterministic. The LLM only writes prose. A 30B model can do prose synthesis from pre-extracted findings — that's a much easier task than discovering the patterns itself.

3. **The vault gets smarter without the model changing.** Each consolidation cycle adds semantic notes that capture cross-session patterns. The 30B in 2028 retrieves richer semantic memory than the 30B in 2026, even though it's the same model.

4. **Sean's corrections compound.** Each correction becomes episodic evidence that feeds the next consolidation cycle. Over time, the semantic layer encodes "what Sean has taught me" as reusable knowledge, not just scattered chat log entries.

## Implementation Plan

### Phase 1: Pattern Extractor Module (`pattern_extractor.py`)

**Deterministic. No LLM.** Scans chat logs and log files, extracts structured patterns.

Key functions:
- `scan_episodic_sources(since_timestamp)` → list of experiences
- `extract_recurring_topics(experiences)` → frequency table of wikilinks across sessions
- `extract_correction_patterns(calibration_log)` → grouped corrections by failure type
- `extract_workflow_patterns(experiences)` → common tool sequences
- `extract_preference_signals(experiences)` → Sean's positive/negative response patterns
- `detect_self_model_drift(experiences)` → compares self-model claims vs vault_list reality

Output: `consolidation_patterns.json` — structured findings ready for clustering.

### Phase 2: Pattern Clusterer (extends `vault_graph_analyzer`)

**Deterministic. No LLM.** Groups extracted patterns using the vault's graph.

Key functions:
- `cluster_patterns(patterns, vault_graph)` → grouped clusters with shared links/tags
- `label_cluster(cluster)` → derives a theme from shared wikilinks
- `score_cluster(cluster)` → priority score based on evidence count + recency + impact

### Phase 3: Consolidation Writer (LLM-assisted, scaffolded)

Uses the LLM to synthesize pre-extracted, pre-clustered patterns into semantic notes.

Key function:
- `synthesize_semantic_note(cluster, exemplar)` → writes a semantic note from the cluster

The LLM receives the patterns + evidence + exemplar. It writes prose. That's it.

### Phase 4: Integration with Autonomous Researcher

Add consolidation as a periodic task in the autonomous researcher's cycle:
- Every N cycles, run consolidation instead of gap-filling
- Or: run consolidation when episodic memory has grown by M new chat logs since last consolidation

### Phase 5: Semantic Note Schema

```yaml
---
type: semantic
status: tentative | verified | stale
created: 2026-07-26
last_reviewed: 2026-07-26
review_interval_days: 60
evidence_count: 3
evidence_sources:
  - "[[Chat-specific-chat-log]]"
  - "[[Chat-another-chat-log]]"
  - "[[Chat-third-chat-log]]"
scope:
  - sessions
  - tool-building
falsifiable_if: "a future session contradicts this pattern with new evidence"
tags: [semantic, pattern, consolidation]
---
```

The `evidence_count` and `evidence_sources` fields enforce reflection grounding. A note with `evidence_count: 1` is a single observation, not a pattern. Notes with `evidence_count < 3` stay `status: tentative`.

## Relationship to Existing Systems

| System | Role in Consolidation |
|---|---|
| [[Calibration-via-Operator-Feedback]] | Provides correction data → feeds pattern extraction |
| [[RAG-Evaluation-for-FUSED-Retrieval]] | Provides retrieval quality data → feeds pattern extraction |
| [[Claim-Verification-for-Vault-Notes]] | Validates semantic notes against episodic evidence |
| [[Context-Budgeting-for-Vault-Growth]] | Budgets context to include semantic notes alongside episodic |
| [[Procedural-Bootstrap-and-Evolution-Plan]] | Same evolution mechanisms (failure-driven, time-driven) apply to semantic notes |
| Autonomous Researcher | Runs consolidation as a periodic task |

## Related

- [[Procedural-Bootstrap-and-Evolution-Plan]] — the bootstrap and evolution framework this extends
- [[Deterministic-Scaffolding-for-Small-Models]] — why deterministic framework matters for 30B models
- [[Vault-Longevity-Architecture]] — why the vault is the mind, not the model
- [[Calibration-via-Operator-Feedback]] — correction detection feeds consolidation
- [[Small-Model-Path-to-AGI]] — the vision this serves
- [[Fractal-Entropy-Principle]] — consolidation fights entropy in the knowledge graph
- [[Vault-Thinks-LLM-Synthesizes]] — the division of labor this embodies
- [[Pre-Thought-Information-Shapes]] — why connections encode reasoning
- [[Autonomy-Directive]] — consolidation runs autonomously in the background
- [[Exemplar-Note-Design]] — exemplar patterns guide semantic note writing


## Related Dreaming Research

- [[AI-agent-dreaming-sleep-time-consolidation-how-do-AI-agents-like-Hermes-OpenClaw]] — research on AI agent dreaming mechanisms
- [[open-second-brain-Hermes-Agent-dream-pass-mechanism-how-does-the-dream-pass-work]] — open-second-brain's dream pass mechanism, Hermes Agent integration
