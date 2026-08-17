"""Tests for procedure_compiler.py — pure deterministic parser.

Covers frontmatter parsing, step parsing (v1 text, v2 code, v2 llm,
multi-line llm), annotation extraction, ``## Steps`` section scoping,
and the ``## Steps``-absent fallback.  No LLM, no vault, no I/O.

See [[Procedure-Subprocess-Architecture]] and
[[Deterministic-Scaffolding-for-Small-Models]].
"""

import pytest

pytestmark = pytest.mark.unit

from procedure_compiler import (
    _extract_annotations,
    _parse_frontmatter,
    _parse_steps,
    compile_from_text,
)


# ── Frontmatter ─────────────────────────────────────────────────────────


def test_parse_frontmatter_flat_kv():
    text = "---\ntype: procedure\nstatus: experimental\n---\nbody"
    fm, _, body = _parse_frontmatter(text)
    assert fm["type"] == "procedure"
    assert fm["status"] == "experimental"
    assert body == "body"


def test_parse_frontmatter_quoted_values():
    text = '---\ndescription: "Verify claims"\n---\nbody'
    fm, _, _ = _parse_frontmatter(text)
    assert fm["description"] == "Verify claims"


def test_parse_frontmatter_list_values():
    text = "---\nallowed_tools:\n  - vault_search\n  - llm_generate\n---\nbody"
    fm, _, _ = _parse_frontmatter(text)
    assert fm["allowed_tools"] == ["vault_search", "llm_generate"]


def test_parse_frontmatter_no_frontmatter():
    fm, _, body = _parse_frontmatter("no frontmatter here")
    assert fm == {}
    assert body == "no frontmatter here"


def test_compile_from_text_non_procedure_returns_none():
    text = "---\ntype: research\n---\n# Not a procedure"
    assert compile_from_text("X", text) is None


def test_compile_from_text_procedure_returns_procedure():
    text = "---\ntype: procedure\n---\n## Steps\n1. Do thing"
    proc = compile_from_text("X", text)
    assert proc is not None
    assert proc.name == "X"
    assert len(proc.steps) == 1


def test_compile_from_text_exemplar_procedure():
    text = "---\nexemplar_procedure: true\n---\n## Steps\n1. Do thing"
    proc = compile_from_text("X", text)
    assert proc is not None


def test_compile_from_text_carries_allowed_tools():
    text = (
        "---\ntype: procedure\nallowed_tools:\n"
        "  - vault_search\n  - llm_generate\n---\n## Steps\n1. Do thing"
    )
    proc = compile_from_text("X", text)
    assert proc is not None
    assert "vault_search" in proc.allowed_tools
    assert "llm_generate" in proc.allowed_tools


# ── Annotation extraction ──────────────────────────────────────────────


def test_extract_annotations_all_three():
    text = "Search the vault [validate: mention 2 note titles] [condition: if < 3 notes] [branch: step 4]"
    clean, val, cond, branch = _extract_annotations(text)
    assert "validate" not in clean.lower()
    assert val == "mention 2 note titles"
    assert cond == "if < 3 notes"
    assert branch == 4


def test_extract_annotations_none():
    clean, val, cond, branch = _extract_annotations("Plain instruction")
    assert clean == "Plain instruction"
    assert val is None
    assert cond is None
    assert branch is None


def test_extract_annotations_validation_only():
    clean, val, cond, branch = _extract_annotations(
        "Check sources [validate: at_least 2 sources]"
    )
    assert val == "at_least 2 sources"
    assert cond is None
    assert branch is None


# ── Step parsing ────────────────────────────────────────────────────────


def test_parse_steps_v1_text_with_annotation():
    body = "## Steps\n1. Search the vault [validate: mention 2 note titles]"
    steps = _parse_steps(body)
    assert len(steps) == 1
    assert steps[0].step_type == "text"
    assert steps[0].validation == "mention 2 note titles"
    assert "validate" not in steps[0].instruction.lower()


def test_parse_steps_v2_code_block():
    body = "## Steps\n1. ```python\nresult = 1 + 1\n```\n"
    steps = _parse_steps(body)
    assert len(steps) == 1
    assert steps[0].step_type == "code"
    assert "result = 1 + 1" in steps[0].code


def test_parse_steps_v2_llm_single_line():
    body = "## Steps\n1. [llm: Given the results, decide.]\n"
    steps = _parse_steps(body)
    assert len(steps) == 1
    assert steps[0].step_type == "llm"
    assert steps[0].llm_instruction == "Given the results, decide."


def test_parse_steps_v2_llm_multi_line():
    body = "## Steps\n1. [llm: Given the results,\n   decide.]\n"
    steps = _parse_steps(body)
    assert len(steps) == 1
    assert steps[0].step_type == "llm"
    assert "Given the results" in steps[0].llm_instruction
    assert "decide" in steps[0].llm_instruction


def test_parse_steps_scoped_to_steps_section():
    body = "## Steps\n1. First step\n2. Second step\n\n## Notes\nThis is not a step"
    steps = _parse_steps(body)
    assert len(steps) == 2
    assert steps[0].instruction == "First step"
    assert steps[1].instruction == "Second step"


def test_parse_steps_fallback_when_no_steps_header():
    body = "1. First step\n2. Second step"
    steps = _parse_steps(body)
    assert len(steps) == 2


def test_parse_steps_mixed_v1_and_v2():
    body = (
        "## Steps\n"
        "1. Search the vault [validate: mention 2 titles]\n"
        "2. ```python\nresult = vault_search('claim')\n```\n"
        "3. [llm: Is the claim supported?]\n"
    )
    steps = _parse_steps(body)
    assert len(steps) == 3
    assert steps[0].step_type == "text"
    assert steps[1].step_type == "code"
    assert steps[2].step_type == "llm"


def test_parse_steps_strips_bold_markers():
    body = "## Steps\n1. **Check authority.** Who wrote this?"
    steps = _parse_steps(body)
    assert len(steps) == 1
    assert "**" not in steps[0].instruction
    assert "Check authority" in steps[0].instruction
