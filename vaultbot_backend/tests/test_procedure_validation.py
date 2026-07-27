"""Tests for the structured validation spec (Phase 4).

Covers the three opt-in predicate forms (at_least, contains, matches)
plus the free-text word-overlap fallback.  Each form has a pass case
and a fail case.

See [[Procedure-Subprocess-Architecture]] validation section.
"""
from step_gate_runtime import _parse_validation, _validate_step


# ── at_least ───────────────────────────────────────────────────────────

def test_at_least_notes_pass():
    ok, err = _validate_step("[[A]] [[B]] [[C]]", "at_least 2 notes")
    assert ok
    assert err is None


def test_at_least_notes_fail():
    ok, err = _validate_step("[[A]]", "at_least 2 notes")
    assert not ok
    assert "found 1" in err


def test_at_least_sources_pass():
    ok, _ = _validate_step(
        "see https://x.com and https://y.com", "at_least 2 sources")
    assert ok


def test_at_least_sources_fail():
    ok, _ = _validate_step("no urls here", "at_least 1 source")
    assert not ok


def test_at_least_generic_tokens():
    # Unrecognised unit → token count.
    ok, _ = _validate_step("one two three four", "at_least 3 words")
    assert ok


# ── contains ───────────────────────────────────────────────────────────

def test_contains_double_quote_pass():
    ok, _ = _validate_step("the claim is supported", 'contains "supported"')
    assert ok


def test_contains_single_quote_pass():
    ok, _ = _validate_step("the claim is supported", "contains 'supported'")
    assert ok


def test_contains_fail():
    ok, err = _validate_step("the claim is refuted", 'contains "supported"')
    assert not ok
    assert "not found" in err


# ── matches ────────────────────────────────────────────────────────────

def test_matches_digit_pass():
    ok, _ = _validate_step("error code 42", r"matches /\d+/")
    assert ok


def test_matches_fail():
    ok, _ = _validate_step("no digits here", r"matches /\d+/")
    assert not ok


def test_matches_invalid_regex():
    ok, err = _validate_step("anything", "matches /(/")
    assert not ok
    assert "invalid regex" in err


# ── fallback ────────────────────────────────────────────────────────────

def test_fallback_word_overlap_pass():
    ok, _ = _validate_step("the note mentions sources", "mention sources")
    assert ok


def test_fallback_word_overlap_fail():
    ok, _ = _validate_step("unrelated content", "mention two note titles")
    assert not ok


# ── parse_validation ────────────────────────────────────────────────────

def test_parse_at_least():
    pred = _parse_validation("at_least 2 notes")
    assert pred["form"] == "at_least"
    assert pred["n"] == 2
    assert pred["unit"] == "notes"


def test_parse_contains():
    pred = _parse_validation('contains "literal"')
    assert pred["form"] == "contains"
    assert pred["literal"] == "literal"


def test_parse_matches():
    pred = _parse_validation(r"matches /\d+/")
    assert pred["form"] == "matches"
    assert pred["pattern"] == r"\d+"


def test_parse_unknown_returns_none():
    assert _parse_validation("just some free text") is None