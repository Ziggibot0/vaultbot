---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-14
description: "Auto-generated from Behavioral-Pattern-Mine. Wraps the recurring pattern: thought -> md_safe_replace -> md_safe_replace -> md_safe_replace -> md_safe_replace. Observed in 5 sessions (observed in session dbb290f6-b50a-45dd-8981-eaec9c142efd) (priority: 25)."
when_to_use: "When you need to thought, then md_safe_replace, then md_safe_replace, then md_safe_replace, then md_safe_replace."
falsifiable_if: "The pattern thought -> md_safe_replace -> md_safe_replace -> md_safe_replace -> md_safe_replace does not actually recur in practice, or the sequence is better done with individual tool calls."
applies_to:
  - automation
  - auto-generated
allowed_tools:
  - md_safe_replace
  - thought
summary: |
  Auto-generated from Behavioral-Pattern-Mine. Wraps the sequence: thought -> md_safe_replace -> md_safe_replace -> md_safe_replace -> md_safe_replace.
  Observed in 5 sessions (observed in session dbb290f6-b50a-45dd-8981-eaec9c142efd).
tags:
  - procedure
  - auto-generated
  - experimental
---

# thought-to-md-safe-replace

## Purpose

Auto-generated procedure for the recurring tool-call pattern observed across 5 sessions (observed in session dbb290f6-b50a-45dd-8981-eaec9c142efd): `thought -> md_safe_replace -> md_safe_replace -> md_safe_replace -> md_safe_replace`.

This procedure was created by [[Dream-Pattern-To-Procedure]] during a [[Dream-Pass]]. It starts as experimental -- the grading loop will promote or demote it based on actual usage.

## Steps

### Step 1: Think through the problem

[llm: Think through the problem with thought]

### Step 2: Apply the markdown fix

[llm: Apply the markdown fix with md_safe_replace]

### Step 3: Apply the markdown fix

[llm: Apply the markdown fix with md_safe_replace]

### Step 4: Apply the markdown fix

[llm: Apply the markdown fix with md_safe_replace]

### Step 5: Apply the markdown fix

[llm: Apply the markdown fix with md_safe_replace]

[validate: at_least 0 result]
