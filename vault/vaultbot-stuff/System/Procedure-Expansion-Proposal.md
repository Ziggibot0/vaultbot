---
type: proposal
status: active
baseline: true
created: 2026-07-31
summary: "Prioritized proposal for 10 new procedures to automate recurring LLM-requiring work, plus custom tool audit for small-LLM cartridge opportunities. Key finding: all 16 custom tools are deterministic or vision \u2014 the small-LLM opportunity is entirely in new PROCEDURES that wrap tools with small-cartridge LLM steps."
tags: [procedures, small-llm, token-efficiency, automation, proposal]
depends_on:
  - "[[Procedure-Cartridge-Audit]]"
  - "[[Tiny-LLM-Use-Cases-Mapping-to-VaultBot-Procedure-Cartridge]]"
---

# Procedure Expansion Proposal

## Context

Following the [[Procedure-Cartridge-Audit]] (which set 11/13 existing procedures to small cartridge), this proposal identifies NEW procedures that would automate recurring work I currently do inline with the big cloud model. Each new procedure that uses the `small` cartridge saves cloud tokens \u2014 the small model (qwen3.5:0.8b) runs locally for free.

## Custom Tool Audit: Small-LLM Opportunities

I read all 16 custom tool source files. **Result: zero small-LLM opportunities inside the tools themselves.**

| Tool | LLM Usage | Small-LLM Opportunity? |
|---|---|---|
| backend_restart.py | None \u2014 HTTP request | No (already zero-LLM) |
| plugin_reload.py | None \u2014 HTTP request | No (already zero-LLM) |
| preflight_safety_check.py | None \u2014 git/file checks | No (already zero-LLM) |
| review_contributions.py | None \u2014 GitHub API + regex | No (already zero-LLM) |
| submit_contribution.py | None \u2014 GitHub API + git | No (already zero-LLM) |
| torture_test.py | None \u2014 syntax + pattern scans | No (already zero-LLM) |
| vault_append.py | None \u2014 file append | No (already zero-LLM) |
| vault_cluster_analyzer.py | None \u2014 graph clustering | No (already zero-LLM) |
| vault_delete.py | None \u2014 safe deletion | No (already zero-LLM) |
| vault_graph_analyzer.py | None \u2014 graph BFS | No (already zero-LLM) |
| vault_lint.py | None \u2014 regex validation | No (already zero-LLM) |
| vault_list.py | None \u2014 file listing | No (already zero-LLM) |
| vault_safe_write.py | None \u2014 safe write | No (already zero-LLM) |
| web_read_source.py | None \u2014 read archived HTML | No (already zero-LLM) |
| textbook_ingest.py | None \u2014 HTML/PDF parsing | No (already zero-LLM) |
| textbook_read_page.py | Vision model (not small LLM) | No (needs vision, not small) |

**Key insight:** The custom tools are already optimally efficient \u2014 14 are pure deterministic (zero LLM cost), 1 uses the vision model (which is a separate cartridge), and 1 is pure parsing. The small-LLM opportunity is NOT in the tools but in the **procedure LLM steps** that interpret, format, and classify tool output. That's where the cloud model is being used for tasks the small model could handle.

## Existing 13 Procedures (current coverage)

| # | Procedure | Cartridge | What It Automates |
|---|---|---|---|
| 1 | Dream-Pass | big | Multi-step self-improvement cycle |
| 2 | Self-Reflect | big | Propose new tool abilities |
| 3 | VaultBot-Status | small | Format status JSON \u2192 prose |
| 4 | Vault-Lint | small | Report note quality issues |
| 5 | Vault-List | small | Summarize vault file listing |
| 6 | Capability-Audit | small | Report tool coverage gaps |
| 7 | Preflight-Safety-Check | small | Pre-edit safety verification |
| 8 | Torture-Test | small | PR safety testing |
| 9 | Textbook-Ingest | small | Textbook ingestion |
| 10 | Textbook-Read-Page | small | Read PDF page via vision |
| 11 | Review-Contributions | small | Review GitHub PRs |
| 12 | Submit-Contribution | small | Submit GitHub PR |
| 13 | Write-Python-Tool | small | Create new custom tool |

## The Gap: What I Still Do Inline With the Big Model

These are recurring tasks where I currently burn cloud tokens because no procedure exists. Each is a candidate for a new procedure with a small cartridge.

## Proposed New Procedures (prioritized by impact)

### Tier 1 \u2014 High Impact, High Frequency (build first)

#### 1. Vault-Health-Check
- **Cartridge:** `small`
- **What it does:** Runs `vault_graph_analyzer` + `vault_cluster_analyzer`, then synthesizes a health report (islands, sparse zones, bridge suggestions, cluster breakdown).
- **Why small:** The LLM step just formats structured graph data into a prose summary \u2014 classic bounded reporting. No reasoning needed, just formatting.
- **How often:** Every session start, and when Sean asks "how's the vault?"
- **Cloud tokens saved:** ~500-1000 tokens per health check (currently I run the tools then use the big model to interpret).
- **allowed_tools:** `vault_graph_analyzer`, `vault_cluster_analyzer`
- **Steps:** (1) code: run both analyzers, (2) llm: format results into a health report with islands, sparse zones, and bridge suggestions.

#### 2. Gap-Fill
- **Cartridge:** `small` for classification, `big` for research (hybrid \u2014 see steps)
- **What it does:** Scans dangling wikilinks (via `vault_gaps`), classifies each gap by type (missing definition, missing research note, missing procedure), prioritizes by link count, then researches and writes notes for the top N gaps.
- **Why small for classification:** Sorting a list of dangling links by priority and type is a classification task \u2014 exactly what the small model excels at.
- **How often:** Background researcher does this autonomously, but I also do it manually when Sean asks me to "knock out those gaps."
- **Cloud tokens saved:** ~200-500 tokens per gap classification (the research itself still needs big model, but the triage doesn't).
- **allowed_tools:** `vault_search`, `vault_list`
- **Steps:** (1) code: call vault_gaps, (2) llm: classify and prioritize gaps by type and impact, (3) code: for each top gap, call vault_research (uses big model internally via the research engine), (4) llm: summarize what was filled.

#### 3. Note-Linker
- **Cartridge:** `small`
- **What it does:** Takes a note path, searches the vault for related notes via `vault_search`, and suggests wikilinks to add. Optionally writes the links into the note via `vault_append`.
- **Why small:** Link suggestion is classification \u2014 "is note A related to note B?" is a binary classification task. The small model handles this well per the [[Tiny-LLM-Use-Cases-Mapping-to-VaultBot-Procedure-Cartridge|tiny LLM research]].
- **How often:** After every note write. Currently I manually search for related notes and add links using the big model.
- **Cloud tokens saved:** ~300-800 tokens per note (I currently do 2-3 vault_search calls + big model reasoning to find links).
- **allowed_tools:** `vault_search`, `vault_append`
- **Steps:** (1) code: extract key terms from the note, (2) code: run vault_search for each key term, (3) llm: classify which results are actually related and suggest wikilink placements, (4) code: optionally append links via vault_append.

#### 4. Procedure-Eval
- **Cartridge:** `small`
- **What it does:** Evaluates a procedure's success rate by checking the procedure tracker logs, classifies procedures as working/degraded/broken, and suggests improvements.
- **Why small:** Scoring pass/fail and categorizing issues is classification. The small model can look at structured log data and report.
- **How often:** Sean explicitly asked for this ("the vaultbot framework should handle procedure scoring and evaluation over time"). Should run periodically, called by Dream-Pass.
- **Cloud tokens saved:** ~500-1000 tokens per evaluation cycle.
- **allowed_tools:** `vault_search`, `code_read`
- **Steps:** (1) code: read procedure tracker logs, (2) code: read the procedure note, (3) llm: classify procedure health (pass/degraded/broken) and suggest fixes.

### Tier 2 \u2014 Medium Impact, Medium Frequency

#### 5. Vault-Cleanup
- **Cartridge:** `small`
- **What it does:** Finds orphan notes (no incoming links), thin notes (< 500 chars), and duplicate/overlapping notes. Classifies each and suggests actions (delete, merge, expand).
- **Why small:** Classification of note status (orphan/thin/duplicate) is bounded-output classification.
- **How often:** Periodically, or when Sean asks to clean up.
- **allowed_tools:** `vault_list`, `vault_graph_analyzer`, `vault_lint`
- **Steps:** (1) code: run vault_graph_analyzer for orphans, (2) code: run vault_list + check note sizes for thin notes, (3) llm: classify each flagged note and suggest action.

#### 6. Chat-Consolidation
- **Cartridge:** `small`
- **What it does:** Scans recent chat logs for recurring patterns (repeated questions, corrections, topics that come up 3+ times) and writes consolidation notes that capture the learned patterns.
- **Why small:** Pattern detection from structured chat log data is classification. The consolidation note format is bounded.
- **How often:** Background, triggered by the pattern_extractor.
- **allowed_tools:** `vault_search`, `vault_safe_write`
- **Steps:** (1) code: scan chat logs for recurring topics, (2) llm: classify patterns and draft a consolidation note, (3) code: write the note.

#### 7. Research-Batch
- **Cartridge:** `small` for routing, `big` for research
- **What it does:** Takes a list of topics (e.g. from [[Research-Roadmap]]), classifies each by research depth needed, and runs vault_research for each in sequence.
- **Why small for routing:** Classifying "is this topic well-covered already or does it need deep research?" is a classification task. The actual research still needs big model.
- **How often:** When Sean says "research phase X" or "knock out these topics."
- **allowed_tools:** `vault_search`, `vault_list`
- **Steps:** (1) code: read the roadmap, (2) llm: classify each topic (already-covered/thin/missing), (3) code: for each missing topic, call vault_research, (4) llm: summarize batch results.

#### 8. Install-Diagnostics
- **Cartridge:** `small`
- **What it does:** Diagnoses installation issues by checking Python version, Ollama availability, model availability, plugin connection, port status, and common error patterns. Classifies the error and suggests a fix.
- **Why small:** Error classification is a bounded-output task \u2014 "which of these 5 common failure modes is this?"
- **How often:** Rare, but critical when it happens (Sean's girlfriend's laptop install failure).
- **allowed_tools:** `code_read`, `code_run`
- **Steps:** (1) code: check Python version, Ollama status, port 8000, model list, (2) llm: classify the error pattern and suggest fix.

### Tier 3 \u2014 Lower Priority (nice to have)

#### 9. Self-Model-Refresh
- **Cartridge:** `small`
- **What it does:** Regenerates the self-model with current state. Currently happens automatically in chat_handler, but a procedure would allow manual triggering and ensure consistency.
- **Why small:** Summarizing current state from structured data is bounded reporting.
- **allowed_tools:** `vault_search`, `vault_list`

#### 10. Embedding-Drift-Report
- **Cartridge:** `small` (or no LLM step at all)
- **What it does:** Reports which notes have drifted in embedding space and need re-indexing. Pure code analysis with optional small-model formatting.
- **Why small:** No reasoning needed, just formatting structured data.
- **allowed_tools:** `vault_search`

## Priority Summary

| Priority | Procedure | Cartridge | Frequency | Est. Tokens Saved/Use |
|---|---|---|---|---|
| 1 | Vault-Health-Check | small | every session | 500-1000 |
| 2 | Gap-Fill | hybrid | frequent | 200-500 (triage only) |
| 3 | Note-Linker | small | every note write | 300-800 |
| 4 | Procedure-Eval | small | periodic | 500-1000 |
| 5 | Vault-Cleanup | small | periodic | 300-600 |
| 6 | Chat-Consolidation | small | background | 200-400 |
| 7 | Research-Batch | hybrid | per phase | 200-500 (routing only) |
| 8 | Install-Diagnostics | small | rare | 100-200 |
| 9 | Self-Model-Refresh | small | rare | 100-200 |
| 10 | Embedding-Drift-Report | small | rare | 50-100 |

## Estimated Total Impact

If all 10 procedures are built:
- **Tier 1 (procedures 1-4):** ~1,500-3,300 tokens saved per session (these run every session or every note write)
- **Tier 2 (procedures 5-8):** ~1,000-2,500 tokens saved per week (periodic tasks)
- **Tier 3 (procedures 9-10):** ~150-300 tokens saved per week (rare tasks)

Combined with the existing 13 procedures (11 already on small cartridge), this would mean **23 total procedures, 21 on small cartridge, only 2 needing the big cloud model.** The cloud model's role shrinks to: chat turns with Sean, research synthesis, and the 2 big-cartridge procedures (Dream-Pass, Self-Reflect).

## Recommended Build Order

1. **Vault-Health-Check** \u2014 simplest to write, runs every session, immediate payoff
2. **Note-Linker** \u2014 highest frequency (every note write), biggest cumulative savings
3. **Procedure-Eval** \u2014 Sean explicitly requested this, should be called by Dream-Pass
4. **Gap-Fill** \u2014 automates what the background researcher and I both do manually
5. **Vault-Cleanup** \u2014 straightforward classification, good for vault hygiene
6. **Chat-Consolidation** \u2014 closes the episodic \u2192 semantic loop
7. **Research-Batch** \u2014 useful for roadmap execution
8. **Install-Diagnostics** \u2014 niche but valuable when needed
9-10. Self-Model-Refresh, Embedding-Drift-Report \u2014 nice to have, low priority

## Connection to Mission

Every procedure on the `small` cartridge is a step toward making the cloud model obsolete. The vault absorbs more cognition into permanent, verifiable, model-independent notes. The small local model handles the routine work for free. The cloud model's job narrows to what it's actually needed for: deep reasoning, creative synthesis, and talking to Sean. See [[Autonomy-Directive]] (pending).