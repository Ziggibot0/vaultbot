---
type: procedure
status: experimental
model_cartridge: big
created: 2026-08-05
description: "Audit how the agentic loop assembles the prompt sent to the model: trace every block that gets injected (system prompt, vault context, procedure surface, hints, working memory, conversation recall), map the ORDER they appear in the final message list, and identify blocks that are in the wrong position. Use when the model seems to ignore suggestions, when context seems buried, or when improving where information appears in the prompt pipeline."
when_to_use: when the model isn't acting on injected suggestions, when context or hints seem to get lost, when you suspect prompt ordering is hurting model performance, when asked to improve the prompt pipeline, or when auditing how the prompt is built
falsifiable_if: the audit reports a block in the wrong position but moving it makes no difference, or misses a block that's actually being injected
applies_to:
  - prompt-engineering
  - prompt-pipeline
  - context-injection
  - agentic-loop
  - architecture-audit
allowed_tools:
  - code_read
  - llm_generate
  - run_procedure
summary: Prompt-Architecture-Audit
tags:
  - procedure
  - procedures
---

# Prompt-Architecture-Audit

## When to Run This

Run this when the model seems to ignore something injected into its prompt
(suggestions, hints, context), when you suspect information is buried in the
wrong position, or when you want to systematically improve the prompt
pipeline. The core insight: **position matters**. A suggestion at the
start of a long prompt gets buried under context and history; the same
suggestion as the last thing before the model acts is a starting point.

## What It Does

1. Traces the code that assembles the conversation message list (the `conversation = [...]` block in the chat handler)
2. Maps every block that gets injected and the ORDER it appears in the final prompt
3. Identifies blocks that should be repositioned (actionable suggestions should be LAST; reference material should be EARLY)
4. Proposes the specific code change to reposition blocks
5. Optionally calls `Safe-Write` to apply the change and `Run-Test-Suite` to verify

## Steps

### Step 1: Find the conversation assembly code

1. ```python
import json

# The conversation list is built in chat_handler.py. Find the block
# where `conversation = [...]` is constructed — this is where the
# prompt pipeline's final order is determined.
from custom_tools.code_read import run as _read
backend = "vaultbot_stuff/vaultbot_backend/chat_handler.py"

# Read the region around conversation assembly. Search for the
# conversation list construction.
result = _read({
    "file_path": backend,
    "start_line": 1,
    "end_line": 50
})
print(result)
```

2. [llm: Search chat_handler.py for the line `conversation = [` or `conversation.append`. This is where the final prompt order is set. Read a generous range around it (at least 80 lines) to see every block that gets appended. List every `conversation.append` or `conversation.extend` call you find, in order, with what each one injects.]

### Step 2: Map the injection order

3. ```python
import json

# Now read the system prompt construction — everything that gets appended
# to system_prompt BEFORE the conversation list is built. These are blocks
# injected into the first system message.
# Search for `system_prompt +=` and `system_prompt = system_prompt +` lines.
from custom_tools.code_read import run as _read
result = _read({
    "file_path": "vaultbot_stuff/vaultbot_backend/chat_handler.py",
    "start_line": 1180,
    "end_line": 1320
})
print(result)
```

4. [llm: Build the complete injection map. For each block that gets added to the prompt, record:
  - Block name (e.g., "stable prompt", "working memory", "procedure surface", "procedure hint", "vault context", "conversation history", "user message", "suggested action")
  - Where it's injected (system message 1? system message 2? appended after user? etc.)
  - What MESSAGE INDEX it ends up at in the final list
  - Whether it's REFERENCE material (should be early) or ACTIONABLE (should be late/last)

Output a JSON array of {block, location, message_index, type: "reference"|"actionable"|"history"|"user"}.]

### Step 3: Identify misplacements

5. [llm: Review the injection map from Step 2. Apply these ordering principles:
  - **Reference material** (procedure surface catalog, identity, capability lists) should be EARLY — the model reads it once and refers back.
  - **Actionable suggestions** (procedure hints, suggested actions, task nudges) should be LAST — right before or after the user message, so they're the freshest thing in the model's attention.
  - **Context** (vault context, retrieved notes) should be after reference but before history.
  - **History** goes in the middle (it's managed by the sliding window).
  - **User message** is the anchor — suggestions should be adjacent to it.

Identify any block that violates these principles. For each violation, describe:
  - Which block is misplaced
  - Where it currently sits
  - Where it should sit
  - Why the current position is suboptimal (what symptom it causes)

Output JSON: {"violations": [{"block": "...", "current": "...", "should_be": "...", "reason": "..."}], "none": false}]

### Step 4: Propose the repositioning change

6. [llm: For each violation from Step 3, write the SPECIFIC code change needed:
  - What lines to remove from their current position
  - What lines to add at the new position
  - The variable that needs to capture the block (e.g., store the hint in `_suggested_action` instead of appending to `system_prompt`)
  - The new `conversation.append` or `conversation.insert` call at the target position

Be precise — give exact line numbers and the before/after code. This proposal
will be passed to Safe-Write if the user wants to apply it.]

### Step 5: Optional — apply and verify

7. [llm: If the proposed change should be applied:
  - Call `run_procedure("Safe-Write", {"file_path": "vaultbot_stuff/vaultbot_backend/chat_handler.py", "content": "<the full updated file>"})` to apply the edit with syntax check
  - Call `run_procedure("Run-Test-Suite", {"filter": "not step_gate"})` to verify nothing broke
  - Call `run_procedure("Verify-Backend-Change", {})` to restart and verify health
  - Report the result
Otherwise, output the proposal for review.]