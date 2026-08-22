# 0001. The vault is the knowledge base, not the model

Status: Accepted

## Context

A conventional RAG system treats the model as the source of truth and the
retrieval store as a cache of context to feed it. The model "knows" things
from its training data; the vector store just helps it recall relevant
passages.

VaultBot's mission is *provenance*: every knowledge claim must be
traceable to a source. If the model is the source of truth, provenance is
impossible — you cannot cite a model's weights.

## Decision

The Obsidian vault is the single source of truth. The LLM is swappable
plumbing. A claim that is not in the vault with a citation does not exist.

This is enforced by the **closed-set citation gate**: a synthesized answer
must cite vault notes, and an uncited claim is rejected — the code
analogue of a peer-reviewed citation requirement.

## Consequences

- **Easier:** provenance is architectural, not aspirational. Every answer
  is auditable. The model can be swapped (Ollama → OpenAI → OpenRouter)
  without changing what the system "knows."
- **Harder:** the system is only as good as the vault. Thin vaults produce
  thin answers, and the model's own knowledge is deliberately suppressed.
- **Given up:** the convenience of letting a frontier model answer from
  its weights. This is the point — but it means VaultBot will say "I don't
  know" where a chatbot would confidently answer.
