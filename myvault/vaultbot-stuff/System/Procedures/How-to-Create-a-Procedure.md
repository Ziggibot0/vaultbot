---
type: procedure
status: active
baseline: true
created: 2026-08-01
description: A step-by-step guide to creating a new, valid procedure note. Covers decision-making (tool vs proc), frontmatter structure, step types, and final validation.
when_to_use: whenever you need to create a new automated workflow or SOP for yourself
summary: "1. A procedure guide for deciding between tools and workflows to create a valid executable note structure.
|step_01, step_02, step_03 |key_topics:tool,vault,draft"
tags:
  - procedure
  - procedures
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
allowed_tools:
  - vault_search
---

# Create-Procedure-Guide

This procedure guides you through creating a valid, executable procedure note. Follow these steps in order.

## Why This Exists

Creating a procedure note that actually parses and executes requires a
specific structure (frontmatter, step headers, step types). This guide
codifies that structure so new procedures are valid on the first try. The
tradeoff: it is a human-facing guide, not an automated validator — it relies
on the author following the steps.

## Steps

### Step 1: Decide — Tool vs Procedure
Before writing anything, determine if this belongs as a **Tool** (general capability) or a **Procedure** (specific workflow).
*   **Rule of thumb:** If it's used for *one specific task type*, it's a procedure. If it's used across *many unrelated tasks*, it's a tool.
*   **Action:** If you decide on a Procedure, proceed to Step 2. If Tool, abort this procedure and use `tool_create` or `safe_write` instead.

### Step 2: Check for Existing Procedures
Search the vault to ensure we aren't duplicating an existing procedure.
*   Call `vault_search` with query: `"[name of the workflow] procedure"`
*   If a highly relevant, active procedure exists, **abort** and use that instead.

### Step 3: Draft Frontmatter
Create the YAML frontmatter for the new note. It must include:
*   `type: procedure` (mandatory)
*   `model_cartridge:` (choose one: `big`, `small`, or `vision`)
    *   `big`: For novel reasoning, synthesis, or complex planning.
    *   `small`: For classification, extraction, routing, and formatting (saves tokens).
    *   `vision`: For PDF/image reading tasks.
*   `allowed_tools:` (list of tools needed by code steps)
*   `description:` (brief summary for RAG discovery)

### Step 4: Write the Steps
Structure the body with `### Step N: short-summary` headers. EVERY step MUST have one — the header summary is the step's human-readable description, shown in progress and logs. Mix **Code Steps** and **LLM Steps**:

*   **Code Steps:** Put a ```` ```python ```` fence after the `### Step N:` header for deterministic work (file I/O, searching, parsing). These run in a sandbox at zero LLM cost.
*   **LLM Steps:** Put `[llm: instruction]` after the `### Step N:` header for judgment calls, decisions, or synthesis. The model will "read" these and act on them.
*   **Validation:** Use `[validate:]` annotations in the header instruction text if you need to ensure an output matches a pattern before continuing.

### Step 5: Final Validation & Save
*   Review the note for clarity and completeness.
*   Ensure all `allowed_tools` listed in frontmatter actually exist.
*   Save the file to `System/Procedures/[Procedure-Name].md`.
*   (Optional) Run a mental simulation: "If I were the model, would I understand exactly what to do at each step?"

## Related

- [[Create-New-Procedure]] — automated procedure creation
- [[Draft-New-Procedure]] — drafts a new procedure note
- [[Check-Procedure-Type]] — validates a procedure's type/structure
