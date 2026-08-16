---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-07-31
description: Refine a concept card from an extractive sketch to a tight 2-4 sentence semantic summary. Preserves wikilinks. Uses the small model — it's a simple summarization of existing content.
when_to_use: when a concept card (L1 abstraction) has been retrieved 3+ times and deserves a tighter semantic summary
falsifiable_if: the refined card loses the core idea or drops wikilinks that were in the sketch
applies_to:
  - concept-cards
  - abstraction-hierarchy
  - note-quality
allowed_tools:
  - llm_generate
summary: Refine-Concept-Card
tags:
  - procedure
  - procedures
last_reviewed: 2026-08-15
---

# Refine-Concept-Card

## When to Run This

Run this procedure when a concept card (L1 abstraction) has been retrieved 3+ times and deserves a tighter semantic summary. The card starts as an extractive sketch (top TF-ranked sentences); this procedure rewrites it into 2-4 dense sentences capturing the core idea, definitions, and key formulas. This is the rehearsal-gated refinement — only earned cards get refined.

## Steps

### Step 1: Read the card and refine with the small model

1. [llm: Rewrite the following textbook section as a tight concept-card summary: 2-4 sentences capturing the core idea, definitions, and key formulas. Preserve every [[wikilink]] target verbatim. Do NOT include the heading, source pointer, or link list — only the summary prose. Drop pedagogical scaffolding and worked examples. The section to refine is provided as the prior step context.]

### Step 2: Return the refined card

2. ```python
refined = output.strip()
if len(refined) < 50:
    result = json.dumps({"error": "refined card too short — keeping sketch", "length": len(refined)})
else:
    result = json.dumps({"refined": refined, "length": len(refined)})
```