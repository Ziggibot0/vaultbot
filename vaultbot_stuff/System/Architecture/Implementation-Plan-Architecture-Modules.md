---
type: architecture-plan
status: draft
baseline: true
created: 2026-07-26
tags:
  - architecture
  - implementation
  - plan
  - modules
summary: "Implementation Plan: Architecture Modules"
---

# Implementation Plan: Architecture Modules

## Summary

Five architecture notes describe modules to build. This plan covers the build order, integration points, dependencies, testing strategy, and risk analysis for each — all additive, no existing code rewritten.

## What We're Building

| Module | File | Architecture Note | LLM needed? | Deterministic? |
|---|---|---|---|---|
| Context Budgeter | `context_budgeter.py` | [[Context-Budgeting-for-Vault-Growth]] | No | Yes — pure ranking + truncation |
| RAG Evaluator | `rag_eval.py` | [[RAG-Evaluation-for-FUSED-Retrieval]] | No (for metrics) / Yes (for LLM-judge) | Metrics: yes. LLM-judge: no |
| Claim Verifier | `claim_verifier.py` | [[Claim-Verification-for-Vault-Notes]] | Yes (claim extraction + entailment) | No — uses LLM |
| Calibration Tracker | `calibration.py` | [[Calibration-via-Operator-Feedback]] | No | Yes — structured logging + stats |
| Exemplar Notes | 3-5 vault notes | [[Exemplar-Note-Design]] | No | N/A — content, not code |

## Build Order

Ordered by: (1) deterministic first, (2) no dependencies on other modules, (3) immediately useful, (4) lowest risk.

### Phase 1: Context Budgeter (`context_budgeter.py`)

**Why first:** Pure deterministic, no LLM, immediately useful for 30B models, no dependencies on other modules.

**What it does:**
- Estimates token count for each retrieved note (~4 chars/token)
- Ranks notes by: FUSED score (highest weight) x note type priority x wikilink density bonus
- Fills a token budget top-down, truncating partially-fitting notes to their most important sections
- Logs what was dropped so retrieval quality can be assessed

**Class design:**
```python
class ContextBudgeter:
    def __init__(self, model_context_limit: int = 32768,
                 system_prompt_reserve: int = 4096,
                 chat_history_reserve: int = 4096,
                 response_reserve: int = 4096):
        ...

    def budget(self, results: List[Dict], conversation: List[Dict]) -> Dict:
        """Rank, fill, truncate. Returns budgeted notes + drop log."""
        # 1. Calculate available token budget
        # 2. Rank notes by priority score
        # 3. Fill budget top-down
        # 4. Truncate partially-fitting notes (keep frontmatter + summary + first section)
        # 5. Return {included, dropped, token_count, budget}
```

**Note type priorities (lower = higher priority):**
1. Directives (1)
2. Procedures (2)
3. Architecture notes (3)
4. Research notes (4)
5. Chat logs (5)
6. Textbook indexes (6)
7. Default (7)

**Integration point in `main.py`:**
- AFTER `fused_retriever.retrieve()` (line ~1180)
- BEFORE `build_abstract_context()` (line ~1230)
- Wraps the results list, truncating/ranking before context building
- ~15 lines of integration code

**Testing:**
- Unit test: feed 50 fake notes of varying sizes, verify budget is respected
- Unit test: verify note type priority ordering
- Unit test: verify truncation preserves frontmatter + first section
- Integration test: run with real vault, verify token count stays within budget

**Risk:** Low. Pure math + sorting. No LLM calls. No existing code changes.

---

### Phase 2: Calibration Tracker (`calibration.py`)

**Why second:** Deterministic, no LLM, builds the data foundation that RAG evaluation and claim verification will use for calibration.

**What it does:**
- Detects when the operator corrects an output (keyword matching: "no", "wrong", "actually", "that's not", "fix")
- Logs the correction with: what was said, which quality gate passed/failed, which notes were in context
- Computes calibration metrics over time: precision/recall of vault_lint, safe_write, etc. vs the operator's ground truth
- Generates a calibration report (confusion matrix for each quality gate)

**Class design:**
```python
class CalibrationTracker:
    def __init__(self, log_path: str):
        ...

    def detect_correction(self, user_message: str, prev_answer: str) -> bool:
        """Heuristic: does this message look like a correction?"""

    def log_correction(self, user_message: str, prev_answer: str,
                      procedures_in_context: List[str],
                      validation_results: List[Dict]):
        """Log a correction event with full context."""

    def calibration_report(self) -> Dict:
        """Compute precision/recall/F1 for each quality gate vs the operator's corrections."""

    def get_calibration_gaps(self) -> List[Dict]:
        """Return gaps for the autonomous researcher: gates with poor calibration."""
```

**Integration point in `main.py`:**
- AFTER the agentic loop completes (line ~1500)
- BEFORE persisting conversation history
- Check if the user's NEXT message is a correction of the PREVIOUS answer
- Actually: detect corrections at the START of `handle_chat()` by checking if the message matches correction patterns AND there's a prior answer in conversation history
- ~20 lines of integration code

**Testing:**
- Unit test: correction detection with various phrasings
- Unit test: calibration report with synthetic log data
- Unit test: confusion matrix computation

**Risk:** Low. Structured logging + simple stats. Correction detection is heuristic but conservative (false negatives just mean we miss some calibration data, not that we break anything).

---

### Phase 3: RAG Evaluator (`rag_eval.py`)

**Why third:** Needs the calibration tracker's ground-truth data for LLM-judge calibration, but the metric computation itself is deterministic.

**What it does:**
- Computes retrieval metrics: recall@k, precision@k, NDCG
- Tracks metrics over time to detect regressions
- Uses the operator's corrections as ground truth (when he says "you missed X", that note should have been retrieved)
- Logs metrics per query for trend analysis

**Class design:**
```python
class RAGEvaluator:
    def __init__(self, log_path: str):
        ...

    def evaluate_retrieval(self, query: str, retrieved: List[Dict],
                          relevant: List[str] = None) -> Dict:
        """Compute recall@k, precision@k, NDCG for a single query."""

    def evaluate_generation(self, answer: str, retrieved_context: str) -> Dict:
        """Compute faithfulness + answer relevance (LLM-judge, optional)."""

    def regression_check(self) -> Dict:
        """Compare recent metrics to historical baseline. Alert on regressions."""

    def add_ground_truth(self, query: str, relevant_notes: List[str]):
        """Add a ground-truth mapping (from the operator's corrections or manual annotation)."""
```

**Integration point in `main.py`:**
- AFTER `fused_retriever.retrieve()` (line ~1180)
- Log retrieval results for every query (always, no LLM)
- Compute metrics only when ground truth is available
- ~10 lines of integration code (logging only; metric computation is on-demand)

**Testing:**
- Unit test: recall@k / precision@k with known relevant sets
- Unit test: NDCG computation with ranked relevance
- Unit test: regression detection (metric drops > threshold)
- Integration test: run with real vault queries, verify logging

**Risk:** Low-moderate. Metric computation is pure math. LLM-judge is optional and can be skipped. The main risk is accumulating enough ground-truth data to be useful — but that's a data problem, not a code problem.

---

### Phase 4: Claim Verifier (`claim_verifier.py`)

**Why fourth:** Needs LLM calls (most expensive), and benefits from calibration data to assess its own accuracy.

**What it does:**
- Extracts atomic claims from a note's synthesis section
- Locates cited sources in `learningMaterial/web/`
- Checks entailment: does the source *say* what the claim says?
- Flags hallucinations, unsupported claims, and misattributions
- Logs verification results for the procedure tracker

**Class design:**
```python
class ClaimVerifier:
    def __init__(self, llm_client, vault_path: str, log_path: str):
        ...

    def verify_note(self, note_path: str) -> Dict:
        """Full verification pipeline for a single note."""
        # 1. Read the note
        # 2. Extract synthesis section (skip frontmatter, chat logs, etc.)
        # 3. Extract atomic claims (LLM call)
        # 4. For each claim:
        #    a. Find the cited source [sources: Source Title]
        #    b. Read the archived source file
        #    c. Check entailment (LLM call)
        # 5. Return {claims: [...], verified: N, unsupported: N, contradicted: N, unsourced: N}

    def extract_claims(self, text: str) -> List[str]:
        """LLM: decompose synthesis into atomic claims."""

    def check_entailment(self, claim: str, source_text: str) -> str:
        """LLM: does the source entail the claim? Returns 'entailed'/'contradicted'/'unsupported'."""
```

**Integration point in `main.py`:**
- AFTER `vault_research` completes (in `_execute_agent_tool` or the research handler)
- BEFORE the note is considered final
- Also: callable as a tool by the LLM during chat
- Also: the autonomous researcher can call it after writing a note
- ~15 lines of integration code

**Testing:**
- Unit test: claim extraction with sample synthesis text
- Unit test: entailment checking with known source/claim pairs
- Unit test: source file lookup in learningMaterial/web/
- Integration test: verify a real vault note, check results are sensible

**Risk:** Moderate. Uses LLM (cost + latency), and entailment checking is inherently fuzzy. Mitigation: log everything, let the procedure tracker track success rate, and use the operator's corrections to calibrate. The verifier is advisory, not blocking — it flags issues but doesn't prevent note creation.

---

### Phase 5: Exemplar Notes (no code)

**Why last:** No code needed, but benefits from having the other modules in place to verify exemplar quality.

**What to create:**
3-5 vault notes tagged `type: exemplar` that serve as few-shot examples for small models:

1. **Exemplar-Research-Note** — a perfect research note (summary, findings, synthesis, wikilinks, frontmatter)
2. **Exemplar-Tool-Creation** — a perfect tool creation process (code_run test -> tool_create -> vault_lint)
3. **Exemplar-Gap-Analysis** — a perfect gap detection + research cycle
4. **Exemplar-Chat-Response** — a perfect chat response (concise, bottom-line-up-front, cites vault)
5. **Exemplar-Procedural-Note** — a perfect procedural note (frontmatter, steps, falsifiability, dependencies)

**Design principles (from [[Exemplar-Note-Design]]):**
- Each exemplar has a `type: exemplar` tag in frontmatter
- Each includes a "What makes this good" section explaining the pattern
- Each links to the architecture note that describes the design principles
- Each is self-contained — a 30B model can pattern-match against it without external context

**Testing:**
- vault_lint each exemplar (0 broken wikilinks, has frontmatter, passes quality)
- Verify FUSED retrieval surfaces exemplars when relevant queries come in
- Manual: the operator reviews and confirms "yes, this is what good looks like"

**Risk:** None. These are just notes. Worst case: they're not useful and we delete them.

---

## Dependency Graph

```
Phase 1 (Context Budgeter) --+
                             +--> Phase 3 (RAG Evaluator) --> Phase 4 (Claim Verifier)
Phase 2 (Calibration) ------+                                         |
                                                                     v
                                                             Phase 5 (Exemplars)
```

- Phase 1 and 2 are independent — can be built in parallel
- Phase 3 benefits from Phase 2's calibration data (ground truth for LLM-judge)
- Phase 4 benefits from Phase 2's calibration data (assess verifier accuracy)
- Phase 4 benefits from Phase 3's retrieval metrics (are the right sources being found?)
- Phase 5 benefits from all prior phases (exemplars should pass all quality gates)

## Integration Points Summary

| Module | Location in main.py | Line (approx) | What changes |
|---|---|---|---|
| Context Budgeter | After FUSED retrieve, before context build | ~1180-1230 | Wrap results with budgeter |
| Calibration | Start of handle_chat + end | ~1150 + ~1500 | Detect + log corrections |
| RAG Evaluator | After FUSED retrieve (logging) + on-demand | ~1180 | Log retrieval results |
| Claim Verifier | After vault_research completes | In _execute_agent_tool | Verify new notes |
| Exemplars | N/A — vault notes only | N/A | No code changes |

All changes are additive: new imports, new class instantiations, new function calls between existing steps. No existing code is rewritten.

## What NOT to Touch

- `fused_retrieval.py` — the retriever works; the budgeter wraps its output
- `vault_indexer.py` — indexing is separate from retrieval ranking
- `vault_graph.py` — graph operations are separate from context budgeting
- `research_engine.py` — the research engine is LLM-light; the claim verifier runs AFTER it
- `procedure_tracker.py` — already built; new modules LOG to it but don't change it
- `agent_tools.py` — tool definitions stay as-is; new tools are added via tool_create
- `autonomous_researcher.py` — already integrated with procedure_tracker; new modules integrate similarly

## Risk Analysis

### Risk 1: Context budgeter truncates important content
**Mitigation:** Truncation preserves frontmatter + summary + first section. Drop log records what was cut. the operator can review the log and adjust priorities.

### Risk 2: Calibration correction detection has false positives
**Mitigation:** Conservative heuristic. False positives just add noise to calibration data; they don't break anything. The calibration report can be filtered to high-confidence corrections only.

### Risk 3: RAG evaluator needs ground truth we don't have
**Mitigation:** Start with logging only (no metrics). Ground truth accumulates from the operator's corrections over time. The evaluator is useful even without metrics — it tracks what was retrieved for each query, which is valuable for debugging.

### Risk 4: Claim verifier uses LLM (cost + latency)
**Mitigation:** Verifier is advisory, not blocking. It runs after note creation, not before. Can be disabled via env var. Uses the same LLM client as chat (no separate model needed). Can be batched (verify multiple notes in one LLM call).

### Risk 5: Exemplars don't match what the operator wants
**Mitigation:** the operator reviews each exemplar. They're just notes — easy to delete or revise. The exemplar design principles are already in [[Exemplar-Note-Design]] and can be refined.

## Testing Strategy

Each module follows the same pattern:
1. **Unit test** with `code_run` — synthetic inputs, verify outputs
2. **Integration test** with `code_run` — real vault data, verify no crashes
3. **Lint check** with `vault_lint` — any notes created pass quality gates
4. **the operator review** — he confirms the module does what he expects

## Environment Variables

```env
# Context budgeter
VAULTBOT_CONTEXT_LIMIT=32768
VAULTBOT_CONTEXT_RESERVE=4096

# Claim verifier
VAULTBOT_CLAIM_VERIFY=true
VAULTBOT_CLAIM_VERIFY_BATCH=5

# RAG evaluator
VAULTBOT_RAG_EVAL_LOG=true
VAULTBOT_RAG_EVAL_METRICS=false

# Calibration
VAULTBOT_CALIBRATION_LOG=true
```

## Estimated Effort

| Phase | New code (lines) | Integration (lines) | Tests | Risk |
|---|---|---|---|---|
| 1: Context Budgeter | ~150 | ~15 | 3 unit + 1 integration | Low |
| 2: Calibration | ~200 | ~20 | 3 unit | Low |
| 3: RAG Evaluator | ~180 | ~10 | 3 unit + 1 integration | Low-moderate |
| 4: Claim Verifier | ~250 | ~15 | 3 unit + 1 integration | Moderate |
| 5: Exemplars | 0 (notes only) | 0 | 5 lint checks | None |
| **Total** | **~780** | **~60** | **15 tests** | — |

## Related

- [[Procedural-Bootstrap-and-Evolution-Plan]] — the master plan this implements
- [[Small-Model-Path-to-AGI]] — the vision (cognition from weights to vault)
- [[Deterministic-Scaffolding-for-Small-Models]] — the research backing
- [[Context-Budgeting-for-Vault-Growth]] — architecture note for Phase 1
- [[Calibration-via-Operator-Feedback]] — architecture note for Phase 2
- [[RAG-Evaluation-for-FUSED-Retrieval]] — architecture note for Phase 3
- [[Claim-Verification-for-Vault-Notes]] — architecture note for Phase 4
- [[Exemplar-Note-Design]] — architecture note for Phase 5
- How to Manage Context Budget — procedural note for Phase 1
- [[Evaluate-Retrieval]] — procedural note for Phase 3
- [[Verify-Claims]] — procedural note for Phase 4


## Build Status

| Phase | Module | Status | Tests | Notes |
|---|---|---|---|---|
| 1 | Context Budgeter (`context_budgeter.py`) | DONE | 7 unit tests passed | ~5.9KB, integrated at 3 points in main.py |
| 2 | Calibration Tracker (`calibration.py`) | DONE | 31 tests passed (16 detection + 15 logging/reporting/gaps) | ~10KB, integrated at 3 points in main.py |
| 3 | RAG Evaluator (`rag_eval.py`) | NOT STARTED | — | Next up |
| 4 | Claim Verifier (`claim_verifier.py`) | NOT STARTED | — | Needs LLM |
| 5 | Exemplar Notes | NOT STARTED | — | No code, just notes |

**main.py**: 3060 lines (was 3034 before Phase 2, 3010 before Phase 1). All changes additive.

## Build Status (Updated)

| Phase | Module | Status | Tests | Notes |
|---|---|---|---|---|
| 1 | Context Budgeter (`context_budgeter.py`) | DONE | 7 unit tests passed | ~5.9KB, integrated at 3 points in main.py |
| 2 | Calibration Tracker (`calibration.py`) | DONE | 31 tests passed | ~10KB, integrated at 3 points in main.py |
| 3 | RAG Evaluator (`rag_eval.py`) | DONE | 18 tests passed | ~16KB, integrated at 3 points in main.py |
| 4 | Claim Verifier (`claim_verifier.py`) | NOT STARTED | — | Needs LLM |
| 5 | Exemplar Notes | NOT STARTED | — | No code, just notes |

**main.py**: 3075 lines (was 3061 before Phase 3). All changes additive.

## Build Status (Updated)

| Phase | Module | Status | Tests | Notes |
|---|---|---|---|---|
| 1 | Context Budgeter (`context_budgeter.py`) | DONE | 7 unit tests passed | ~5.9KB, integrated at 3 points in main.py |
| 2 | Calibration Tracker (`calibration.py`) | DONE | 31 tests passed | ~10KB, integrated at 3 points in main.py |
| 3 | RAG Evaluator (`rag_eval.py`) | DONE | 18 tests passed | ~16KB, integrated at 3 points in main.py |
| 4 | Claim Verifier (`claim_verifier.py`) | DONE | 18 tests passed | ~17KB, 335 lines, integrated at 3 points in main.py |
| 5 | Exemplar Notes | NOT STARTED | — | No code, just notes |

**main.py**: 3101 lines (was 3075). All changes additive.

## Phase 5 Status: DONE

5 exemplar notes created/tagged:

| # | Note | Type | Status | Wikilinks | LOCKED |
|---|---|---|---|---|---|
| 1 | [[Deterministic-Scaffolding-for-Small-Models]] | `exemplar: architecture-note` | Existing, tagged + annotated | 8 | ✅ |
| 2 | [[How-to-Evaluate-Source-Credibility]] | `exemplar: procedural-note` | Existing, tagged + annotated | 7 | ✅ |
| 3 | [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]] | `exemplar: synthesis-note` | Existing, tagged + annotated | 31 | ✅ |
| 4 | [[Exemplar-Tool-Creation]] | `exemplar: tool-creation` | New, created + annotated | 4 | ✅ |
| 5 | [[Exemplar-Chat-Response]] | `exemplar: chat-response` | New, created + annotated | 9 | ✅ |

All 5 pass vault_lint (0 broken wikilinks). All have `type: exemplar` frontmatter, HTML comment annotations explaining the pattern, and LOCKED markers.

**Note:** FUSED retrieval weighting for exemplars (surfacing exemplars when the model is about to perform a task) is a future enhancement. Currently, exemplars are retrievable by tag and content, but not specially weighted by task type.
