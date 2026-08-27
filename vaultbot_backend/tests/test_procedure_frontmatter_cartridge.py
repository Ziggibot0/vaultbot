"""Regression tests for per-step procedure cartridge selection."""

import pytest

pytestmark = pytest.mark.unit

from procedure_validator import validate_procedure_text


def _procedure(frontmatter_extra: str = "") -> str:
    return f"""---
type: procedure
description: Test procedure
when_to_use: During the regression test
allowed_tools:
  - vault_search
falsifiable_if: The test fails
status: experimental
created: 2026-08-25
summary: Test procedure
tags:
  - procedure
{frontmatter_extra}---
# Test Procedure

## Steps

### Step 1: Search for evidence

```python
result = vault_search(query="evidence")
```
"""


def test_procedure_frontmatter_does_not_require_cartridge():
    result = validate_procedure_text(_procedure())

    assert not any("model_cartridge" in error for error in result["errors"])


def test_procedure_frontmatter_rejects_cartridge():
    result = validate_procedure_text(_procedure("model_cartridge: big\n"))

    assert any(
        "Frontmatter must not declare 'model_cartridge'" in error
        for error in result["errors"]
    )
