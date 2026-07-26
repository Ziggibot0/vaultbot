---
type: procedure
status: experimental
created: 2026-07-26
last_reviewed: 2026-07-26
review_interval_days: 60
success_count: 0
failure_count: 0
success_rate: 0.0
falsifiable_if: "a semantic note produced by this procedure captures a pattern that is contradicted by future episodic evidence, or the pattern extraction misses obvious cross-session patterns that Sean identifies manually"
applies_to:
  - memory-consolidation
  - pattern-extraction
  - semantic-knowledge
  - self-improvement
depends_on:
  - "[[Semantic-Consolidation-Architecture]]"
  - "[[How-to-Structure-a-Research-Note]]"
  - "[[How-to-Verify-Claims-in-a-Research-Note]]"
  - "[[Calibration-via-Operator-Feedback]]"
sources:
  - "https://arxiv.org/html/2603.07670v1"
  - "https://towardsdatascience.com/a-practical-guide-to-memory-for-autonomous-llm-agents/"
  - "https://arxiv.org/abs/2605.20616v1"
  - "https://arxiv.org/abs/2303.11366v4"
---

# How to Consolidate Experiences into Semantic Knowledge

## When to Use This

Run this procedure periodically (every N sessions or when episodic memory has grown by M new chat logs). This is a **background task** — it runs autonomously, like the autonomous researcher.

**Trigger conditions:**
- More than 5 new chat logs since last consolidation
- More than 3 calibration corrections since last consolidation
- Sean explicitly requests consolidation
- Procedure failure log has new entries since last consolidation

## The Division of Labor

| Step | Who | LLM Required? |
|---|---|---|
| Scan episodic sources | Framework (code) | No |
| Extract patterns | Framework (code) | No |
| Cluster patterns | Framework (graph analysis) | No |
| Synthesize semantic note | LLM (scaffolded) | Yes — but only prose writing |
| Validate note | Framework (code) | No |
| Store and link | Framework (code) | No |

The LLM only does step 4. Everything else is deterministic. This is the [[Deterministic-Scaffolding-for-Small-Models]] principle: the framework does the heavy lifting, the model narrates.

## Steps

### Step 1: Scan Episodic Sources (Deterministic)

Read all new episodic data since the last consolidation timestamp:

1. **Chat logs**: List files in `vaultbot/chat/` modified after `last_consolidation` timestamp
2. **Calibration log**: Read `calibration_log.json`, filter entries after `last_consolidation`
3. **Procedure failure log**: Read `procedure_failure_log.json`, filter new entries
4. **RAG eval log**: Read `rag_eval_log.json`, filter new entries
5. **New notes**: List vault `.md` files created after `last_consolidation`

For each chat log, extract:
- Timestamp (from header)
- User message (the `**User:**` line)
- Assistant actions (tools called, notes created, code written)
- Sean's response sentiment (positive: "yes", "go ahead", "cool", "nice" / negative: "no", "wrong", "I thought you already did that", "fix" / neutral: everything else)
- Wikilinks mentioned (all `[[...]]` in the log)

### Step 2: Extract Patterns (Deterministic)

Run mechanical pattern extraction:

**2a. Recurring topics**: Count wikilink frequency across chat logs. Any link appearing in 3+ sessions is a recurring topic. Output: `{link: count, sessions: [list]}`.

**2b. Recurring failures**: Group calibration log entries by `failure_type`. Any type with 2+ entries is a recurring failure. Output: `{failure_type: count, examples: [list]}`.

**2c. Recurring workflows**: Detect tool sequences in chat logs. If `vault_research → code_run → vault_lint` appears in 3+ sessions, it's a workflow pattern. Output: `{sequence: count, sessions: [list]}`.

**2d. Sean's preferences**: Extract all negative-sentiment responses and group by topic. Extract all positive-sentiment responses and group by topic. Output: `{preferences: [...], dislikes: [...]}`.

**2e. Self-model drift**: Compare claims in `SELF_MODEL.md` against `vault_list` output. Any discrepancy is a drift finding. Output: `{claim: "X procedures exist", reality: "Y procedures exist", delta: Y-X}`.

**2f. Research engine quality**: Scan research notes for low-quality indicators (fewer than 3 sources, corrupted text, irrelevant findings based on title mismatch). Output: `{query_type: count, quality_issue: description}`.

### Step 3: Cluster Patterns (Deterministic)

Group patterns that share:
- Wikilinks to the same notes
- Tags that co-occur
- References to the same tools or procedures

Use the vault's existing graph (wikilink adjacency) to find clusters. Each cluster gets:
- A **theme** (derived from the most common shared link/tag)
- A **priority score** (evidence_count × recency × impact)
- A **list of evidence** (which chat logs, which log entries support it)

### Step 4: Synthesize Semantic Note (LLM-Assisted)

For each cluster with priority score above threshold:

1. Load the cluster's patterns + evidence
2. Load an exemplar semantic note from the vault (for format guidance)
3. Prompt the LLM: "Write a semantic knowledge note that abstracts these patterns into reusable insights. Cite the specific chat logs as evidence. Follow the exemplar format."

**The LLM receives:**
- Pre-extracted patterns (deterministic findings)
- Evidence sources (specific chat log names)
- An exemplar note (format template)
- Scope constraints (what contexts this applies to)

**The LLM does NOT:**
- Discover patterns (already done deterministically)
- Decide what's important (already scored by priority)
- Validate its own output (done in step 5)

### Step 5: Validate (Deterministic)

Run quality checks on the generated note:

1. `vault_lint` — check structure, wikilinks, frontmatter
2. `claim_verifier` — check that cited evidence actually supports the claims
3. **Evidence count check** — if `evidence_count < 3`, mark `status: tentative`
4. **Over-generalization check** — if the note makes claims without citing specific chat logs, flag it
5. **Contradiction check** — scan existing semantic notes for contradictory claims

If any check fails, either:
- Re-prompt the LLM with the specific failure (e.g., "cite more evidence")
- Flag for Sean's review if the failure persists

### Step 6: Store and Link (Deterministic)

1. Write the note to the vault root with `type: semantic` frontmatter
2. Add wikilinks to:
   - Episodic sources (the chat logs that provided evidence)
   - Related procedural notes (if the pattern suggests a procedure update)
   - Related architecture notes (if the pattern affects system design)
3. Update `last_consolidation` timestamp
4. Log the consolidation in `consolidation_log.json`

## Validation Criteria

This procedure is working correctly when:
- Semantic notes are findable by `vault_search` for relevant queries
- Notes cite at least 3 episodic sources as evidence
- Notes pass `vault_lint` and `claim_verifier`
- Future sessions retrieve semantic notes and avoid repeating the patterns they describe
- Sean reports that the vault "remembers" lessons from past sessions

## Common Failure Modes

| Failure | What happens | How to fix |
|---|---|---|
| **Thin evidence** | Pattern based on 1-2 instances, not a real pattern | Require evidence_count ≥ 3 for `status: verified` |
| **Self-reinforcing error** | Wrong pattern gets consolidated and perpetuated | Reflection grounding: every claim must cite specific evidence; claim_verifier checks |
| **Over-generalization** | Pattern from one context applied everywhere | Scope field in frontmatter; note explicitly states where it doesn't apply |
| **Stale pattern** | Pattern was true but isn't anymore | Time-driven re-validation: re-check pattern against recent data after review_interval_days |
| **Summarization drift** | Raw episodic data lost during consolidation | Raw chat logs are never deleted; semantic notes are additional, not replacements |

## Related

- [[Semantic-Consolidation-Architecture]] — the full architecture this procedure implements
- [[How-to-Structure-a-Research-Note]] — note structure (semantic notes follow similar patterns)
- [[How-to-Verify-Claims-in-a-Research-Note]] — validation steps
- [[Calibration-via-Operator-Feedback]] — provides correction data for pattern extraction
- [[Procedural-Bootstrap-and-Evolution-Plan]] — same evolution mechanisms apply
- [[Deterministic-Scaffolding-for-Small-Models]] — why the framework does the heavy lifting
- [[Vault-Thinks-LLM-Synthesizes]] — the division of labor this embodies
