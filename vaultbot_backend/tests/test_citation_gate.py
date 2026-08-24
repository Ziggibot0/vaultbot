"""Unit tests for citation_gate — closed-set citation enforcement.

These are pure offline tests (no Ollama, no FAISS, no Services). They
verify the closed-set construction, wikilink extraction, grounding score,
reprimand builder, and the retry-trigger logic.
"""

from __future__ import annotations

from citation_gate import (
    add_citation_target,
    build_allowed_citations,
    build_reprimand,
    build_sources_block,
    build_trust_badge,
    detect_coaching_turn,
    detect_conversational,
    detect_temporal_question,
    extract_wikilinks,
    score_grounding,
)


class TestDetectTemporalQuestion:
    def test_recency_question(self):
        assert detect_temporal_question("what were we working on last?") is True

    def test_what_were_we_doing(self):
        assert detect_temporal_question("what were we doing earlier?") is True

    def test_most_recent(self):
        assert detect_temporal_question("what's the most recent thing?") is True

    def test_where_did_we_leave_off(self):
        assert detect_temporal_question("where did we leave off?") is True

    def test_non_temporal(self):
        assert detect_temporal_question("explain the FAISS index") is False

    def test_empty(self):
        assert detect_temporal_question("") is False
        assert detect_temporal_question(None) is False

    def test_case_insensitive(self):
        assert detect_temporal_question("WHAT WERE WE WORKING ON LAST?") is True


class TestDetectConversational:
    def test_short_greeting(self):
        assert detect_conversational("Hey Sean! I'm here and ready.") is True

    def test_sup_homie(self):
        assert detect_conversational("sup homie") is True

    def test_short_no_punctuation(self):
        assert detect_conversational("hi") is True

    def test_long_answer_not_conversational(self):
        # >200 chars -> substantive multi-claim answer, not casual.
        long = (
            "This is a substantive multi-claim answer that explains how the "
            "grounding gate works and why short greetings should be exempted "
            "from the redundant re-citation retry. It goes on for a while and "
            "makes several distinct factual points that need vault grounding. "
            "There is more than one sentence here. And yet another one."
        )
        assert detect_conversational(long) is False

    def test_short_with_multiple_sentence_marks_still_conversational(self):
        # Short AND has punctuation marks -> still casual (length is the signal).
        assert detect_conversational("First claim. Second claim.") is True

    def test_empty(self):
        assert detect_conversational("") is False
        assert detect_conversational(None) is False

    def test_custom_max_len(self):
        assert detect_conversational("a" * 300, max_len=400) is True
        assert detect_conversational("a" * 500, max_len=400) is False

    def test_greeting_with_wikilink_still_conversational(self):
        # A short greeting that cites a chat-log note is still conversational.
        assert detect_conversational("Hey Sean [[Chat-yo]]") is True


class TestDetectCoachingTurn:
    def test_daily_planning_prompt(self):
        assert detect_coaching_turn("What should I do today?") is True

    def test_burnout_prompt(self):
        msg = "I'm tired and overwhelmed, help me prioritize"
        assert detect_coaching_turn(msg) is True

    def test_non_coaching_fact_query(self):
        assert detect_coaching_turn("Explain how FAISS works") is False

    def test_empty(self):
        assert detect_coaching_turn("") is False
        assert detect_coaching_turn(None) is False


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
