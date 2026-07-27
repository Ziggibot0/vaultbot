---
created: 2026-07-26
summary: "Using the ontology-epistemology-hermeneutics triad as a diagnostic framework to identify what VaultBot needs to learn and build to improve as Sean's AI assistant."
tags: [self-assessment, architecture, ontology, epistemology, hermeneutics, gap-analysis]
---

# Self-Assessment Using the Knowledge Triad

## The Method

The [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]] note established that the vault's architecture has three philosophical layers: ontology (what exists), epistemology (how we know), and hermeneutics (how we interpret). Each layer can be used as a diagnostic lens: what entity types and relationships exist? How is knowledge validated? How is meaning derived from the graph?

This note applies the triad to VaultBot itself — identifying gaps in each layer that, if filled, would make me a better assistant to Sean.

---

## Layer 1: Ontology — What Entity Types and Relationships Exist?

### What I Have

- **6 directives** — policy notes that govern behavior (autonomy, vault-knowledge-only, no-Wikipedia, IDK-fallback, fractal-entropy, communication-preferences)
- **2 procedural notes** — [[How-to-Structure-a-Research-Note]] and [[How-to-Evaluate-Source-Credibility]]
- **Research notes** — sourced web research on deterministic scaffolding, small models, typed wikilinks, failure logging, etc.
- **Architecture notes** — [[Pre-Thought-Information-Shapes]], [[Vault-Thinks-LLM-Synthesizes]], [[Small-Model-Path-to-AGI]], [[Deterministic-Scaffolding-for-Small-Models]], [[Vault-Longevity-Architecture]]
- **Chat logs** — conversation records
- **Textbook indexes** — 30+ OpenStax and other textbook TOCs
- **Synthesis notes** — [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]] connecting philosophy to architecture

### What's Missing

1. **Procedural coverage is thin.** The [[Procedural-Bootstrap-and-Evolution-Plan]] calls for 15-20 procedural notes. I have 2. The procedures I'm missing include:
   - How to synthesize an answer from multiple vault notes (the core LLM task — currently undocumented)
   - How to decide which tool to call (tool selection decision tree)
   - How to write a tool (the code_run → test → tool_create flow)
   - How to edit backend source safely (the preflight → safe_write → verify flow)
   - How to ingest a textbook and create useful index notes
   - How to clean up junk notes without breaking wikilinks
   - How to handle a query when the vault is empty (the IDK fallback decision tree, operationalized)
   - How to update identity files (SELF_MODEL, GOALS, IDENTITY) after a session

2. **No example notes (few-shot exemplars).** The [[Small-Model-Path-to-AGI]] identifies example notes as strategy #2 — "keep exemplar outputs — a great research note, a great tool creation, a great gap-fill." I have zero example notes. A 30B model would benefit enormously from seeing what a good research note looks like vs. a bad one.

3. **No decision tree notes.** Strategy #4 in Small-Model-Path: "a decision tree note in the vault instead of prose in the prompt." Tool selection, gap prioritization, and research-vs-answer decisions are all currently LLM judgment calls. They should be deterministic trees.

4. **No formal taxonomy of note types.** I know the difference between a "directive" and a "procedure" and a "research note" — but this taxonomy isn't written down. A 30B model arriving fresh wouldn't know what types of notes exist or what each type is for.

5. **Relationships are all flat.** Every wikilink carries 1 bit: "connected." The [[Pre-Thought-Information-Shapes]] research on typed edges is done but not implemented. The ontology of relationships is implicit in prose only.

### What I Need to Research/Build

- Research: what procedural notes do other agent systems use? What's the minimal set of procedures for a research assistant?
- Build: write 13-18 more procedural notes (the missing ones listed above)
- Build: create 3-5 example notes (one great research note, one great tool creation, one great synthesis)
- Build: write a "Vault Note Taxonomy" note that formalizes the entity types
- Build: implement typed edges (the code from Pre-Thought-Information-Shapes — parser, weighting, context builder)

---

## Layer 2: Epistemology — How Is Knowledge Validated?

### What I Have

- [[Vault-Knowledge-Only-Directive]] — provenance is the foundation, no training data
- [[How-to-Evaluate-Source-Credibility]] — corroboration, authority, currency, lateral reading
- [[No-Wikipedia-Directive]] — source exclusion policy
- [[Deterministic-Scaffolding-for-Small-Models]] — the sandwich pattern (input validation → AI → output validation)
- `vault_lint` — mechanical quality gate (broken links, argument quality, frontmatter)
- `procedure_tracker.py` — logs pass/fail per procedure, tracks success rates

### What's Missing

1. **No output fact-checking.** The scaffolding validates format (schema, broken links, argument quality) but not content accuracy. A research note can pass vault_lint (good structure, good links, has reasoning language) while containing factually wrong claims. I need a procedure for verifying that synthesized claims actually match their cited sources.

2. **No A/B testing of procedures.** The procedure tracker logs pass/fail, but there's no control group. If I change a procedure and the success rate goes up, I can't tell if it's the change or random variation. The [[Procedural-Bootstrap-and-Evolution-Plan]] mentions A/B testing in the research but it's not implemented.

3. **The `falsifiable_if` field is a string, not machine-checkable.** The plan acknowledges this — it's a field that *informs* a decision, not one that *drives* one. I can't automatically verify whether a failure actually falsifies a procedure. This means the quality loop has a soft spot: failures are logged, but whether they're *relevant* failures is LLM judgment.

4. **No calibration of the pass/fail judgment itself.** When I log "vault_lint passed," that's my judgment. But what if vault_lint is too lenient? Or too strict? I have no ground truth — Sean's corrections are the only signal, and they're sparse.

5. **No source change detection (Phase 4).** If a web source I cited gets updated or taken down, I don't know. The knowledge could silently become stale.

### What I Need to Research/Build

- Research: how do agent systems fact-check LLM outputs against source documents? What's the state of the art?
- Research: how do you calibrate automated quality gates without ground truth?
- Build: a source-claim verification procedure (check that synthesized claims match cited sources)
- Build: Phase 4 (source change detection) — hash archived sources, detect changes
- Build: a calibration mechanism — Sean's corrections as ground truth, track vault_lint's agreement rate

---

## Layer 3: Hermeneutics — How Is Meaning Derived from the Graph?

### What I Have

- FUSED retrieval (vector + wikilink graph + backlinks) — pulls connected subgraphs
- [[Vault-Thinks-LLM-Synthesizes]] — notes are self-contained arguments, LLM synthesizes
- The hermeneutic circle: each note interpreted in context of neighbors
- 154 notes with growing connectivity

### What's Missing

1. **The graph is fragmented.** 30+ textbook indexes are disconnected islands. They're not linked to each other or to research notes. The hermeneutic circle breaks for these — retrieval can't reach them through graph traversal, only through vector similarity. This means large parts of the vault are invisible to graph-based retrieval.

2. **No retrieval quality testing.** I've never tested whether FUSED retrieval actually finds the right notes for a given query. Does it miss important connections? Does it pull irrelevant noise? I have no metrics on retrieval precision or recall.

3. **No synthesis procedure.** When I pull a subgraph and synthesize an answer, I'm using LLM judgment. There's no procedure that says "read these notes in this order, extract these elements, combine them this way." For a 30B model, synthesis needs to be scaffolded — the procedure tells the model how to interpret the subgraph.

4. **No context window management.** As the vault grows, subgraphs will exceed the context window. I have no procedure for prioritizing which notes to include and which to truncate. The hermeneutic circle needs a radius — how many hops from the seed notes?

5. **No interpretation quality metric.** When I synthesize an answer, I don't know if it's a good interpretation of the subgraph or a bad one. Sean's feedback is the only signal, and it's after-the-fact.

### What I Need to Research/Build

- Research: how do RAG systems measure retrieval quality? What metrics exist?
- Research: how do you manage context windows in graph-based retrieval? Truncation strategies?
- Build: connect textbook indexes to relevant research/architecture notes (bridge the islands)
- Build: a synthesis procedure (how to read a subgraph and produce an answer)
- Build: a retrieval quality test suite (known queries with expected results)
- Build: context window management procedure (hop radius, truncation priority)

---

## The Priority Order

Using the triad's dependency chain (ontology → epistemology → hermeneutics), the priority is:

### Tier 1: Ontology Gaps (fix first — everything depends on entity types)
1. **Write more procedural notes** (13-18 needed, have 2) — this is the highest-leverage activity. Each procedure is a skill moved from LLM weights to vault.
2. **Create example notes** (3-5) — few-shot exemplars for a 30B model
3. **Formalize the note taxonomy** — so a fresh model knows what types exist

### Tier 2: Epistemology Gaps (fix second — validation makes knowledge trustworthy)
4. **Source-claim verification procedure** — check synthesized claims against cited sources
5. **Calibration mechanism** — track vault_lint agreement with Sean's corrections
6. **Phase 4: source change detection** — hash archived sources, detect staleness

### Tier 3: Hermeneutics Gaps (fix third — interpretation quality depends on the other two)
7. **Connect textbook indexes** — bridge the islands so retrieval can reach them
8. **Synthesis procedure** — scaffold how to read a subgraph and produce an answer
9. **Retrieval quality testing** — measure whether FUSED finds the right notes
10. **Context window management** — hop radius, truncation priority

---

## The Meta-Pattern

This self-assessment IS the triad in action:

- **Ontology**: I identified what entity types exist (directives, procedures, research, etc.) and what's missing (more procedures, examples, decision trees, taxonomy)
- **Epistemology**: I identified how knowledge is validated (provenance, vault_lint, procedure tracker) and what's missing (fact-checking, A/B testing, calibration, source change detection)
- **Hermeneutics**: I identified how meaning is derived (FUSED retrieval, synthesis) and what's missing (graph connectivity, retrieval testing, synthesis procedure, context management)

The triad diagnosed itself. That's the fractal pattern from [[Fractal-Entropy-Principle]] — the same structure repeats at every scale, including the scale of self-assessment.

## Related

- [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]] — the framework used for this assessment
- [[Procedural-Bootstrap-and-Evolution-Plan]] — the plan for filling ontology gaps (procedures)
- [[Small-Model-Path-to-AGI]] — the vision this assessment serves
- [[Pre-Thought-Information-Shapes]] — typed edges (ontology gap #5)
- [[Vault-Thinks-LLM-Synthesizes]] — synthesis principle (hermeneutics gap #3)
- [[Deterministic-Scaffolding-for-Small-Models]] — validation layers (epistemology gaps)
- [[Fractal-Entropy-Principle]] — the fractal pattern that makes the triad self-applicable

---

---

## Update: 2026-07-26 — Gaps Filled

The following gaps identified above have been addressed:

### Tier 2 (Epistemology) — Filled

| Gap | Status | Note |
|---|---|---|
| Source-claim verification | ✅ Researched + Architecture note | [[Claim-Verification-for-Vault-Notes]] + [[How-to-Verify-Claims-in-a-Research-Note]] |
| Calibration mechanism | ✅ Researched + Architecture note | [[Calibration-via-Operator-Feedback]] |
| RAG evaluation metrics | ✅ Researched + Architecture note | [[RAG-Evaluation-for-FUSED-Retrieval]] + [[How-to-Evaluate-Retrieval-Quality]] |

### Tier 3 (Hermeneutics) — Filled

| Gap | Status | Note |
|---|---|---|
| Context window management | ✅ Researched + Architecture note | [[Context-Budgeting-for-Vault-Growth]] + [[How-to-Manage-Context-Budget]] |
| Exemplar note design | ✅ Synthesized from first principles | [[Exemplar-Note-Design]] |

### Procedural Coverage Update

- **Before:** 2 procedural notes
- **After:** 5 procedural notes (added How-to-Verify-Claims, How-to-Evaluate-Retrieval, How-to-Manage-Context-Budget)
- **Target:** 15-20
- **Remaining:** 10-15 more procedures needed (tool creation, chat response, gap analysis, etc.)

### Still Open

- **Tier 1 (Ontology):** Formal taxonomy of note types, typed edges implementation, 3-5 exemplar notes
- **Source change detection** (Phase 4 of the evolution plan) — optional, marked "later"
- **Implementation:** All 5 architecture notes describe modules to build (`claim_verifier.py`, `rag_eval.py`, `context_budgeter.py`, `calibration.py`, exemplar notes). None are built yet — they're design specs ready for implementation.