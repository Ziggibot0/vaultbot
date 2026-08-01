---
type: procedure
status: experimental
model_cartridge: small
created: 2026-07-31
description: "Summarize a conversation transcript concisely, preserving the user's goal, key decisions, important facts, and open questions. Returns a brief summary (max 500 tokens). Uses the small model — summarization is simple condensation."
when_to_use: "when the conversation history is getting too long and needs to be compacted"
falsifiable_if: "the summary loses the user's original goal or key decisions, or includes fabricated content not in the transcript"
applies_to:
  - compaction
  - conversation-management
  - context-compression
allowed_tools:
  - llm_generate
---

# Summarize-Conversation

## When to Run This

Run this procedure when the conversation history is getting too long and needs to be compacted. Summarizes the middle portion while keeping the head and tail verbatim. This is the OpenHands Condenser pattern — summarize early at a conservative threshold rather than waiting until the context window overflows.

## Steps

### Step 1: Receive the transcript and summarize

1. [llm: Summarize the following conversation history concisely. Preserve: the user's original goal, key decisions made, important tool results/facts learned, and any open questions. Be specific and brief (max 500 tokens). Return only the summary, no preamble. The transcript is provided as the prior step context (the conversation messages to summarize).]

### Step 2: Return the summary

2. ```python
result = json.dumps({"summary": output.strip(), "length": len(output.strip())})
```