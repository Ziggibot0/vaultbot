---
type: guide
status: active
baseline: true
created: 2026-07-30
summary: "When to create a tool vs a procedure. Tools are general capabilities used across many tasks. Procedures are bespoke step sequences for specific workflows."
tags: [meta, decision-guide, tools, procedures]
depends_on:
  - "[[Write-Python-Tool]]"
  - "[[Procedural-Bootstrap-and-Evolution-Plan]]"
---

# Tool vs Procedure: When to Build What

## The Rule

| | Tool | Procedure |
|---|---|---|
| **Scope** | General capability | Bespoke workflow |
| **Frequency** | Used across many different tasks | Used for one specific task type |
| **If it were gone** | You'd feel its absence constantly | You'd only miss it when doing that one thing |
| **Examples** | `vault_search`, `vault_lint`, `code_run` | How-to-Write-a-Python-Tool, How-to-Verify-Claims |
| **Cost of noise** | Low — always useful in the tool list | High — clutters the tool list for no benefit most of the time |

## Decision Test

Ask yourself these questions in order:

1. **"Would I use this on unrelated tasks?"** → Yes = tool. No = procedure.
2. **"Does this need LLM judgment between steps?"** → Yes = procedure (procedures can have LLM steps). No, it's pure deterministic logic = maybe a tool.
3. **"Is this a sequence of steps I follow the same way every time?"** → Yes = procedure. The steps are the value, not the capability.
4. **"Would this be noise in my tool list 90% of the time?"** → Yes = procedure. Don't pollute the tool registry.

## Concrete Examples from This Vault

### Things that ARE tools (correct)
- `vault_lint` — checks any note for broken links, missing frontmatter. Used after every note write, regardless of topic.
- `vault_search` — semantic search over the vault. Used in every session for every kind of task.
- `code_run` — execute Python in a sandbox. Used for testing, data processing, file operations. General.

### Things that ARE procedures (correct)
- [[Write-Python-Tool]] — a specific 7-step workflow for creating tools. The *steps* are the value (audit → reflect → code → test → deploy → verify → preflight). Making this a tool would be meaningless — it's not a capability, it's a checklist.
- [[Verify-Claims]] — a specific workflow for post-research verification. Only relevant after writing a research note.

### Friction I hit during research that needs a decision

**Wikilink case mismatch + title hallucination in research notes:**
- The LLM generates wikilinks in lowercase, but vault notes are title-case. It also hallucinates note titles that don't exist.
- This happens on EVERY research note. It's a general post-processing step.
- **Verdict: Tool.** A `vault_wikilink_fixer` that checks all wikilinks against actual vault filenames and fixes case mismatches / removes hallucinated links would be used after every research note, every chat response with links, every exemplar write. It's general, not bespoke to one workflow.
- Alternatively: make it a step in the research pipeline itself (deterministic post-processing after note creation). Either way, it's tool-level, not procedure-level.

## Related

- [[Write-Python-Tool]] — the procedure for creating tools
- [[Procedural-Bootstrap-and-Evolution-Plan]] — the framework for procedures
- [[Fractal-Entropy-Principle]] — expect edge cases; keep mechanisms simple
