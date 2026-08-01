---
type: procedure
status: active
model_cartridge: small
created: 2026-08-01
description: "A step-by-step guide to creating a new, valid procedure note. Covers decision-making (tool vs proc), frontmatter structure, step types, and final validation."
when_to_use: "whenever you need to create a new automated workflow or SOP for yourself"
---

# How to Create a Procedure

This procedure guides you through creating a valid, executable procedure note. Follow these steps in order.

## Step 1: Decide — Tool vs Procedure
Before writing anything, determine if this belongs as a **Tool** (general capability) or a **Procedure** (specific workflow).
*   **Rule of thumb:** If it's used for *one specific task type*, it's a procedure. If it's used across *many unrelated tasks*, it's a tool.
*   **Action:** If you decide on a Procedure, proceed to Step 2. If Tool, abort this procedure and use `tool_create` or `safe_write` instead.

## Step 2: Check for Existing Procedures
Search the vault to ensure we aren't duplicating an existing procedure.
1.  Call `vault_search` with query: `"[name of the workflow] procedure"`
2.  If a highly relevant, active procedure exists, **abort** and use that instead.

## Step 3: Draft Frontmatter
Create the YAML frontmatter for the new note. It must include:
*   `type: procedure` (mandatory)
*   `model_cartridge:` (choose one: `big`, `small`, or `vision`)
    *   `big`: For novel reasoning, synthesis, or complex planning.
    *   `small`: For classification, extraction, routing, and formatting (saves tokens).
    *   `vision`: For PDF/image reading tasks.
*   `allowed_tools:` (list of tools needed by code steps)
*   `description:` (brief summary for RAG discovery)

## Step 4: Write the Steps
Structure the body as numbered instructions. Mix **Code Steps** and **LLM Steps**:
1.  **Code Steps:** Use ````python` blocks for deterministic work (file I/O, searching, parsing). These run in a sandbox at zero LLM cost.
2.  **LLM Steps:** Write plain text instructions for judgment calls, decisions, or synthesis. The model will "read" these and act on them.
3.  **Validation:** Use `[validate:]` annotations in code blocks if you need to ensure an output matches a pattern before continuing.

## Step 5: Final Validation & Save
1.  Review the note for clarity and completeness.
2.  Ensure all `allowed_tools` listed in frontmatter actually exist.
3.  Save the file to `System/Procedures/[Procedure-Name].md`.
4.  (Optional) Run a mental simulation: "If I were the model, would I understand exactly what to do at each step?"
