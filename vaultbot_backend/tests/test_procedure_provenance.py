"""Tests for provenance (#64) and rationale (#63) warnings.

The validator warns (does not error) when a procedure has no provenance
(sources: / depends_on: / ## Related wikilinks) and no ``## Why This Exists``
rationale section. These are soft signals — the ~200 existing procedures
predate the convention, so they surface as warnings, not hard errors.
"""

import pytest

pytestmark = pytest.mark.unit

from procedure_validator import validate_procedure_text

_BODY = """
# Test-Proc

## Steps

### Step 1: Do the thing

```python
result = {"ok": True}
```
"""

_FM = (
    "---\n"
    "type: procedure\n"
    "status: experimental\n"
    'description: "test proc"\n'
    "when_to_use: testing\n"
    "falsifiable_if: it fails\n"
    "allowed_tools:\n"
    "  - code_read\n"
    "---\n"
)


def _warnings(text: str) -> list[str]:
    return validate_procedure_text(text).get("warnings", [])


def test_invalid_model_cartridge_errors():
    invalid = "med" + "ium"
    fm = _FM.replace("---\n", f"---\nmodel_cartridge: {invalid}\n", 1)
    result = validate_procedure_text(fm + _BODY)

    assert result["passed"] is False
    assert any("Invalid 'model_cartridge'" in e for e in result["errors"])


def test_no_provenance_warns():
    """A procedure with no sources/depends_on/## Related warns."""
    warnings = _warnings(_FM + _BODY)
    assert any("No provenance" in w for w in warnings)


def test_sources_satisfies_provenance():
    """A sources: frontmatter field satisfies the provenance check."""
    fm = _FM.replace(
        "allowed_tools:\n", "sources:\n  - https://example.com\nallowed_tools:\n"
    )
    warnings = _warnings(fm + _BODY)
    assert not any("No provenance" in w for w in warnings)


def test_depends_on_satisfies_provenance():
    """A depends_on: frontmatter field satisfies the provenance check."""
    fm = _FM.replace(
        "allowed_tools:\n", "depends_on:\n  - '[[Think]]'\nallowed_tools:\n"
    )
    warnings = _warnings(fm + _BODY)
    assert not any("No provenance" in w for w in warnings)


def test_research_sources_satisfies_provenance():
    """A research_sources: frontmatter field satisfies the provenance check."""
    fm = _FM.replace(
        "allowed_tools:\n", "research_sources:\n  - '[[Some-Note]]'\nallowed_tools:\n"
    )
    warnings = _warnings(fm + _BODY)
    assert not any("No provenance" in w for w in warnings)


def test_related_wikilink_satisfies_provenance():
    """A ## Related section with a wikilink satisfies the provenance check."""
    body = _BODY + "\n## Related\n\n- [[Some-Note]]\n"
    warnings = _warnings(_FM + body)
    assert not any("No provenance" in w for w in warnings)


def test_related_without_wikilink_still_warns():
    """A ## Related section with no wikilink does NOT satisfy provenance."""
    body = _BODY + "\n## Related\n\n- just some text, no link\n"
    warnings = _warnings(_FM + body)
    assert any("No provenance" in w for w in warnings)


def test_no_rationale_warns():
    """A procedure with no ## Why This Exists section warns."""
    warnings = _warnings(_FM + _BODY)
    assert any("Why This Exists" in w for w in warnings)


def test_rationale_satisfies():
    """A ## Why This Exists section satisfies the rationale check."""
    body = _BODY + "\n## Why This Exists\n\nIt fixes a real gap.\n"
    warnings = _warnings(_FM + body)
    assert not any("Why This Exists" in w for w in warnings)
