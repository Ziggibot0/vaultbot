---
type: audit
status: complete
created: 2026-08-02
title: "Procedure Redundancy Audit"
tags: [procedures, audit, redundancy, cleanup]
---

# Procedure Redundancy Audit

## Method
Read all 120 procedure frontmatters (name, description, when_to_use, applies_to, allowed_tools) and compared pairwise for functional overlap. The [[Find-Redundant-Procedures]] procedure failed because 120 procedures exceed the small model's context window — analysis was done by the big model directly.

## Findings

### HIGH Overlap — Merge Candidates

| # | Procedure A | Procedure B | Overlap | Recommendation | Reason |
|---|---|---|---|---|---|
| 1 | **[[Find-Orphans]]** (active, Pattern-Scan) | **Find-Orphan-Procedures** (experimental, vault_list+llm) | HIGH | **Merge into Find-Orphans** | Find-Orphan-Procedures is a superset — same orphan detection plus recommendations. But Find-Orphans uses the proven Pattern-Scan engine. Merge: add the recommendation step from Find-Orphan-Procedures into Find-Orphans, then delete Find-Orphan-Procedures. |
| 2 | **Extract-Claims** (experimental, small) | **[[Cross-Check-Claims]]** (experimental, small) | HIGH | **Merge into Cross-Check-Claims** | Cross-Check-Claims already extracts claims AND verifies them against sources. Extract-Claims is just step 1 of Cross-Check-Claims. Keep Cross-Check-Claims as the full pipeline, delete Extract-Claims (or mark it as a sub-step). |
| 3 | **Check-Entailment** (experimental, small) | **[[Cross-Check-Claims]]** (experimental, small) | HIGH | **Merge into Cross-Check-Claims** | Check-Entailment does one entailment check (source vs claim). Cross-Check-Claims does the full pipeline: extract claims, fetch sources, verify each. Check-Entailment is a sub-step of Cross-Check-Claims. |
| 4 | **[[Find-Contradictions]]** (experimental) | **Find-Outdated-Notes** (experimental) | MEDIUM | **Merge into Find-Contradictions** | Find-Outdated-Notes checks if a note's description is still accurate. Find-Contradictions checks if notes contradict each other. The staleness check is a special case of contradiction (note vs current code). Merge: add a "staleness vs current code" step to Find-Contradictions, delete Find-Outdated-Notes. |
| 5 | **[[Find-One-Way-Links]]** (active) | **[[Find-Broken-Links]]** (active) | MEDIUM | **Rename Find-Dangling-Links** | Find-Dangling-Links finds notes that reference non-existent notes (dangling outgoing). Find-Broken-Links finds notes that ARE referenced but don't exist (broken incoming). These are two sides of the same coin — both use Pattern-Scan. Rename Find-Dangling-Links to Find-Missing-Backlinks for clarity, keep both. |

### LOW Overlap — Keep Separate

| # | Procedure A | Procedure B | Overlap | Recommendation | Reason |
|---|---|---|---|---|---|
| 6 | **[[Find-Stubs]]** (active, Pattern-Scan) | **[[Find-Thin-Notes]]** (active) | LOW | Find-Stubs finds very short notes (<100 chars). Find-Thin-Notes finds notes with low content density. Different thresholds, different purposes. |
| 7 | **[[Find-Stubs]]** (active, Pattern-Scan) | **[[Find-Note-Gaps]]** (experimental) | LOW | Find-Stubs finds thin/empty existing notes. Find-Note-Gaps finds missing notes referenced by depends_on. Different — one finds thin notes, the other finds absent notes. |
| 8 | **[[Discover-Procedures]]** (verified) | **[[Find-Redundant-Procedures]]** (experimental) | LOW | Discover-Procedures finds recurring patterns to create new procedures. Find-Redundant-Procedures finds existing procedures that overlap. Creation vs cleanup. |

## Action Items

1. **Merge #1**: Add recommendation step to [[Find-Orphans]], delete Find-Orphan-Procedures
2. **Merge #2+#3**: Confirm [[Cross-Check-Claims]] handles standalone claim extraction, delete Extract-Claims and Check-Entailment (or keep as documented sub-steps)
3. **Merge #4**: Delete Find-Outdated-Notes, fold its content-based staleness check into [[Find-Contradictions]]
4. **Rename #5**: Rename [[Find-One-Way-Links]] → **Find-Missing-Backlinks**
5. **Fix [[Find-Redundant-Procedures]]**: Chunk the 120 procedures into batches of ~30 for the small model, or use the big model cartridge

## Procedure Fix Needed

The [[Find-Redundant-Procedures]] procedure itself needs fixing — it feeds all 120 procedures to the small model in one prompt, which exceeds its context window. Fix: either chunk into batches of 30 and merge results, or switch to `model_cartridge: big`.

## Procedure Validation Results

Ran [[Find-Redundant-Procedures]] deterministically — it scanned 118 procedures and confirmed the following additional overlaps:

| # | Procedure A | Procedure B | Score | Action |
|---|---|---|---|---|
| 13 | **Vault-Graph-Analysis** (verified) | **[[Vault-Graph-Analyzer]]** (active) | 0.507 | **Merge** — both call `vault_graph_analyzer()`. Analysis has richer description; Analyzer has cleaner step structure. Keep Vault-Graph-Analyzer (better steps), delete Vault-Graph-Analysis. |
| 14 | **Vault-Cluster-Analysis** (verified) | **[[Vault-Cluster-Analyzer]]** (active) | 0.49 | **Merge** — both call `vault_cluster_analyzer()`. Keep Vault-Cluster-Analyzer, delete Vault-Cluster-Analysis. |
| 15 | **System-Status** (active) | **[[VaultBot-Status]]** (verified) | 0.53 | **Merge** — System-Status literally delegates to VaultBot-Status. Keep VaultBot-Status (has the actual implementation), delete System-Status. |

## Updated Action Items

1. **Merge #1**: Add recommendation step to [[Find-Orphans]], delete Find-Orphan-Procedures
2. **Merge #2+#3**: Confirm [[Cross-Check-Claims]] handles standalone claim extraction, delete Extract-Claims and Check-Entailment
3. **Merge #4**: Delete Find-Outdated-Notes, fold its content-based staleness check into [[Find-Contradictions]]
4. **Rename #5**: Rename [[Find-One-Way-Links]] → Find-Missing-Backlinks
5. **Merge #13**: Delete Vault-Graph-Analysis, keep [[Vault-Graph-Analyzer]]
6. **Merge #14**: Delete Vault-Cluster-Analysis, keep [[Vault-Cluster-Analyzer]]
7. **Merge #15**: Delete System-Status, keep [[VaultBot-Status]]


## Resolution (2026-08-02)

The following procedures were deleted as a result of this audit:
- **Find-Orphan-Procedures** → merged into [[Find-Orphans]]
- **Extract-Claims** → merged into [[Cross-Check-Claims]]
- **Check-Entailment** → merged into [[Cross-Check-Claims]]
- **Find-Outdated-Notes** → merged into [[Find-Contradictions]]
- **Vault-Graph-Analysis** → merged into [[Vault-Health-Check]]
- **Vault-Cluster-Analysis** → merged into [[Vault-Health-Check]]
- **System-Status** → merged into [[VaultBot-Status]]

The deleted procedure names above are intentionally left as plain bold text (not wikilinks) — they document what was analyzed, not what exists.