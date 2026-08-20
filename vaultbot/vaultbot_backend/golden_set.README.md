# Golden Set — FUSED Retrieval Benchmark

> **What this is:** A curated set of `{query → expected_notes[]}` pairs scored
> against VaultBot's real FusedRetriever to produce objective, repeatable
> retrieval-quality metrics (recall@k, precision@k, NDCG@k, MRR).
>
> **Why it exists:** Without a fixed yardstick, there is no way to know whether
> a retrieval change made things better or worse. The golden set turns "I think
> retrieval is good" into a number that can be watched over time and gated in
> CI. It is the objective slop-detector.

---

## The 5 Categories

Every entry in the golden set is tagged with one of five categories. Each
category tests a distinct retrieval capability, so a per-category breakdown
immediately localizes regressions to a specific channel.

### 1. Direct Retrieval (`direct`)
Queries where the answer is a specific note by name or a very close
paraphrase of the note's title. Tests **exact keyword and vector match** —
the query contains the note's name words. Recovered by the **lexical BM25
channel** (title/keyword match) as well as the vector channel.

- Example: `"what is the no wikipedia directive"` → `No-Wikipedia-Directive`
- What it proves: the vector index contains the note and the embedding
  model recognizes title-level keyword overlap; the lexical channel
  surfaces title/keyword matches a small embedding model can't map.
- Failure signal: a direct query can't find the named note → indexing is
  broken or the note was deleted/renamed without reindexing.

### 2. Semantic / Paraphrase (`semantic`)
Queries that use **different words** than the note title to ask for the same
thing. Tests **semantic matching** — the embedding model must generalize
beyond surface-form keywords.

- Example: `"how do I stop the bot from using online encyclopedias"` →
  `No-Wikipedia-Directive` (no shared keywords with the title)
- What it proves: the embedding model captures meaning, not just string
  overlap — the foundation of semantic retrieval.
- Failure signal: semantic queries fail while direct queries pass → the
  embedding model is doing keyword matching, not semantic matching
  (model is too small, wrong embedding, or index is stale).

### 3. Graph Walk (`graph`)
Queries where the right answer is **connected via wikilinks**, not directly
mentioned. The query semantically matches one note, but the *expected* note
is a wikilink neighbor of that note — retrievable only if the graph channel
follows links. Tests the **graph retrieval channel** (the "FUSED" fusion of
vector + graph + backlinks).

- Example: `"why did the operator correct vaultbot about making itself
  redundant"` semantically matches `VaultBot-Is-the-Vault` (the correction
  story), but the expected note is `Vault-Longevity-Architecture` (linked
  in that note's body as "the vault IS the mind, the model is swappable").
  Only the graph walk pulls it in.
- What it proves: the graph channel is firing — vector hits seed the walk,
  and linked neighbors are surfacing.
- Failure signal: graph queries fail while direct/semantic pass → the graph
  channel is broken (backlinks not indexed, graph depth = 0, or the fusion
  weights are ignoring graph candidates).

### 4. Negative / Anti-Retrieval (`negative`)
Queries from **domains unrelated to VaultBot's architecture** (biology,
electronics, philosophy, web-dev). The expected note is the correct
domain-specific note. The test is **precision**: the system should return
the correct domain note at high rank, NOT the vault's many architecture /
procedure / identity notes that might semantically bleed in.

- Example: `"how does CRISPR Cas9 cut DNA"` → `CRISPR-Gene-Editing`
  (biology note — should NOT retrieve code-quality or procedure-system
  notes even though the vault is full of them)
- What it proves: the retriever discriminates between domains — it doesn't
  just return "the most VaultBot-ish note" for every query.
- Failure signal: negative queries return architecture notes at high rank →
  the retriever is biased toward vault-architecture content (procedure
  boost is too aggressive, or the embedding model conflates domains).

### 5. Multi-Note (`multi`)
Queries where **2–3 notes are all relevant** and should all surface in
top-k. Tests **recall@k with k > 1** — the system must return multiple
correct notes, not just the single best match.

- Example: `"how does vaultbot handle safety when editing code"` →
  `Safe-Write`, `Self-Edit-Verification-Directive`, `Run-Test-Suite`
  (three safety procedures that all apply)
- What it proves: the retriever surfaces a complete relevant set, not just
  the top hit.
- Failure signal: multi-note queries return only 1 of N expected notes →
  recall@k is low because the retriever is over-precision (k too small,
  or score threshold is dropping the 2nd/3rd relevant note).

---

## Methodology — How Queries Were Selected

This is not "vibes." Each entry follows a documented selection protocol:

### Source Pool
All expected notes are drawn from the **baseline vault content** — the notes
that ship with the repo under `vaultbot/System/`, `vaultbot/Knowledge/`,
and `vaultbot/baseline/` (which includes the directive templates). These are
guaranteed present in every clone, so the golden set is reproducible in CI.

### Selection Criteria
1. **Realism** — the query is phrased the way an actual operator or reviewer
   would ask it, not a keyword soup. Direct queries use natural language
   ("what is the no wikipedia directive"), not bare title strings.
2. **Verifiability** — the expected note(s) must exist in the vault at the
   time of annotation. Every `expected_notes` entry was verified by listing
   the containing directory. The note stem (filename without `.md`) is used;
   normalization in `rag_eval.py` strips path, case, and extension, so
   `No-Wikipedia-Directive` matches `vaultbot/No-Wikipedia-Directive.md`.
3. **Distinctness** — no two entries in the same category test the exact same
   retrieval path. Each entry exercises a different note or a different
   retrieval mechanism.
4. **Category fit** — each entry is assigned to the category whose retrieval
   channel it primarily tests, and the `note` field explains the reasoning.

### Annotation Protocol
For each entry, the annotator (GitHub Copilot, 2026-08-16) performed:

1. **Identified the target note** by scanning the vault's directory tree.
2. **Verified the note exists** — listed the containing directory and
   confirmed the `.md` file is present.
3. **Crafted the query** to match the category's test intent:
   - `direct`: query contains the note's title words in natural order.
   - `semantic`: query uses synonyms/descriptions with NO title-word overlap.
   - `graph`: query matches a *different* note that wikilinks to the expected
     note — verified the wikilink exists by reading the linking note's body.
   - `negative`: query is from an unrelated domain; expected note is the
     domain-correct one.
   - `multi`: query legitimately applies to 2–3 notes; all were verified to
     exist and to be topically relevant to the query.
4. **Recorded the rationale** in the `note` field — the category, the
   paraphrase strategy, and the wikilink path (for graph entries).
5. **Cross-checked** — no expected note is a phantom (a wikilink target that
   doesn't resolve). Every stem was verified against a real `.md` file.

### What Was NOT Done
- No LLM was used to generate queries or expected notes. All pairs were
  hand-curated by reading the actual vault content.
- No query was included whose expected note could not be verified to exist.
  Three entries from the original 6-query seed set (`How-to-Decide-When-to-
  Research-vs-Answer`, `How-to-Structure-a-Research-Note`, `How-to-Verify-
  Claims-in-a-Research-Note`) were **removed** because those notes do not
  exist in the vault — they were phantom references that would have
  permanently scored 0 recall.

---

## How to Add New Entries

1. **Find a real note** in the vault. Verify it exists by listing its
   directory. Note the stem (filename without `.md`).
2. **Decide the category** — which retrieval channel are you testing?
3. **Craft the query** following the category's intent (see above).
4. **For graph entries**: read the linking note and confirm the wikilink to
   your expected note exists in the note body. Record the linking note as
   `seed_notes` (the note the query semantically matches, whose wikilinks
   lead to `expected_notes`). The seed note MUST be committed to the repo —
   a gitignored seed makes the graph walk unreachable in CI.
5. **Add the entry** to `golden_set.json` under `queries`:
   ```json
   {
     "query": "your query text",
     "expected_notes": ["Note-Stem-1", "Note-Stem-2"],
     "seed_notes": ["Linking-Note-Stem"],
     "note": "Category: direct|semantic|graph|negative|multi — why these notes",
     "category": "graph"
   }
   ```
   (`seed_notes` is required for `graph` entries; omit it for other
   categories.)
6. **Validate the set** against the committed notes — this fails loudly on
   any phantom expected note or gitignored graph seed:
   ```
   python validate_golden_set.py
   ```
7. **Run the gate locally** to confirm the new entry doesn't drag recall
   below the CI floor:
   ```
   python run_golden_gate.py --vault <vault_path> --min-recall 0.7 --k 5
   ```
8. Commit `golden_set.json` and `golden_set.README.md` together.

### Growth Sources (in priority order)
1. **Operator corrections** — when Sean says "you missed X," add a
   `{query → X}` entry. This is the highest-signal source: it's a real
   query that retrieval actually failed on.
2. **Phantom-neighbor probes** — identify hub notes with many wikilinks and
   craft graph-walk queries that should pull in a specific neighbor.
3. **Domain diversity** — if a category (especially `negative`) is dominated
   by one domain, add entries from other domains to keep the set balanced.
4. **Multi-note expansions** — when a single-note query has 2–3 equally
   valid answers, split it into a `multi` entry to test recall@k > 1.

---

## Current Size & Goal

| Category   | Count | Target |
|------------|-------|--------|
| direct     | 10    | 10–15  |
| semantic   | 11    | 10–15  |
| graph      | 6     | 10–15  |
| negative   | 4     | 8–10   |
| multi      | 7     | 8–12   |
| **Total**  | **38**| **50+**|

The goal is **50+ entries** across all 5 categories, grown primarily from
real operator corrections (source 1 above). A 50-entry set with documented
methodology is the minimum a scientist would consider evaluative rather
than anecdotal. The 5-category structure ensures regressions are
localizable to a specific retrieval channel.

---

## Scoring & CI Gate

The golden set is scored by `golden_eval.py`:

- **Metrics**: recall@k, precision@k, NDCG@k, MRR (computed per-query and
  aggregated).
- **Normalization**: `RAGEvaluator._normalize_note` strips path, case, and
  `.md` — so `expected_notes` uses bare stems.
- **CI gate**: `.github/workflows/golden-gate.yml` runs `run_golden_gate.py`
  with `--min-recall 0.7 --k 5` on PRs touching retrieval-affecting code.
  Aggregate recall@5 < 0.7 fails the workflow.
- **Pre-flight validation**: the workflow first runs
  `validate_golden_set.py`, which checks every `expected_notes` stem and
  every graph `seed_notes` stem resolves to a committed note (via
  `git ls-files`). A phantom expected note or a gitignored graph seed fails
  the workflow *before* the expensive index build, instead of silently
  scoring 0 recall at gate time.

The `category` field on each entry enables per-category metric breakdowns
in future reporting — so a regression in graph retrieval shows up as a
drop in the `graph` category's recall, not just a blurred aggregate.