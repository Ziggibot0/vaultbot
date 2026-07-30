---
created: 2026-07-26
summary: "How to structure permanent example notes in the vault that small models can retrieve and pattern-match against — few-shot prompting where the shots live in the vault, not the prompt."
tags: [architecture, exemplars, few-shot, small-models, ontology]
---

# Exemplar Note Design

## The Problem

The [[Small-Model-Path-to-AGI]] vision depends on moving cognition from LLM weights to vault content. One of the six strategies is **example notes** — permanent exemplars that a small model can retrieve and pattern-match against, instead of relying on its weights to "know" what a good output looks like.

But the research on how to *design* these exemplars is thin. The external research (see `research/Designing-permanent-exemplar-documents-for-pattern-matching-in-knowledge-based-A.md`) found KG-RAG papers but not exemplar design principles. This note synthesizes from first principles, drawing on the few-shot prompting research already in the vault and the procedural bootstrap framework.

## What We Know from Existing Research

### Few-Shot Prompting Works for Small Models

The [[Procedural-Bootstrap-and-Evolution-Plan]] established that small models benefit disproportionately from explicit examples. A 30B model with a good example can match a frontier model without one. The example doesn't need to be in the prompt — it just needs to be *retrievable* when the model needs it.

### The Vault Already Has Implicit Exemplars

Several existing notes serve as de facto exemplars:
- [[Structure-Research-Note]] — exemplar for research note structure
- [[How-to-Evaluate-Source-Credibility]] — exemplar for source evaluation
- [[Deterministic-Scaffolding-for-Small-Models]] — exemplar for architecture notes
- [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]] — exemplar for synthesis notes

But they're not *tagged* as exemplars, not *retrievable* as exemplars, and not *designed* to be pattern-matched against.

## Design Principles for Exemplar Notes

### 1. Type-Specific, Not Generic

Each exemplar should demonstrate one *type* of output. Don't mix a research note exemplar with a tool creation exemplar. The model needs to pattern-match against a clean example, not a hybrid.

| Exemplar Type | What it demonstrates | Status |
|---|---|---|
| Research note | Synthesis with sources, wikilinks, frontmatter | Exists ([[Structure-Research-Note]]) |
| Architecture note | Design reasoning, trade-offs, what-to-build | Exists ([[Deterministic-Scaffolding-for-Small-Models]]) |
| Procedural note | Step-by-step procedure with schema | Exists ([[How-to-Evaluate-Source-Credibility]]) |
| Tool creation | Code, test, deploy, lint | Needed |
| Chat response | Concise, bottom-line-up-front, cited | Needed |
| Gap analysis | Detect gap, research, fill, lint | Needed |

### 2. Annotated, Not Just Complete

An exemplar isn't just a good output — it's a good output *with annotations explaining why each part is there*. The annotations are what teach the model the pattern. Use HTML comments so they don't render in Obsidian but are visible to the model when it reads the raw markdown.

### 3. Retrievable by Task, Not by Topic

Exemplars should be tagged so FUSED retrieval finds them when the model is about to perform a task, not when it's researching a topic. Tags like `exemplar`, `exemplar:research-note`, `exemplar:tool-creation` make them retrievable by task type.

### 4. Stable, Not Evolving

Exemplars should be LOCKED once validated. If an exemplar changes, the pattern it teaches changes, and the model's behavior becomes non-deterministic. Evolve the *procedure* (see [[Procedural-Bootstrap-and-Evolution-Plan]]), not the exemplar.

## How This Connects to the Small Model Path

For a 30B model (see [[Small-Model-Path-to-AGI]]):

1. Model receives a task (e.g., "write a research note about X")
2. FUSED retrieval finds the exemplar for research notes
3. Model reads the exemplar + the research results
4. Model pattern-matches against the exemplar's structure
5. Output follows the exemplar's pattern, not the model's internal "idea" of what a research note looks like

This is the difference between a senior engineer (frontier model with weights that "know" good output) and a junior engineer with a really good playbook (30B + exemplar vault).

## What Needs to Be Built

- 3-5 exemplar notes covering the types marked "Needed" above
- An `exemplar` tag convention in the vault
- FUSED retrieval weighting that surfaces exemplars when the model is about to perform a task
- A LOCKED marker on validated exemplars to prevent drift

## Related
- [[Small-Model-Path-to-AGI]] — the vision this serves
- [[Procedural-Bootstrap-and-Evolution-Plan]] — procedures + exemplars = the scaffolding
- [[Pre-Thought-Information-Shapes]] — exemplars are a type of pre-thought information shape
- [[Deterministic-Scaffolding-for-Small-Models]] — exemplars are deterministic scaffolding
- [[Structure-Research-Note]] — existing implicit exemplar
- [[Self-Assessment-Using-the-Knowledge-Triad]] — the gap this fills
