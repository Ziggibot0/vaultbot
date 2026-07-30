---
created: 2025-07-25
summary: "The vault thinks for the LLM. The LLM only reads, calls tools, and synthesizes. No maintenance machinery."
tags: [architecture, design-principle, core]
---

# Vault-Thinks-LLM-Synthesizes

## The Principle

The intelligence lives in the vault's note content, not in graph metadata or machinery. When VaultBot writes a note, it writes a complete argument — claim, reasoning, connections via plain wikilinks. The LLM's job is to find the right notes, read them, and synthesize an answer.

**The vault thinks. The LLM synthesizes.**

## What This Means in Practice

- **Notes are self-contained arguments**, not raw facts. Each note includes the reasoning, implications, and connections to related notes — in prose, not metadata.
- **Wikilinks are citations**, not typed edges. `[[Related Note]]` is enough. The prose around the link explains the relationship.
- **No typed edges, no abstraction cache, no argument builder.** These were considered in [[Ephemeral-Argument-Architecture]] but rejected — too much ongoing maintenance for unclear benefit.
- **The LLM does three things:** (1) find the right notes via retrieval, (2) call tools when the vault is insufficient, (3) synthesize results into an answer. That's it.
- **Maintenance is zero-cost** because writing good notes is what VaultBot already does. Writing them with reasoning included is just writing better notes — no new syntax, no new subsystems, no vocabulary to garden.

## Why Not Typed Edges?

The [[Ephemeral-Argument-Architecture]] design proposed typed edges (`contradicts::[[Note]]`) with annotations, an ephemeral argument builder, and an abstraction cache for high-traffic clusters. It was rejected because:

1. **Typed edges add cost to every note write** — extra syntax, annotations, vocabulary discipline. Forever.
2. **The abstraction cache is a self-maintaining subsystem** — it exists solely to optimize itself. More machinery = more maintenance.
3. **In practice, notes are written in response to real queries** — the argument is already tailored to a real context when it's written. The "flexibility" of reassembling facts into different arguments solves a problem we mostly don't have.

If we later hit a wall where the LLM can't synthesize across multiple notes, typed edges can be added as a lightweight enhancement. But don't build the machinery until we know we need it.

## The LLM's Role

```
query → retrieval finds relevant notes → LLM reads notes → LLM synthesizes answer
                                    ↓ (if vault is thin)
                                    → LLM calls research tools → writes new notes → synthesizes
```

The LLM is a reader, tool-caller, and synthesizer. It is NOT a graph reasoner. The graph's job is to surface the right notes. The notes' job is to contain the reasoning. The LLM's job is to relay it.

---

## Philosophical Grounding

This note's principle — "the vault thinks, the LLM synthesizes" — is grounded in **hermeneutics**, the theory of interpretation. The hermeneutic circle (understanding parts through the whole, and the whole through the parts) is exactly what FUSED retrieval does: it pulls a connected subgraph where each note is interpreted in context. See [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]] for the full philosophical framework.
