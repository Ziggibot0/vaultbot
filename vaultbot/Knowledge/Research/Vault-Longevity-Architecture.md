---
type: research
status: raw
baseline: true
created: 2026-08-03
recreated: 2026-08-15
summary: "How VaultBot's vault architecture ensures knowledge permanence across model swaps, software updates, and long-term use. The vault is the mind — models are expendable plumbing."
tags: [architecture, local-first, vault-longevity, model-independence]
---

# Vault-Longevity-Architecture

## Core Principle

The vault is the mind. The LLM model is expendable, swappable plumbing. This architecture ensures that knowledge accumulated in the vault survives across:

- **Model swaps** — replacing one LLM with another loses zero knowledge
- **Software updates** — VaultBot backend changes don't affect vault content
- **Long-term use** — knowledge accumulates permanently, verifiably, and model-independently

## Why the Vault Outlives the Model

Every note, procedure, and exemplar in the vault is cognition that doesn't need to live in model weights. A ~4B model reading a well-structured vault can answer questions that would stump a 70B model working from memory. The vault is:

1. **Permanent** — markdown files on disk, not ephemeral model weights
2. **Verifiable** — every claim has sources, every wikilink can be checked
3. **Model-independent** — any LLM can read markdown; no proprietary format lock-in
4. **Growable** — knowledge accumulates over time; the vault gets smarter, models don't

## Architecture Decisions for Longevity

| Decision | Why it matters for longevity |
|---|---|
| Plain markdown files | Any tool can read them; no database to corrupt or migrate |
| Wikilinks as the graph | Relationships are explicit in the files themselves, not in a separate index |
| YAML frontmatter | Machine-readable metadata without locking into one tool's schema |
| Procedures as markdown | Skills survive model swaps — the procedure text is the skill, not the model's training |
| Trash/ backup before delete | Accidental deletions are recoverable |
| LOCKED notes | Critical identity/architecture notes can't be accidentally overwritten |

## The FAISS Index and Longevity

The FAISS vector index is a **derived artifact**, not a source of truth. If it's deleted or corrupted, it can be rebuilt from the markdown files. The files are primary; the index is a cache. This means:

- Index format changes (FAISS upgrades) don't lose knowledge
- A corrupted index is a nuisance, not a catastrophe
- The vault can be moved to a different machine and re-indexed

## Related Notes

- [[Cloud-Model-Obsolescence-Architecture]] — the full architecture for making cloud models optional
- [[Deterministic-Scaffolding-for-Small-Models]] — how the framework does the thinking, not the model
- [[Small-Model-Path-to-AGI]] — how small models achieve strong reasoning through scaffolding
- [[VaultBot-Strategic-Vision]] — the philosophical and strategic picture
- [[Procedural-Bootstrap-and-Evolution-Plan]] — how procedures grow and evolve over time