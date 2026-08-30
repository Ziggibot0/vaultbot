"""Tests for note_schema.py — universal frontmatter schema injection + validation."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

pytestmark = pytest.mark.unit

from note_schema import (
    heal_note_on_disk,
    heal_vault_schema,
    inject_schema,
    parse_frontmatter,
    split_note_if_needed,
    strip_frontmatter,
    validate_schema,
)


def test_no_frontmatter_auto_inject():
    """Note with no frontmatter gets all required fields."""
    content = "# Mitosis\n\nCell division process."
    result = inject_schema(content, "vaultbot-stuff/Knowledge/Research/Mitosis.md")
    fm = parse_frontmatter(result)
    assert fm.get("type") == "research"
    assert fm.get("status") == "raw"
    assert fm.get("created") is not None
    assert fm.get("summary") == "Mitosis"
    assert "research" in fm.get("tags", [])
    ok, errors, _ = validate_schema(result)
    assert ok, f"Should be valid: {errors}"


def test_partial_frontmatter_fill_missing():
    """Note with partial frontmatter gets missing fields filled."""
    content = "---\ntype: research\nstatus: draft\n---\n# Photosynthesis\n\nPlants."
    result = inject_schema(content, "vaultbot-stuff/Knowledge/Research/Photo.md")
    fm = parse_frontmatter(result)
    assert fm.get("type") == "research"
    assert fm.get("status") == "draft"
    assert "created" in fm
    assert "summary" in fm
    assert "tags" in fm


def test_claim_fields_preserved():
    """Optional claim fields are preserved through injection."""
    content = (
        "---\n"
        "type: claim\n"
        "status: draft\n"
        "created: 2026-08-03\n"
        'summary: "Test claim"\n'
        "tags: [claim]\n"
        'supports: ["[[Other-Note]]"]\n'
        "confidence: 0.85\n"
        'falsifiable_if: "test condition"\n'
        "---\n"
        "# Test Claim\n\nBody."
    )
    result = inject_schema(content, "vaultbot-stuff/System/Architecture/Test.md")
    fm = parse_frontmatter(result)
    assert fm.get("supports") == ["[[Other-Note]]"]
    assert fm.get("confidence") == "0.85"
    assert fm.get("falsifiable_if") == "test condition"
    ok, errors, _ = validate_schema(result)
    assert ok, f"Claim should be valid: {errors}"


def test_invalid_type_rejected():
    """Invalid type value is caught by validation."""
    content = "---\ntype: garbage\nstatus: raw\n---\n# Bad"
    ok, errors, _ = validate_schema(content)
    assert not ok
    assert any("type" in e for e in errors)


def test_invalid_status_rejected():
    """Invalid status value is caught by validation."""
    content = "---\ntype: research\nstatus: bogus\n---\n# Bad"
    ok, errors, _ = validate_schema(content)
    assert not ok
    assert any("status" in e for e in errors)


def test_overwrite_preserves_status_and_created():
    """Overwriting a note preserves its existing status and created date."""
    existing = (
        "---\ntype: research\nstatus: verified\n"
        "created: 2026-07-15\nsummary: old\n---\n# Old"
    )
    new_content = "# Updated Title\n\nNew content."
    result = inject_schema(
        new_content,
        "vaultbot-stuff/Knowledge/Research/Test.md",
        existing_content=existing,
    )
    fm = parse_frontmatter(result)
    assert fm.get("status") == "verified"
    assert fm.get("created") == "2026-07-15"


def test_path_inference_research():
    """Type is inferred from path."""
    result = inject_schema("# X\n\nbody", "vaultbot-stuff/Knowledge/Research/X.md")
    fm = parse_frontmatter(result)
    assert fm.get("type") == "research"


def test_path_inference_chat():
    """Session log events get type: chat."""
    result = inject_schema(
        "# Event\n\nbody", "vaultbot-stuff/Memory/Logs/sid/event-0001.md"
    )
    fm = parse_frontmatter(result)
    assert fm.get("type") == "chat"


def test_path_inference_procedure():
    """Procedure notes get type: procedure."""
    result = inject_schema("# Proc\n\nbody", "vaultbot-stuff/System/Procedures/Proc.md")
    fm = parse_frontmatter(result)
    assert fm.get("type") == "procedure"


def test_path_inference_default_claim():
    """Unknown paths get type: claim."""
    result = inject_schema("# X\n\nbody", "vaultbot/X.md")
    fm = parse_frontmatter(result)
    assert fm.get("type") == "claim"


def test_force_type_override():
    """Caller can force the type."""
    result = inject_schema("# X\n\nbody", "vaultbot/X.md", force_type="semantic")
    fm = parse_frontmatter(result)
    assert fm.get("type") == "semantic"


def test_split_multi_topic_note():
    """Note with multiple distinct H2 sections is flagged for splitting."""
    content = (
        "---\ntype: research\nstatus: draft\n"
        "created: 2026-08-03\nsummary: big\n---\n"
        "# Big Note\n\n"
        "## Mitosis\n\n" + "Mitosis is cell division. " * 20 + "\n\n"
        "## Photosynthesis\n\n" + "Photosynthesis converts light. " * 20 + "\n\n"
        "## Cellular Respiration\n\n" + "Respiration produces ATP. " * 20 + "\n\n"
    )
    result = split_note_if_needed(content, "vaultbot-stuff/Knowledge/Research/Big.md")
    assert result is not None
    assert len(result) == 3
    titles = [p["title"] for p in result]
    assert "Mitosis" in titles
    assert "Photosynthesis" in titles


def test_no_split_single_argument():
    """Note with one argument in multiple sections is NOT split."""
    content = (
        "---\ntype: research\nstatus: draft\n"
        "created: 2026-08-03\nsummary: single\n---\n"
        "# Single Arg\n\n"
        "## Introduction\n\n" + "Intro text. " * 30 + "\n\n"
        "## Evidence\n\n" + "Evidence text. " * 30 + "\n\n"
        "## Conclusion\n\n" + "Conclusion text. " * 30 + "\n\n"
    )
    result = split_note_if_needed(
        content, "vaultbot-stuff/Knowledge/Research/Single.md"
    )
    assert result is None


def test_no_split_procedure():
    """Procedures are never split."""
    content = (
        "---\ntype: procedure\nstatus: experimental\n---\n"
        "# My Procedure\n\n"
        "## Step 1\n\n" + "Do step 1. " * 30 + "\n\n"
        "## Step 2\n\n" + "Do step 2. " * 30 + "\n\n"
        "## Step 3\n\n" + "Do step 3. " * 30 + "\n\n"
    )
    result = split_note_if_needed(
        content, "vaultbot-stuff/System/Procedures/My-Proc.md"
    )
    assert result is None


def test_strip_frontmatter():
    """strip_frontmatter returns body only."""
    content = "---\ntype: research\n---\n# Title\n\nBody text."
    body = strip_frontmatter(content)
    assert "type: research" not in body
    assert "# Title" in body


def test_existing_fields_not_overwritten():
    """inject_schema never overwrites a field the caller provided."""
    content = (
        "---\ntype: architecture\nstatus: verified\ncreated: 2020-01-01\n"
        "summary: mine\ntags: [custom]\n---\n# X\n\nbody"
    )
    result = inject_schema(content, "vaultbot-stuff/System/Architecture/X.md")
    fm = parse_frontmatter(result)
    assert fm.get("type") == "architecture"
    assert fm.get("status") == "verified"
    assert fm.get("created") == "2020-01-01"
    assert fm.get("summary") == "mine"
    assert fm.get("tags") == ["custom"]


def test_empty_frontmatter_error():
    """No frontmatter at all → validation reports error."""
    ok, errors, _ = validate_schema("# Just a title\n\nNo frontmatter.")
    assert not ok
    assert any("frontmatter" in e.lower() for e in errors)


# ── Boot-time healing tests ──────────────────────────────────────────

import shutil
import tempfile


def test_heal_note_adds_missing_fields():
    """heal_note_on_disk writes schema to a note that has no frontmatter."""
    tmp = tempfile.mkdtemp()
    try:
        note = os.path.join(tmp, "vaultbot", "Knowledge", "Research")
        os.makedirs(note)
        p = os.path.join(note, "Test-Heal.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# Test Heal\n\nNo frontmatter here.")
        result = heal_note_on_disk(p, tmp)
        assert result["healed"] is True
        assert "added type" in result["changes"]
        # Verify on disk
        with open(p, encoding="utf-8") as f:
            content = f.read()
        fm = parse_frontmatter(content)
        assert fm.get("type") == "research"
        assert fm.get("status") == "raw"
    finally:
        shutil.rmtree(tmp)


def test_heal_note_skips_valid_note():
    """heal_note_on_disk does NOT rewrite a note that already has schema."""
    tmp = tempfile.mkdtemp()
    try:
        note = os.path.join(tmp, "vaultbot", "System", "Architecture")
        os.makedirs(note)
        p = os.path.join(note, "Good.md")
        content = (
            "---\ntype: architecture\nstatus: verified\n"
            "created: 2026-01-01\nsummary: good\ntags: [architecture]\n---\n"
            "# Good\n\nAlready has schema."
        )
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        result = heal_note_on_disk(p, tmp)
        assert result["healed"] is False
    finally:
        shutil.rmtree(tmp)


def test_heal_vault_schema_scans_all():
    """heal_vault_schema scans a vault and heals notes missing schema."""
    tmp = tempfile.mkdtemp()
    try:
        # Create notes: one with schema, one without, one in a skipped dir
        os.makedirs(os.path.join(tmp, "vaultbot-stuff", "Knowledge", "Research"))
        os.makedirs(os.path.join(tmp, "vaultbot-stuff", "Memory", "Logs", "sid"))

        with_schema = os.path.join(
            tmp, "vaultbot-stuff", "Knowledge", "Research", "Good.md"
        )
        with open(with_schema, "w", encoding="utf-8") as f:
            f.write(
                "---\ntype: research\nstatus: raw\ncreated: 2026-08-03\n"
                "summary: x\ntags: [research]\n---\n# Good\n\nbody"
            )

        without_schema = os.path.join(
            tmp, "vaultbot-stuff", "Memory", "Logs", "sid", "event-0001.md"
        )
        with open(without_schema, "w", encoding="utf-8") as f:
            f.write("# Chat Test\n\nNo frontmatter.")

        result = heal_vault_schema(tmp)
        assert result["scanned"] >= 2
        assert result["healed"] >= 1
        # Verify the chat note got type: chat
        with open(without_schema, encoding="utf-8") as f:
            healed = f.read()
        fm = parse_frontmatter(healed)
        assert fm.get("type") == "chat"
    finally:
        shutil.rmtree(tmp)


def test_heal_vault_schema_skips_source_docs():
    """heal_vault_schema must NOT inject frontmatter into repo source docs.

    Regression test for the bug where the broad "vaultbot/" prefix matched
    vaultbot/README.md, vaultbot/ARCHITECTURE.md, vaultbot/docs/*.md, and
    vaultbot-stuff/baseline/*.md — auto-injecting `type: claim` frontmatter into
    the repo's own source documentation on every boot.
    """
    tmp = tempfile.mkdtemp()
    try:
        # A source doc directly under vaultbot/ (not a knowledge zone)
        os.makedirs(os.path.join(tmp, "vaultbot", "docs"))
        source_doc = os.path.join(tmp, "vaultbot", "docs", "ARCHITECTURE.md")
        with open(source_doc, "w", encoding="utf-8") as f:
            f.write("# Architecture\n\nThis is a source doc, not a vault note.")

        heal_vault_schema(tmp)
        # The source doc must be untouched (not scanned, not healed)
        with open(source_doc, encoding="utf-8") as f:
            content = f.read()
        assert content == "# Architecture\n\nThis is a source doc, not a vault note."
        assert "type: claim" not in content
    finally:
        shutil.rmtree(tmp)


# ── Issue #176: placeholder summary, duplicate tags, empty Key Findings ──


def test_infer_tags_dedupes_research_dir():
    """_infer_tags must not produce duplicate 'research' tag.

    When note_type='research' and the parent directory is 'Research',
    the tag and dir-name collide — they must be deduped.
    """
    from note_schema import _infer_tags

    tags = _infer_tags("vaultbot-stuff/Knowledge/Research/Topic.md", "research")
    assert tags.count("research") == 1
    # Non-colliding types still get both tags
    tags2 = _infer_tags("vaultbot-stuff/Knowledge/Patterns/Foo.md", "pattern")
    assert "pattern" in tags2
    assert "patterns" in tags2


def test_inject_schema_replaces_placeholder_summary():
    """inject_schema must replace 'summary: SUMMARY' with an inferred value."""
    content = (
        "---\n"
        "type: research\n"
        "status: raw\n"
        "created: 2026-08-22\n"
        "summary: SUMMARY\n"
        "tags: [research]\n"
        "---\n"
        "# Real Title Here\n\nSome content."
    )
    result = inject_schema(content, "vaultbot-stuff/Knowledge/Research/Test.md")
    fm = parse_frontmatter(result)
    # The placeholder must be replaced by the inferred H1 title
    assert fm.get("summary") != "SUMMARY"
    assert fm.get("summary") == "Real Title Here"
    ok, errors, _ = validate_schema(result)
    assert ok, f"Should be valid after injection: {errors}"


def test_inject_schema_replaces_one_line_description_placeholder():
    """inject_schema must replace 'summary: one-line description' placeholder."""
    content = (
        "---\n"
        "type: research\n"
        "status: raw\n"
        "created: 2026-08-22\n"
        "summary: one-line description\n"
        "tags: [research]\n"
        "---\n"
        "# My Topic\n\nContent here."
    )
    result = inject_schema(content, "vaultbot-stuff/Knowledge/Research/My.md")
    fm = parse_frontmatter(result)
    assert fm.get("summary") == "My Topic"


def test_inject_schema_dedupes_existing_duplicate_tags():
    """inject_schema must dedupe tags that the LLM emitted twice."""
    content = (
        "---\n"
        "type: research\n"
        "status: raw\n"
        "created: 2026-08-22\n"
        "summary: A real summary\n"
        "tags:\n"
        "  - research\n"
        "  - research\n"
        "  - biology\n"
        "---\n"
        "# Topic\n\nContent."
    )
    result = inject_schema(content, "vaultbot-stuff/Knowledge/Research/Topic.md")
    fm = parse_frontmatter(result)
    tags = fm.get("tags", [])
    lower_tags = [t.lower() for t in tags]
    assert lower_tags.count("research") == 1
    assert "biology" in lower_tags


def test_inject_schema_dedupes_case_insensitive_tags():
    """inject_schema must dedupe tags case-insensitively (Research vs research)."""
    content = (
        "---\n"
        "type: research\n"
        "status: raw\n"
        "created: 2026-08-22\n"
        "summary: A real summary\n"
        "tags: [Research, research, Biology, biology]\n"
        "---\n"
        "# Topic\n\nContent."
    )
    result = inject_schema(content, "vaultbot-stuff/Knowledge/Research/Topic.md")
    fm = parse_frontmatter(result)
    tags = fm.get("tags", [])
    lower_tags = [t.lower() for t in tags]
    assert lower_tags.count("research") == 1
    assert lower_tags.count("biology") == 1


def test_validate_schema_rejects_placeholder_summary():
    """validate_schema must flag 'summary: SUMMARY' as an error."""
    content = (
        "---\n"
        "type: research\n"
        "status: raw\n"
        "created: 2026-08-22\n"
        "summary: SUMMARY\n"
        "tags: [research]\n"
        "---\n"
        "# Topic\n\nContent."
    )
    ok, errors, _ = validate_schema(content)
    assert not ok
    assert any("placeholder" in e.lower() for e in errors)


def test_validate_schema_rejects_duplicate_tags():
    """validate_schema must flag duplicate tags as an error."""
    content = (
        "---\n"
        "type: research\n"
        "status: raw\n"
        "created: 2026-08-22\n"
        "summary: A real summary\n"
        "tags: [research, research]\n"
        "---\n"
        "# Topic\n\nContent."
    )
    ok, errors, _ = validate_schema(content)
    assert not ok
    assert any("duplicate" in e.lower() for e in errors)


def test_synthesize_note_markdown_never_empty_key_findings():
    """synthesize_note_markdown must never ship an empty Key Findings section.

    Regression for issue #176: when report['synthesis'] is truthy but
    contains only frontmatter, _strip_frontmatter yields an empty string.
    The fallback text must be used so the section is never empty.
    """
    from research_synthesizer import synthesize_note_markdown

    # synthesis is frontmatter-only (truthy but body is empty after strip)
    report = {
        "topic": "Test Topic",
        "synthesis": "---\ntype: research\nsummary: x\ntags: [research]\n---\n",
        "sources": [],
        "source_count": 0,
        "synthesis_facts": 0,
        "gaps_filled": [],
        "rounds": [],
    }
    result = synthesize_note_markdown(report, summary="A summary")
    # The Key Findings section must contain the fallback text, not be empty
    assert "## Key Findings" in result
    findings_idx = result.index("## Key Findings")
    after_header = result[findings_idx + len("## Key Findings") :]
    # Skip to the next section header or end
    next_section = after_header.find("## ")
    if next_section == -1:
        section_content = after_header
    else:
        section_content = after_header[:next_section]
    assert section_content.strip() != "", "Key Findings section must not be empty"
    assert "no corroborated findings" in section_content.lower()


def test_synthesize_note_markdown_key_findings_with_real_content():
    """synthesize_note_markdown preserves real synthesis content under Key Findings."""
    from research_synthesizer import synthesize_note_markdown

    report = {
        "topic": "Real Topic",
        "synthesis": "This is a real finding about the topic.",
        "sources": [],
        "source_count": 1,
        "synthesis_facts": 1,
        "gaps_filled": [],
        "rounds": [],
    }
    result = synthesize_note_markdown(report, summary="A summary")
    assert "## Key Findings" in result
    assert "This is a real finding about the topic." in result


if __name__ == "__main__":
    # Run all tests
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS: {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {test.__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{passed + failed} tests passed")
    if failed:
        sys.exit(1)
