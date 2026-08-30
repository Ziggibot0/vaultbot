"""Unit tests for citation_gate — closed-set citation enforcement.

These are pure offline tests (no Ollama, no FAISS, no Services). They
verify the closed-set construction, wikilink extraction, grounding score,
reprimand builder, and the retry-trigger logic.

Also guards against the return of the lexical intent classifiers
(conversational/coaching/temporal) that were removed: the repo's rule is
"no lexical keyword list — FUSED retrieval and the model decide
relevance", and those heuristics misfired and wasted an LLM round at the
start of every agentic turn. If one of them is ever re-added, this fails.
"""

from __future__ import annotations

from importlib import import_module

import pytest
from citation_gate import (
    add_citation_target,
    build_allowed_citations,
    build_reprimand,
    build_sources_block,
    build_trust_badge,
    extract_wikilinks,
    score_grounding,
)

pytestmark = pytest.mark.unit


class TestNoLexicalIntentClassifiers:
    """Guard: the cheap bs detectors must stay gone."""

    def test_conversational_detector_absent(self):
        assert hasattr(import_module("citation_gate"), "detect_conversational") is False

    def test_coaching_detector_absent(self):
        assert hasattr(import_module("citation_gate"), "detect_coaching_turn") is False
        assert (
            hasattr(import_module("citation_gate"), "classify_coaching_turn") is False
        )

    def test_temporal_detector_absent(self):
        assert (
            hasattr(import_module("citation_gate"), "detect_temporal_question") is False
        )

    def test_conversational_tunable_absent(self):
        from config import TUNABLES

        assert hasattr(TUNABLES, "conversational_max_len") is False

    def test_idk_detector_kept(self):
        # The content-based escape (judges the ANSWER, not the user's intent)
        # must remain.
        assert hasattr(import_module("citation_gate"), "detect_idk") is True


class TestExtractWikilinks:
    def test_simple(self):
        assert extract_wikilinks("see [[Note-A]] for details") == ["Note-A"]

    def test_alias(self):
        assert extract_wikilinks("[[Note-A|the first note]]") == ["Note-A"]

    def test_heading(self):
        assert extract_wikilinks("[[Note-A#Section]]") == ["Note-A"]

    def test_dedup_preserves_order(self):
        assert extract_wikilinks("[[B]] [[A]] [[B]]") == ["B", "A"]

    def test_empty(self):
        assert extract_wikilinks("") == []
        assert extract_wikilinks("no links here") == []

    def test_strips_whitespace(self):
        assert extract_wikilinks("[[ Note-A ]]") == ["Note-A"]


class TestBuildAllowedCitations:
    def test_from_headers(self):
        ctx = (
            "VAULT CONTEXT\n\n"
            "--- CONNECTED NOTES ---\n"
            "### [[Alpha]]\nbody alpha\n\n"
            "### [[Beta]]\nbody beta\n"
        )
        allowed = build_allowed_citations(ctx)
        assert set(allowed.keys()) == {"Alpha", "Beta"}

    def test_from_search_results_backfills_paths(self):
        ctx = "### [[Alpha]]\nbody\n"
        results = [
            {"file_path": "/vault/Alpha.md", "content": "alpha body"},
            {"file_path": "/vault/Beta.md", "content": "beta body"},
        ]
        allowed = build_allowed_citations(ctx, results)
        assert set(allowed.keys()) == {"Alpha", "Beta"}
        assert allowed["Alpha"]["file_path"] == "/vault/Alpha.md"
        assert "alpha body" in allowed["Alpha"]["snippet"]

    def test_empty_context_with_results(self):
        results = [{"file_path": "/v/X.md", "content": "x"}]
        allowed = build_allowed_citations("", results)
        assert set(allowed.keys()) == {"X"}

    def test_empty(self):
        assert build_allowed_citations("", None) == {}


class TestAddCitationTarget:
    def test_adds_new(self):
        allowed: dict = {}
        add_citation_target(allowed, "/vault/Gamma.md", "gamma body")
        assert "Gamma" in allowed
        assert allowed["Gamma"]["file_path"] == "/vault/Gamma.md"

    def test_idempotent_does_not_overwrite(self):
        allowed = {"Gamma": {"file_path": "/original/Gamma.md", "snippet": "orig"}}
        add_citation_target(allowed, "/new/Gamma.md", "new body")
        assert allowed["Gamma"]["file_path"] == "/original/Gamma.md"

    def test_empty_path_noop(self):
        allowed: dict = {}
        add_citation_target(allowed, "", "x")
        assert allowed == {}

    def test_table_target_records_source_type(self):
        allowed: dict = {}
        add_citation_target(
            allowed, "/vault/sales.csv", "Region, Revenue", source_type="table"
        )
        assert allowed["sales"]["source_type"] == "table"


class TestScoreGrounding:
    def test_all_cited_passes(self):
        allowed = {"Alpha": {}, "Beta": {}}
        answer = "Alpha is first [[Alpha]]. Beta is second [[Beta]]."
        score = score_grounding(answer, allowed)
        assert score["failed"] is False
        assert score["allowed_cited"] == 2
        assert score["grounding_score"] == 1.0
        assert score["ungrounded_sentences"] == 0

    def test_zero_wikilinks_with_sentences_fails(self):
        allowed = {"Alpha": {}}
        answer = "This is a claim. This is another claim. And a third one."
        score = score_grounding(answer, allowed)
        assert score["failed"] is True
        assert score["total_wikilinks"] == 0
        assert score["grounding_score"] == 0.0

    def test_wikilink_not_in_set_counts_missing(self):
        allowed = {"Alpha": {}}
        answer = "See [[Alpha]] and [[Hallucinated-Note]] here."
        score = score_grounding(answer, allowed)
        assert "Hallucinated-Note" in score["missing_from_set"]
        assert score["allowed_cited"] == 1
        assert score["total_wikilinks"] == 2

    def test_ungrounded_ratio_above_threshold_fails(self):
        allowed = {"Alpha": {}}
        # 5 sentences, only 1 cited → 4/5 = 0.8 uncited > 0.30 threshold.
        answer = (
            "First claim is cited [[Alpha]]. "
            "Second claim is not. "
            "Third is not either. "
            "Fourth has nothing. "
            "Fifth is bare."
        )
        score = score_grounding(answer, allowed)
        assert score["sentences"] == 5
        assert score["ungrounded_sentences"] == 4
        assert score["failed"] is True

    def test_short_answer_not_subject_to_ratio(self):
        # 2 sentences, 1 uncited — but <3 sentences so ratio gate doesn't fire.
        allowed = {"Alpha": {}}
        answer = "One cited [[Alpha]]. Two not."
        score = score_grounding(answer, allowed)
        assert score["failed"] is False  # has a wikilink, short enough

    def test_graph_lookup_detects_broken_citations(self):
        allowed = {"Alpha": {}, "Broken": {}}

        def lookup(stem):
            return stem == "Alpha"  # "Broken" doesn't exist in graph

        score = score_grounding(
            "See [[Alpha]] and [[Broken]].", allowed, graph_lookup=lookup
        )
        assert "Broken" in score["missing_from_vault"]

    def test_table_citation_does_not_require_markdown_graph_node(self):
        allowed = {
            "sales": {
                "file_path": "/vault/sales.csv",
                "source_type": "table",
            }
        }

        score = score_grounding(
            "Revenue was 150 [[sales]].",
            allowed,
            graph_lookup=lambda stem: False,
        )

        assert score["missing_from_vault"] == []
        assert score["allowed_cited"] == 1

    def test_empty_answer_is_ok(self):
        score = score_grounding("", {})
        assert score["failed"] is False
        assert score["grounding_score"] == 1.0


class TestBuildReprimand:
    def test_includes_allowed_stems(self):
        allowed = {"Alpha": {}, "Beta": {}}
        score = {
            "grounding_score": 0.0,
            "allowed_cited": 0,
            "total_wikilinks": 0,
            "ungrounded_sentences": 3,
            "sentences": 3,
            "missing_from_set": [],
        }
        msg = build_reprimand(score, allowed)
        assert "[[Alpha]]" in msg
        assert "[[Beta]]" in msg
        assert "uncited" in msg.lower() or "forbidden" in msg.lower()

    def test_includes_missing_set(self):
        allowed = {"Alpha": {}}
        score = {
            "grounding_score": 0.5,
            "allowed_cited": 1,
            "total_wikilinks": 2,
            "ungrounded_sentences": 1,
            "sentences": 2,
            "missing_from_set": ["Hallucinated"],
        }
        msg = build_reprimand(score, allowed)
        assert "[[Hallucinated]]" in msg

    def test_empty_allowed_set_says_dont_know(self):
        score = {
            "grounding_score": 0.0,
            "allowed_cited": 0,
            "total_wikilinks": 0,
            "ungrounded_sentences": 2,
            "sentences": 2,
            "missing_from_set": [],
        }
        msg = build_reprimand(score, {})
        assert "I don't know" in msg or "say 'I don't know'" in msg


class TestBuildTrustBadge:
    def test_grounded(self):
        score = {
            "total_wikilinks": 2,
            "allowed_cited": 2,
            "failed": False,
            "grounding_score": 1.0,
        }
        badge = build_trust_badge(score)
        assert "Grounded" in badge
        assert "2" in badge

    def test_ungrounded(self):
        score = {
            "total_wikilinks": 0,
            "allowed_cited": 0,
            "failed": True,
            "grounding_score": 0.0,
        }
        badge = build_trust_badge(score)
        assert "Ungrounded" in badge

    def test_partial(self):
        score = {
            "total_wikilinks": 3,
            "allowed_cited": 1,
            "failed": True,
            "grounding_score": 0.33,
        }
        badge = build_trust_badge(score)
        assert "Partially grounded" in badge
        assert "1/3" in badge

    def test_singular_note(self):
        score = {
            "total_wikilinks": 1,
            "allowed_cited": 1,
            "failed": False,
            "grounding_score": 1.0,
        }
        badge = build_trust_badge(score)
        assert "note" in badge
        assert "notes" not in badge

    def test_graded_badge_with_calibrated_confidence(self):
        score = {
            "total_wikilinks": 2,
            "allowed_cited": 2,
            "failed": False,
            "grounding_score": 1.0,
        }
        confidence = {
            "stage": "grounding",
            "band": "high",
            "calibrated_confidence": 0.82,
        }
        badge = build_trust_badge(score, confidence)
        assert "High confidence" in badge
        assert "82%" in badge
        assert "2/2 citations grounded" in badge

    def test_verified_badge_uses_claim_counts(self):
        score = {
            "total_wikilinks": 2,
            "allowed_cited": 2,
            "failed": False,
            "grounding_score": 1.0,
        }
        confidence = {
            "stage": "verified",
            "band": "moderate",
            "calibrated_confidence": 0.61,
            "supported_claims": 3,
            "total_claims": 5,
        }
        badge = build_trust_badge(score, confidence)
        assert "Moderate confidence" in badge
        assert "61%" in badge
        assert "3/5 claims supported" in badge


class TestBuildSourcesBlock:
    def test_lists_cited_notes(self):
        allowed = {"Alpha": {}, "Beta": {}}
        answer = "Alpha is first [[Alpha]]. Beta is second [[Beta]]."
        block = build_sources_block(answer, allowed)
        assert "## Sources" in block
        assert "[[Alpha]]" in block
        assert "[[Beta]]" in block

    def test_excludes_uncited_notes(self):
        allowed = {"Alpha": {}, "Beta": {}, "Gamma": {}}
        answer = "Only Alpha [[Alpha]]."
        block = build_sources_block(answer, allowed)
        assert "[[Alpha]]" in block
        assert "[[Beta]]" not in block
        assert "[[Gamma]]" not in block

    def test_excludes_hallucinated_links(self):
        allowed = {"Alpha": {}}
        answer = "See [[Alpha]] and [[Hallucinated]]."
        block = build_sources_block(answer, allowed)
        assert "[[Alpha]]" in block
        assert "[[Hallucinated]]" not in block

    def test_empty_when_no_citations(self):
        allowed = {"Alpha": {}}
        answer = "No citations here at all."
        assert build_sources_block(answer, allowed) == ""

    def test_empty_when_no_allowed(self):
        answer = "Cites [[Alpha]] but nothing allowed."
        assert build_sources_block(answer, {}) == ""


class TestScoreGroundingRepairs:
    """score_grounding with repair_pairs (issue #335)."""

    def test_repaired_pair_counts_as_allowed(self):
        from citation_gate import score_grounding

        allowed = {"Chat-sup-homie": {"file_path": "x.md", "snippet": ""}}
        score = score_grounding(
            "Discuss it in [[Chat-sup-homie]].",
            allowed,
            repair_pairs=[("Chat- sup- homie", "Chat-sup-homie")],
        )
        assert score["total_wikilinks"] == 1
        assert score["allowed_cited"] == 1
        assert score["missing_from_set"] == []
        assert score["failed"] is False
        assert score["repaired"] == [("Chat- sup- homie", "Chat-sup-homie")]

    def test_unrepaired_missing_still_missing(self):
        from citation_gate import score_grounding

        allowed = {"Chat-sup-homie": {"file_path": "x.md", "snippet": ""}}
        score = score_grounding("Discuss it in [[Other-Note]].", allowed)
        assert score["missing_from_set"] == ["Other-Note"]
        assert score["grounding_score"] < 1.0

    def test_repair_credit_only_when_corrected_stem_is_allowed(self):
        from citation_gate import score_grounding

        # A repair pair whose REPAIRED stem is NOT in the allowed set must
        # not mint credit for the mangled link (defense-in-depth).
        allowed = {"Unrelated-Note": {"file_path": "x.md", "snippet": ""}}
        score = score_grounding(
            "Discuss it in [[Unrelated-Note]] and [[Chat-sup-homie]].",
            allowed,
            repair_pairs=[("Chat- sup- homie", "Chat-sup-homie")],
        )
        assert score["allowed_cited"] == 1  # Unrelated-Note only
        assert "Chat-sup-homie" in score["missing_from_set"]


class TestBuildReprimandRepairs:
    def test_reprimand_mentions_repairs_and_exact_stem_rule(self):
        from citation_gate import build_reprimand, score_grounding

        # The repaired stem is deliberately NOT in the allowed set here, so
        # the answer fails and the reprimand fires with the repair note.
        allowed = {"Unrelated-Note": {"file_path": "x.md", "snippet": ""}}
        score = score_grounding(
            "Discuss it in [[Chat- sup- homie]].",
            allowed,
            repair_pairs=[("Chat- sup- homie", "Chat-sup-homie")],
        )
        score["failed"] = True
        reprimand = build_reprimand(score, allowed)
        assert "[[Chat- sup- homie]] → [[Chat-sup-homie]]" in reprimand
        assert "Copy citation stems" in reprimand
