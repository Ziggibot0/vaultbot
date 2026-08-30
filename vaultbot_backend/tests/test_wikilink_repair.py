"""Tests for wikilink_repair.py — closed-set citation repair (issue #335).

Pure logic: no I/O, no LLM, no vault access. The repair universe is whatever
candidate mapping the caller passes in; the module never decides policy on
its own.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

from custom_tools.wikilink_repair import (
    build_alias_map,
    repair_wikilinks_in_text,
    repair_wikilinks_verified,
    try_repair_stem,
)

ALLOWED = {
    "Chat-sup-homie": {"file_path": "myvault/x/Chat-sup-homie.md", "snippet": ""},
    "FAISS-IndexIDMap2": {"file_path": "myvault/k/FAISS.md", "snippet": ""},
    "Diagnose-Hallucinated-Sources": {
        "file_path": "myvault/p/Diagnose.md",
        "snippet": "",
    },
}


class TestTryRepairStem:
    def test_exact_stem_is_not_a_repair(self):
        # An exactly-valid stem must not be touched and must not be
        # reported as a repair.
        assert try_repair_stem("Chat-sup-homie", ALLOWED) is None

    def test_space_mangled_stem_is_repaired(self):
        # The EXACT regression from issue #335.
        assert try_repair_stem("Chat- sup- homie", ALLOWED) == "Chat-sup-homie"
        assert try_repair_stem("Chat-sup- homie", ALLOWED) == "Chat-sup-homie"

    def test_case_only_difference_is_repaired(self):
        assert try_repair_stem("chat-sup-homie", ALLOWED) == "Chat-sup-homie"

    def test_punct_mangled_stem_is_repaired(self):
        assert try_repair_stem("Chat sup homie", ALLOWED) == "Chat-sup-homie"

    def test_unknown_dissimilar_stem_gets_no_unsafe_repair(self):
        # A hallucinated title that looks nothing like any allowed stem
        # must NOT be rewritten to a real note.
        assert try_repair_stem("Completely-Unrelated-Novel-Title", ALLOWED) is None

    def test_similarity_below_threshold_is_rejected(self):
        # A 7-char probe vs a 5-char stem: ratio ~0.45 — below the floor.
        # Also guards the length-gap rule for short candidates.
        short = {"Jedi": ALLOWED["FAISS-IndexIDMap2"]}
        assert try_repair_stem("Jupyter", short) is None

    def test_ties_are_deterministic(self):
        # If two candidates score identically, the shorter stem wins, then
        # lexicographic — same inputs must always produce the same output.
        two = {
            "Ab-CD": {"file_path": "a.md", "snippet": ""},
            "Ab-CD-": {"file_path": "b.md", "snippet": ""},
        }
        first = try_repair_stem("Ab CD", two)
        second = try_repair_stem("Ab CD", two)
        assert first == second
        assert first == "Ab-CD"

    def test_empty_inputs(self):
        assert try_repair_stem("", ALLOWED) is None
        assert try_repair_stem("x", {}) is None


class TestRepairWikilinksInText:
    def test_mangled_link_in_text_is_repaired(self):
        text = "See [[Chat- sup- homie]] for context."
        repaired, pairs = repair_wikilinks_in_text(text, ALLOWED)
        assert repaired == "See [[Chat-sup-homie]] for context."
        assert pairs == [("Chat- sup- homie", "Chat-sup-homie")]

    def test_valid_links_are_untouched_and_unreported(self):
        text = (
            "See [[Chat-sup-homie]] and [[FAISS-IndexIDMap2|alias]] "
            "and [[Chat-sup-homie#Section]]."
        )
        repaired, pairs = repair_wikilinks_in_text(text, ALLOWED)
        assert repaired == text
        assert pairs == []

    def test_pipe_and_heading_forms_are_repaired(self):
        text = "A [[Chat- sup- homie|the homie note]] and [[Chat- sup- homie#Bits]]."
        repaired, pairs = repair_wikilinks_in_text(text, ALLOWED)
        assert "[[Chat-sup-homie|the homie note]]" in repaired
        assert "[[Chat-sup-homie#Bits]]" in repaired
        assert pairs == [
            ("Chat- sup- homie", "Chat-sup-homie"),
            ("Chat- sup- homie", "Chat-sup-homie"),
        ]

    def test_links_inside_code_fences_are_not_touched(self):
        text = "```\n[[Chat- sup- homie]]\n```\nSee [[Chat- sup- homie]]."
        repaired, pairs = repair_wikilinks_in_text(text, ALLOWED)
        assert "```\n[[Chat- sup- homie]]\n```" in repaired  # fence untouched
        assert pairs == [("Chat- sup- homie", "Chat-sup-homie")]

    def test_unrepairable_link_is_left_alone(self):
        text = "See [[Totally-Fabricated-Note]]."
        repaired, pairs = repair_wikilinks_in_text(text, ALLOWED)
        assert repaired == text
        assert pairs == []

    def test_order_preserved_for_repeated_repairs(self):
        text = "[[Chat-sup- homie]] then [[Chat- sup- homie]] again."
        _repaired, pairs = repair_wikilinks_in_text(text, ALLOWED)
        assert pairs == [
            ("Chat-sup- homie", "Chat-sup-homie"),
            ("Chat- sup- homie", "Chat-sup-homie"),
        ]


class TestBuildAliasMap:
    def test_maps_lowered_alias_to_real_stem(self):
        m = build_alias_map(ALLOWED)
        assert m["chat-sup-homie"] == "Chat-sup-homie"
        assert m["diagnosehallucinatedsources"] == "Diagnose-Hallucinated-Sources"
        assert m["faiss-indexidmap2"] == "FAISS-IndexIDMap2"
        assert m["faissindexidmap2"] == "FAISS-IndexIDMap2"

    def test_empty(self):
        assert build_alias_map({}) == {}


class TestGraphVerifiedFallback:
    def test_falls_back_to_graph_verified_stem(self):
        graph = {"Jedi-Code-Understanding": True}
        provider_returns = ["Jedi-Code-Understanding", "Some-Other-Note"]

        def provider(_link: str) -> list[str]:
            return provider_returns

        def lookup(stem: str) -> bool:
            return graph.get(stem, False)

        text = "See [[Jedi Code Understanding]]."
        repaired, pairs = repair_wikilinks_verified(
            text,
            {"Unrelated-Note": {"file_path": "u.md", "snippet": ""}},
            lookup,
            provider,
        )
        assert pairs == [("Jedi Code Understanding", "Jedi-Code-Understanding")]
        assert "[[Jedi-Code-Understanding]]" in repaired

    def test_never_repairs_to_a_stem_the_graph_rejects(self):
        def provider(_link):
            return ["Jedi-Code-Understanding"]

        def lookup(_stem):
            return False

        text = "See [[Jedi Code Understanding]]."
        repaired, pairs = repair_wikilinks_verified(text, {}, lookup, provider)
        assert pairs == [] and repaired == text

    def test_no_provider_degrades_to_tier1_only(self):
        def lookup(_stem):
            return True

        # No provider -> Tier 2 can't run, Tier 1 result is the answer.
        text = "See [[Chat- sup- homie]]."
        repaired, pairs = repair_wikilinks_verified(
            text,
            {"Chat-sup-homie": {"file_path": "c.md", "snippet": ""}},
            lookup,
            None,
        )
        assert pairs == [("Chat- sup- homie", "Chat-sup-homie")]
        assert "[[Chat-sup-homie]]" in repaired

    def test_no_graph_lookup_degrades_to_closed_set_only(self):
        # Callers without a graph still get tier 1 (the closed set) —
        # this is the repair path called directly.
        text = "See [[Chat- sup- homie]]."
        repaired, pairs = repair_wikilinks_in_text(
            text, {"Chat-sup-homie": {"file_path": "c.md", "snippet": ""}}
        )
        assert pairs and "Chat-sup-homie" in repaired
