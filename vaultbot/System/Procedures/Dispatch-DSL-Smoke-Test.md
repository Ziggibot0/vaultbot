---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-09
description: "Minimal smoke test for the Dispatch DSL — condition + extract with correct YAML format."
allowed_tools: []
version: 1.0.0
activation: manual
summary: "Tests condition entry with operator inside braces and extract from a list."
tags:
  - procedure
  - procedures
when_to_use: "when the user asks to run this procedure"
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

# Dispatch-DSL-Smoke-Test

Tests that the Dispatch DSL compiler produces clean, runnable Python from YAML entries. Uses `condition` with the operator inside `{{ }}` braces and `extract` from a namespace list.

## Inputs

No inputs required. All values are seeded by the dispatch pipeline itself.

## Dispatch

- extract:
    from: test_data.items
    fields:
      name: name
      value: value
    output_as: filtered_items

- condition:
    if: "{{ filtered_items | length > 0 }}"
    then:
      - run:
          procedure: Nonexistent-Procedure
          args: {}
    else: []
    output_as: chain

## Steps

### Step 1: Run the dispatch pipeline

1. ```python
# Seed test data so extract has something to work with
args = {"test_data": {"items": [{"name": "alpha", "value": 1}, {"name": "beta", "value": 2}]}}
```

2. [validate: contains "chain"]

## Validation

The procedure validates that the dispatch pipeline produces output containing "chain".