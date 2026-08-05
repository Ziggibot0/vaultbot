---
type: convention
status: active
created: 2026-08-03
tags:
  - convention
  - documentation
  - records
  - history
summary: Record Convention
---

# Record Convention

## Purpose

VaultBot's vault contains notes that describe how the system *currently* works, and notes that record how the system *used to* work or what was *planned*. These must never be confused. This convention defines how to mark historical notes so they are never mistaken for current documentation.

## The Rule

Any note that describes behavior, architecture, or design that **does not match the current code** must have:

1. A frontmatter field `record: true`
2. A banner at the top of the note body (immediately after the first heading):

```
> **⚠ RECORD — NOT CURRENT DOCUMENTATION**
> This note describes past behavior, a completed plan, or an unbuilt design spec. It is preserved for project history. Do not use it to understand how the system works today. For current behavior, read the code or search for notes with `record: false` or no `record` field.
```

3. If the note describes behavior that was *replaced*, add a `superseded_by` field in frontmatter pointing to the note or code file that replaced it.

## What Gets Marked as a Record

| Type | Example | Mark as record? |
|---|---|---|
| Chat log | `Chat-some-conversation.md` | No — chat logs are inherently historical, no banner needed |
| Build log | `Procedure-Consolidation-2026-08-02.md` | No — already has `type: build-log` |
| Audit report | `Audit-2026-08-03-Copilot-Changes.md` | No — already has `type: audit` and a date |
| Completed plan | `Framework-Friction-Fix-Plan.md` | Yes — has `status: complete` but should also get `record: true` + banner |
| Architecture spec for unbuilt feature | `Semantic-Consolidation-Architecture.md` | Yes — describes a design that was never built |
| Procedure describing old behavior | `Agentic-Loop-Turn-Protocol.md` (old version) | Yes — if it describes behavior the code no longer implements |
| Semantic note about past patterns | `Autonomy-Interruption-and-Goal-Staleness-Pattern.md` | No — it records observed patterns, not framework mechanics |

## What Does NOT Get Marked as a Record

- Notes with `type: research` — these are knowledge, not system docs
- Notes with `type: concept` — these are ideas, not system docs
- Notes with `type: procedure` that describe *current* behavior — update them to match code
- Chat logs, build logs, audit reports — they already have type markers that make their nature clear

## How to Audit

When auditing the vault for contradictions:

1. Read the note's claims about how the system works
2. Read the actual code (`chat_handler.py`, `agent_tools.py`, etc.)
3. If the note's claims don't match the code:
   - If the note describes a *past* state or *completed* plan → mark as record
   - If the note describes a *current* mechanism incorrectly → update the note to match code
4. Run `vault_lint` on the updated note

## Related

- [[Audit-2026-08-03-Copilot-Changes]] — the audit that triggered this convention
- [[Find-Contradictions]] — procedure for systematically finding note-vs-code mismatches