---
type: procedure
description: Bare-minimum test — does llm_generate work in a code step?
allowed_tools:
  - llm_generate
model_cartridge: small
version: 1.0.0
activation: manual
status: raw
baseline: true
created: 2026-08-06
summary: Steps
tags:
  - procedure
  - procedures
---

## Steps

1. ```python
result = llm_generate("Say 'hello world' and nothing else.")
```
