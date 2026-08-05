---
type: procedure
status: active
model_cartridge: small
created: 2026-08-04
description: Debug test to inspect what run_procedure returns inside a code step
summary: "Step 1: Inspect run_procedure return value"
tags:
  - procedure
  - procedures
---

## Step 1: Inspect run_procedure return value

1. ```python
import json

result = run_procedure("Dream-Scan")
result_type = str(type(result))
result_keys = str(list(result.keys()) if isinstance(result, dict) else "NOT A DICT")
result_repr = json.dumps(result, default=str) if isinstance(result, (dict, list)) else str(result)

output = json.dumps({
    "type": result_type,
    "keys": result_keys,
    "repr": result_repr[:500]
})
```