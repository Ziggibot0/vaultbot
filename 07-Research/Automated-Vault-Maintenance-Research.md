---
type: research
status: complete
created: 2026-07-26
summary: "Research into two gaps: (1) automated graph maintenance in PKM systems and (2) safe deterministic link suggestion algorithms. Synthesizes A-MEM (NeurIPS 2025), Adamic-Adar link prediction, AgentDock 4-layer memory architecture, and existing vault infrastructure into a concrete design for an automated orphan-note connection loop."
tags: [research, vault-maintenance, link-prediction, orphan-detection, automation, deterministic, graph-theory, zettelkasten]
depends_on:
  - "[[Semantic-Consolidation-Architecture]]"
  - "[[How-to-Consolidate-Experiences-into-Semantic-Knowledge]]"
  - "[[Deterministic-Scaffolding-for-Small-Models]]"
sources:
  - "https://proceedings.neurips.cc/paper_files/paper/2025/hash/19909c36f51abc4856b4560aff3d36d6-Abstract-Conference.html"
  - "https://arxiv.org/abs/2308.13176v1"
  - "https://github.com/AgentDock/AgentDock/pull/222"
  - "https://arxiv.org/abs/2411.09999"
  - "https://arxiv.org/abs/2511.11017v1"
---

# Automated Vault Maintenance Research

## The Problem

Orphan notes accumulate in the vault because nothing automatically connects them. The [[Semantic-Consolidation-Architecture]] describes a 4-step pipeline (pattern extraction, clustering, synthesis, validation), but only step 1 is built (`pattern_extractor.py`). More critically, there is a separate gap: **automated orphan detection + connection** is graph *structure* maintenance, not content consolidation. The autonomous researcher actively skips orphans (filters out `link_density` gaps). Every orphan connection I have made (pattern highways, wiring research notes) was a manual one-shot operation.

This research fills two gaps:
1. How do PKM systems and AI agent memory architectures handle automated graph maintenance?
2. What deterministic heuristics can safely suggest links without LLM judgment?

---

## Finding 1: A-MEM — Zettelkasten-Inspired Agentic Memory (NeurIPS 2025)

The most directly relevant source is **A-MEM** (Agentic Memory for LLM Agents, NeurIPS 2025). Its architecture mirrors what VaultBot needs.

**How A-MEM works:**
- When a new memory is added, the system generates a comprehensive note with structured attributes: contextual descriptions, keywords, and tags
- It then **analyzes historical memories to identify relevant connections**, establishing links where meaningful similarities exist
- New memories trigger **memory evolution** — updates to the contextual representations and attributes of *existing* historical memories
- This allows the memory network to continuously refine its understanding

**Why this matters for us:** A-MEM's "analyze historical memories to identify relevant connections" is exactly the orphan-connection step we are missing. The difference is A-MEM uses LLM for the linking decision; we can do it deterministically using our existing FUSED retrieval (vector + graph + backlinks) plus structural heuristics.

**The evolution principle is key:** when a new note is created, it should not just be linked — it should also trigger a check of whether *existing* notes should now link to it. This is bidirectional maintenance, not just one-way linking.

Source: [A-MEM: Agentic Memory for LLM Agents](learningMaterial/web/proceedings-neurips-cc-paper-files-paper-2025-hash-68c3d06a.html)

---

## Finding 2: Adamic-Adar Index — Deterministic Link Prediction

The **Adamic-Adar Index (AAI)** is a deterministic graph heuristic for predicting missing edges. A 2023 study found AAI outperforms Jaccard Coefficient, Common Neighbor Centrality, *and* machine learning algorithms (random forest, SVM, gradient boosting) for graph link prediction.

**How it works:**
For two nodes u and v, Adamic-Adar sums the inverse log of the degree of each common neighbor:

```
AAI(u, v) = sum of 1/log(degree(w)) for each common neighbor w
```

**Why this is the right heuristic for us:**
- **Low-degree common neighbors are more informative.** If an orphan note and a candidate target both link to a niche note like [[Calibration-via-Operator-Feedback]], that is a stronger signal than if they both link to a hub like [[Deterministic-Scaffolding-for-Small-Models]].
- **Pure math, zero LLM.** No hallucination risk — the survivor-forge warning about "hallucinating connections that look plausible but are not load-bearing" applies to LLM-based linking, not deterministic graph heuristics.
- **"Less is more"** — the study found AAI works well even with sparse data, which matches our vault scale (~200 notes).
- **Complementary to FUSED retrieval.** Adamic-Adar captures *structural* similarity (shared neighbors in the graph). FUSED captures *semantic* similarity (vector embeddings + backlinks). Combining both gives a richer signal than either alone.

**Other deterministic heuristics from the literature:**
- **Jaccard Coefficient**: common neighbors / union of neighbors. Simpler than AAI but does not weight by neighbor degree.
- **Common Neighbor Centrality (CNC)**: raw count of shared neighbors. Even simpler but most prone to suggesting hub connections.
- **Preferential Attachment**: degree(u) x degree(v). Biases toward high-degree nodes — useful for "where should this go?" but risks creating everything-links-to-hubs patterns.

Source: [Using Adamic-Adar Index Algorithm to Predict Volunteer Collaboration](learningMaterial/web/arxiv-org-abs-2308-13176v1-d1f5e77f.html), [Theoretical Justification of Popular Link Prediction Heuristics](https://www.researchgate.net/profile/Deepayan-Chakrabarti-2/publication/220815677_Theoretical_Justification_of_Popular_Link_Prediction_Heuristics)

---

## Finding 3: AgentDock 4-Layer Memory Architecture

AgentDock (open-source agent framework) implemented a 4-layer cognitive memory architecture that maps cleanly to VaultBot's existing structure:

| AgentDock Layer | VaultBot Equivalent | Built? |
|---|---|---|
| Working memory (current context, TTL) | Chat context in system prompt | Yes |
| Episodic memory (conversation history, semantic search) | Chat logs in `vaultbot/chat/` | Yes |
| Semantic memory (knowledge extraction, long-term facts) | Research notes, architecture notes, pattern highways | Yes (but not auto-linked) |
| Procedural memory (learned patterns, workflows) | Procedure notes, tool code | Yes |
| **Intelligence layer (connections + consolidation)** | **MISSING** | **No** |

AgentDock's intelligence layer does two things we do not:
1. **Connections** — automatically identifies and creates links between memories
2. **Consolidation** — converts episodic memories into semantic knowledge

Their hybrid search uses 30% text + 70% vector fusion — interesting but our FUSED retrieval already does vector + graph + backlinks, which is richer.

Source: [AgentDock PR #222: Conversational memory system with 4-layer architecture](learningMaterial/web/github-com-agentdock-agentdock-pull-222-bc64769b.html)

---

## Finding 4: Graph Database Survey — Community Detection + Connectivity

A comprehensive graph database survey (arXiv:2411.09999) covers algorithms directly applicable to vault maintenance:

- **Louvain method** for community detection — could identify clusters of related notes that should share a hub
- **Node centrality** (betweenness, degree, closeness) — identifies hub notes and bridge notes
- **Graph connectivity** analysis — the foundation of orphan detection

The survey also covers Neo4j's approach to graph maintenance, but at a scale (Wikidata-level) that is overkill for a 200-note personal vault.

Source: [Understanding Graph Databases: A Comprehensive Tutorial and Survey](learningMaterial/web/arxiv-org-abs-2411-09999-868e9e5e.html)

---

## Finding 5: AI Agent-Driven KG Construction — Automated Population

A 2025 paper on automated product knowledge graph construction describes a 3-stage LLM-agent pipeline: ontology creation, refinement, KG population. While the domain (e-commerce) is different, the pattern is relevant:

- **Stage 1**: Create structure (ontology/schema) — for us, this is the note type system (procedure, architecture, research, exemplar)
- **Stage 2**: Refine structure — for us, this is pruning junk and fixing broken links
- **Stage 3**: Populate — for us, this is connecting orphans to the right nodes

They achieve 97% property coverage with minimal redundancy. The key lesson: automated KG construction works when the schema is well-defined and the agents operate within clear constraints.

Source: [AI Agent-Driven Framework for Automated Product Knowledge Graph Construction](learningMaterial/web/arxiv-org-abs-2511-11017v1-cce71b85.html)

---

## Synthesis: Design for vault_maintenance.py

Based on this research, the automated maintenance loop should combine three signals:

### Signal 1: Structural Similarity (Adamic-Adar)
For each orphan node, compute Adamic-Adar score against all connected nodes. High score = shared neighbors = likely related. This is pure graph math, no embeddings needed.

### Signal 2: Semantic Similarity (FUSED Retrieval)
For each orphan, run FUSED retrieval (vector + graph + backlinks) with the note title + first paragraph as query. Top results are semantically related candidates. This uses the existing `vault_search` infrastructure.

### Signal 3: Type-Based Rules (Deterministic)
Structural rules that do not need any scoring:
- Chat logs -> connect to relevant pattern highway ([[Testing-and-Verification-History]], [[VaultBot-Build-Log]], [[Sean-Design-Decisions]])
- Research notes -> connect to their architecture counterpart
- Textbook indexes -> connect to [[Textbook-Library]]
- New procedure notes -> connect to [[Procedural-Bootstrap-and-Evolution-Plan]]

### The Connection Decision
For each orphan, combine all three signals:
1. If a type-based rule matches -> apply it (highest confidence, zero computation)
2. If Adamic-Adar score > threshold AND FUSED score > threshold -> suggest the link
3. If only one signal fires -> log as "low-confidence" for later review
4. If no signal fires -> leave as orphan (some notes are legitimately isolated)

### Safety Guards
- **Never delete notes** — only add wikilinks
- **Never modify LOCKED notes** or sacred journals
- **Threshold-based**: only connect if both structural AND semantic signals agree (prevents junk connections)
- **Log everything**: every connection made, with reason (which signal triggered, what score)
- **Rate-limited**: max N connections per cycle (do not flood the vault)
- **Respects existing links**: do not add duplicate wikilinks

### Integration Point
Runs as a background loop alongside the autonomous researcher, on a longer interval (e.g., every 30 min vs. every 10 min for research). Uses the same pause-for-chat mechanism so it does not compete with interactive turns for GPU.

---

## What We Already Have (No Research Needed)

- `pattern_extractor.py` (606 lines) — deterministic pattern extraction from chat logs
- `vault_graph_analyzer` tool — finds islands, suggests bridge edges
- FUSED retrieval (`vault_search`) — vector + graph + backlinks semantic search
- Pattern highways — 4 hub notes already connecting 97 orphan chats
- [[Semantic-Consolidation-Architecture]] — 18KB design doc, 4-step pipeline
- [[How-to-Consolidate-Experiences-into-Semantic-Knowledge]] — procedure note
- Autonomous researcher — background loop infrastructure

## What Is Missing (Build Targets)

1. **`vault_maintenance.py`** — the automated orphan detection + connection loop
2. **Adamic-Adar implementation** — pure Python, ~20 lines of math
3. **Type-based routing rules** — simple if/else on file paths
4. **Connection logging** — JSON log of every link created, with reason and score
5. **Integration into main.py** — background thread alongside autonomous researcher
6. **Steps 2-4 of consolidation pipeline** — clustering, synthesis, validation (separate from maintenance but related)

---

## Research Quality Assessment

**Strong sources:** A-MEM (NeurIPS 2025, peer-reviewed), Adamic-Adar study (arXiv, empirical), AgentDock (real implementation).

**Weak coverage:** PKM-specific tooling (Obsidian plugins, Foam, Logseq) — the research engine consistently failed to find relevant sources in this niche. The keyterm extractor stripped domain-specific terms and searched for generic ones. This is a known limitation of the research engine, not a gap in the literature.

**Confidence:** High on the algorithmic approach (Adamic-Adar is well-established). Medium on the PKM-specific patterns (extrapolated from A-MEM and AgentDock rather than direct PKM research). High on the integration design (builds on existing infrastructure).
