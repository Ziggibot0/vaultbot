"""
Deep research engine with LLM synthesis.

Design goal: "get to the bottom of" a topic. The search/fetch/clean pipeline
is deterministic (no LLM); the synthesis uses ONE LLM call when a client is
provided. If the LLM is unavailable or fails, the synthesis raises — no
silent extractive fallback.

Pipeline:
  1. Extract key terms from the topic (noun phrases, no LLM).
  2. Multi-round search queries with progressive refinement.
  3. Fetch the top sources per round, clean to article text.
  4. Gap detection: identify under-covered facets, run follow-up queries.
  5. Synthesis: ONE LLM call to synthesize all source texts. Raises on
     LLM failure — no extractive fallback.
  6. Return a structured ResearchReport.

The LLM naturally filters irrelevant sources because it understands the
topic. The deterministic pipeline ensures search/fetch/clean is
model-independent and cacheable.
"""

import contextlib
import re
import time
from collections import Counter
from typing import Any, Optional

from research_synthesizer import (
    extractive_synthesis as _extractive_synthesis_fn,
)
from research_synthesizer import (
    get_vault_note_titles as _get_vault_note_titles_fn,
)
from research_synthesizer import (
    llm_synthesize as _llm_synthesize_fn,
)
from research_synthesizer import (
    repair_wikilinks as _repair_wikilinks_fn,
)
from research_synthesizer import (
    synthesize_note_markdown as _synthesize_note_markdown_fn,
)
from research_synthesizer import (
    synthesize_structured_note as _synthesize_structured_note_fn,
)
from source_classification import (
    is_allowlisted as _is_allowlisted,
)
from source_classification import (
    is_blocked_source as _is_blocked_source,
)
from source_classification import (
    is_denylisted as _is_denylisted,
)
from source_classification import (
    is_github_issue_or_pr as _is_github_issue_or_pr,
)
from source_classification import (
    is_low_credibility_domain as _is_low_credibility_domain,
)
from source_classification import normalize_source_domains as _normalize_source_domains
from source_classification import (
    normalize_url as _normalize_url,
)
from source_classification import (
    source_relevance as _source_relevance,
)
from source_credibility import SourceCredibilityTracker
from text_scoring import (
    _GENERIC_TERMS,
    _STOPWORDS,
)
from text_scoring import (
    keyterms as _keyterms,
)
from text_scoring import (
    score_sentence as _score_sentence,
)
from text_scoring import (
    signal_terms as _signal_terms,
)
from text_scoring import (
    tokenize_light as _tokenize_light,
)

try:
    from tavily_client import TavilyClient
except Exception:  # noqa: BLE001 — type-hint-only import; module instance is injected at runtime
    TavilyClient = None  # type: ignore

try:
    from free_search import FreeSearch
except Exception:  # noqa: BLE001 — type-hint-only import; module instance is injected at runtime
    FreeSearch = None  # type: ignore

try:
    from url_liveness import filter_dead_urls as _filter_dead_urls
except Exception:  # noqa: BLE001 — best-effort import, liveness check is optional
    _filter_dead_urls = None  # type: ignore


# --- Compound signal detection (2026-08-17) -------------------------------
# When a topic like "what are sea shells made of" is keyterm-extracted,
# the result is ['shells', 'made', 'sea'] — three separate single-word
# tokens. signal_terms() then keeps only 'shells' (≥5 chars, not generic),
# dropping 'sea' (3 chars) and 'made' (4 chars). The relevance gate ends
# up with a single signal term ['shells'], which matches any arxiv paper
# about "shell galaxies" — the word "shells" is a homonym.
#
# This helper scans the ORIGINAL topic string for adjacent content-word
# pairs (e.g., "sea shells") and adds them as compound signal terms.
# A source about "shell galaxies" won't contain "sea shells", so it
# fails the gate — even with a single-signal-term topic.
_COMPOUND_STOP = frozenset(
    {
        "what",
        "are",
        "is",
        "the",
        "a",
        "an",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "with",
        "by",
        "from",
        "and",
        "or",
        "but",
        "how",
        "why",
        "when",
        "who",
        "where",
        "which",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "can",
        "could",
        "should",
        "would",
        "will",
        "may",
        "might",
        "about",
        "into",
        "than",
        "then",
        "so",
        "if",
        "just",
        "also",
    }
)


def _compound_signals(topic: str) -> list[str]:
    """Extract adjacent content-word pairs from the topic as compound signals.

    "what are sea shells made of" → ["sea shells"]
    "how do bacteria communicate" → ["bacteria communicate"]
    These compounds are more specific than individual words and help the
    relevance gate reject sources that match one word but not the concept.
    Only pairs of content words (len >= 3, not stopwords, not generic verbs)
    are kept. Common verbs like "made", "used", "done" are excluded because
    they form spurious compounds ("shells made") that don't appear in real
    sources even when the topic is correctly covered.
    """
    import re as _re

    tokens = _re.findall(r"[a-z]{3,}", (topic or "").lower())
    # Common verbs/adjectives that form spurious compounds — they're content
    # words by length but too generic to be useful as compound signal terms.
    _SPURIOUS = frozenset(
        {
            "made",
            "used",
            "done",
            "based",
            "using",
            "via",
            "like",
            "also",
            "many",
            "much",
            "some",
            "such",
            "can",
            "may",
            "will",
            "has",
            "had",
            "did",
            "get",
        }
    )
    compounds: list[str] = []
    for i in range(len(tokens) - 1):
        w1, w2 = tokens[i], tokens[i + 1]
        if w1 in _COMPOUND_STOP or w2 in _COMPOUND_STOP:
            continue
        if w1 in _SPURIOUS or w2 in _SPURIOUS:
            continue
        compounds.append(f"{w1} {w2}")
    # Dedup preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for c in compounds:
        if c not in seen:
            out.append(c)
            seen.add(c)
    return out


# Facets are the kinds of questions a "deep" answer should cover. The engine
# detects which facets a topic implies and then checks coverage of each.
_FACET_PATTERNS = [
    (
        "definition",
        re.compile(r"\b(what (is|are)|define|definition|meaning of)\b", re.I),
    ),
    (
        "history",
        re.compile(
            r"\b(history|origin|when (did|was)|who (discovered|invented|founded))\b",
            re.I,
        ),
    ),
    (
        "mechanism",
        re.compile(r"\b(how (does|do|can)|mechanism|process|works?|algorithm)\b", re.I),
    ),
    (
        "examples",
        re.compile(r"\b(examples?|instances?|cases?|kinds?|types?|categories)\b", re.I),
    ),
    (
        "comparison",
        re.compile(r"\b(vs|versus|compare|difference|better|alternatives?)\b", re.I),
    ),
    (
        "pros_cons",
        re.compile(
            r"\b(pros?|cons?|advantages?|disadvantages?|benefits?|drawbacks?|risks?)\b",
            re.I,
        ),
    ),
    ("recent", re.compile(r"\b(recent|latest|current|2024|2025|2026|new)\b", re.I)),
]


def _detect_facets(topic: str) -> list[str]:
    facets = []
    for name, pattern in _FACET_PATTERNS:
        if pattern.search(topic):
            facets.append(name)
    if not facets:
        # Default: at least try to cover definition + examples.
        facets = ["definition", "examples"]
    return facets


def _facet_keywords(facet: str) -> list[str]:
    return {
        "definition": ["definition", "means", "refers to", "is a", "are a"],
        "history": [
            "history",
            "origin",
            "introduced",
            "founded",
            "invented",
            "first described",
            "etymology",
        ],
        "mechanism": [
            "works",
            "mechanism",
            "process",
            "algorithm",
            "how",
            "steps",
            "procedure",
            "method",
        ],
        "examples": [
            "example",
            "such as",
            "including",
            "instance",
            "case",
            "types of",
            "kinds of",
        ],
        "comparison": [
            "versus",
            "compared to",
            "difference",
            "alternative",
            "whereas",
            "unlike",
        ],
        "pros_cons": [
            "advantage",
            "disadvantage",
            "benefit",
            "drawback",
            "risk",
            "limitation",
            "downside",
        ],
        "recent": [
            "recent",
            "latest",
            "current",
            "2024",
            "2025",
            "2026",
            "new study",
            "recently",
        ],
    }.get(facet, [])


# Max length for a focused search topic. Topics longer than this get
# truncated to their first meaningful clause so search engines don't
# choke on 200-char roadmap bullets.
_MAX_SEARCH_TOPIC_CHARS = 120


def _focus_topic(topic: str) -> str:
    """Truncate an overly long topic but preserve high-specificity terms.

    The agent sometimes passes a full roadmap bullet as the research topic
    (e.g. "Bacterial communication: quorum sensing, how bacteria talk to
    each other with chemical signals, biofilm formation, ..."). Search
    engines can't handle that — they return garbage. This takes the first
    clause (split on `:`, `,`, or sentence boundary) and caps it at
    ``_MAX_SEARCH_TOPIC_CHARS``. The full topic stays as the note title;
    the focused version is what gets searched.

    Short topics (≤ the cap) pass through unchanged.

    FIX: Previously cut at the first colon, losing all specific terms after
    it (autoinducer, AHL, biofilm). Now keeps the first clause PLUS extracts
    high-specificity terms from the rest of the topic so they're included
    in the search query.
    """
    if len(topic) <= _MAX_SEARCH_TOPIC_CHARS:
        return topic
    # Try splitting on the first separator to get the first clause.
    first_clause = topic
    for sep in (": ", ", ", " - ", " — ", ". "):
        idx = topic.find(sep)
        if 0 < idx <= 80:
            first_clause = topic[:idx].strip()
            break

    # Extract specific terms from the FULL topic (words > 4 chars, not
    # stopwords, not generic, not already in the first clause).
    # ALSO preserve short all-caps acronyms (AHL, DNA, RNA, ATP) which are
    # high-specificity biology/chemistry terms that get dropped by the >4
    # char filter but are critical for finding the right sources.
    first_low = first_clause.lower()
    all_words = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]+", topic.lower())
    # Find all-caps acronyms from the ORIGINAL text (case-sensitive).
    acronyms = re.findall(r"\b([A-Z]{2,5}[0-9]?)\b", topic)
    _FOCUS_STOP = _STOPWORDS | _GENERIC_TERMS
    extras = []
    seen = set(first_low.split())
    # Add acronyms first (highest specificity per char).
    for ac in acronyms:
        if ac.lower() not in seen:
            extras.append(ac)
            seen.add(ac.lower())
            if len(extras) >= 4:
                break
    for w in all_words:
        if len(w) > 4 and w not in _FOCUS_STOP and w not in seen:
            extras.append(w)
            seen.add(w)
            if len(extras) >= 4:
                break

    result = first_clause
    if extras:
        result += " " + " ".join(extras)

    if len(result) > _MAX_SEARCH_TOPIC_CHARS:
        cut = result[:_MAX_SEARCH_TOPIC_CHARS]
        last_space = cut.rfind(" ")
        if last_space > 40:
            return cut[:last_space].strip()
        return cut.strip()
    return result.strip()


class ResearchEngine:
    """Multi-round, LLM-free deep research over the web.

    Search backend is pluggable: any client exposing search() + scrape() +
    is_configured (TavilyClient, DuckDuckGoClient, or FreeSearch — the
    default). Pass it via `search_client=`.
    """

    def __init__(
        self,
        session_logger=None,
        max_rounds: int = 4,
        max_sources_per_round: int = 5,
        scrape_timeout: float = 12.0,
        max_follow_ups: int = 3,
        search_client: Any = None,
        tavily: Optional["TavilyClient"] = None,
        progress_callback=None,
    ):
        # `tavily=` is kept for backward compat; `search_client=` is the
        # canonical param. Whatever is set wins.
        self.search_client = search_client or tavily
        self.tavily = self.search_client  # alias for any old callers
        self.session_logger = session_logger
        self.max_rounds = max_rounds
        self.max_sources_per_round = max_sources_per_round
        self.scrape_timeout = scrape_timeout
        self.max_follow_ups = max_follow_ups
        # Optional progress_callback(stage: str, detail: dict) called at every
        # long-running step so a live UI can show "round 2/4, 12 sources…".
        self.progress_callback = progress_callback
        # Empirical credibility tracker — measures how trustworthy a source
        # domain is based on how often its claims hold up under verification.
        # Scores evolve over time as more verifications accumulate.
        self.credibility = SourceCredibilityTracker()

    def _log(self, event: str, data: dict[str, Any] | None = None):
        if self.session_logger is None:
            return
        self.session_logger.log(event, data)

    def _progress(self, stage: str, detail: dict[str, Any] | None = None) -> None:
        """Emit a progress event to the live UI (if a callback is wired)."""
        if self.progress_callback is None:
            return
        with contextlib.suppress(Exception):
            # A UI callback failure must never break research.
            self.progress_callback(stage, detail or {})

    def _search_round(
        self,
        query: str,
        round_idx: int,
        topic: str = "",
        source_allowlist: list[str] | None = None,
        source_denylist: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Run one search query and return fetched, cleaned sources.

        Tavily is the sole search backend. If Tavily is unset or returns
        nothing, the round yields no sources (no SearXNG fallback). Tavily
        often returns raw_content inline, so we skip scraping; when it
        doesn't, we fetch the URL directly via tavily.scrape().
        """
        t0 = time.time()
        results: dict[str, Any] = {}

        if not (self.search_client and self.search_client.is_configured):
            self._log(
                "research_search_unconfigured", {"round": round_idx, "query": query}
            )
            return []

        try:
            results = self.search_client.search(
                query, max_results=self.max_sources_per_round
            )
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            self._log(
                "research_search_failed",
                {
                    "round": round_idx,
                    "query": query,
                    "backend": getattr(self.search_client, "name", "search_client"),
                    "error": str(e),
                },
            )
            results = {
                "results": [],
                "unresponsive_engines": [
                    [getattr(self.search_client, "name", "search_client"), str(e)]
                ],
            }
        hits = results.get("results", [])[: self.max_sources_per_round]
        self._log(
            "research_search",
            {
                "round": round_idx,
                "query": query,
                "backend": getattr(self.search_client, "name", "search_client"),
                "hits": len(hits),
                "duration_ms": (time.time() - t0) * 1000,
            },
        )
        # Compute the topic's signal terms ONCE for the relevance gate.
        # The gate drops sources that don't carry the topic's
        # high-specificity terms (proper nouns, API names) — this is the
        # fix for "the pile has some good stuff but the bot finds garbage":
        # without a gate, off-topic sources that happen to share generic
        # words ("python", "vector") flow into synthesis and crowd out the
        # real hits. See [[How-to-Fix-Research-Engine-Returning-Garbage]].
        topic_terms = _keyterms(topic) if topic else []
        signal = _signal_terms(topic_terms)
        # base_signal_count: the signal count BEFORE compound signals are
        # merged in. Passed to source_relevance() so the min_matches
        # threshold isn't inflated by compounds (which are alternative
        # match opportunities, not additional requirements).
        base_signal_count = len(signal)
        # Merge compound signals from the raw topic (e.g., "sea shells" from
        # "what are sea shells made of"). Without this, the signal list is
        # just ['shells'] — a single word that matches astrophysics papers
        # about "shell galaxies". The compound "sea shells" doesn't appear
        # in galaxy papers, so they get rejected by the gate.
        _compounds = _compound_signals(topic)
        if _compounds:
            _existing = {s.lower() for s in signal}
            for c in _compounds:
                if c not in _existing:
                    signal.append(c)
                    _existing.add(c)
        sources = []
        for hit in hits:
            url = hit.get("url")
            if not url:
                continue
            # Defense-in-depth: skip blocked sources (Wikipedia per the
            # operator's directive)
            if _is_blocked_source(url):
                self._log("research_source_blocked", {"round": round_idx, "url": url})
                continue
            # Source-authority allowlist/denylist (issue #133): when the
            # caller requires authoritative-only sources ("ONLY Google
            # official docs"), filter search results by domain BEFORE any
            # scraping or synthesis. A non-allowlisted source is discarded
            # entirely — not just down-ranked — so it can never leak into
            # the synthesized note.
            if not _is_allowlisted(url, source_allowlist):
                self._log(
                    "research_source_not_allowlisted",
                    {"round": round_idx, "url": url},
                )
                continue
            if _is_denylisted(url, source_denylist):
                self._log(
                    "research_source_denylisted",
                    {"round": round_idx, "url": url},
                )
                continue
            text = hit.get("raw_content", "") or ""
            snippet = hit.get("content", "")
            # Archive the raw source for on-demand re-reading (the index-don't-
            # -copy paradigm applied to web research). We save the page's raw
            # HTML to learningMaterial/web/ so the LLM can re-examine it later
            # without re-scraping (the page may have changed or gone offline).
            # The raw HTML stays OUT of the vault graph; only LLM notes about
            # it enter the graph, with provenance to the saved file.
            try:
                from web_source_store import fetch_and_save, save_source

                # If the search backend already gave us raw_content, save it
                # directly; otherwise fetch the raw HTML now.
                if text and len(text) >= 80:
                    save_source(url, text, title=hit.get("title", ""), topic=topic)
                else:
                    fetch_and_save(url, title=hit.get("title", ""), topic=topic)
            except Exception as e:
                self._log("research_archive_failed", {"url": url, "error": str(e)})
            # Tavily often returns raw_content inline; use it directly and
            # skip scraping. Only fetch when raw_content is missing/short.
            if not text or len(text) < 80:
                self._progress(
                    "scraping",
                    {"round": round_idx, "url": url, "title": hit.get("title", "")},
                )
                try:
                    text = self.search_client.scrape(
                        url, timeout=int(self.scrape_timeout)
                    )
                except Exception as e:
                    self._log("research_scrape_failed", {"url": url, "error": str(e)})
                    text = ""
            if not text or len(text) < 30:
                # Scrape failed or returned nothing useful — skip this
                # source. Do NOT fall back to the search-result snippet
                # (different content, different quality).
                continue
            # Relevance gate: drop sources that don't carry the topic's
            # signal terms. This is what separates the "good stuff" from
            # the pile. A source must score >= 1.0 to pass. We use the
            # snippet/title for the gate when text is short so a source
            # that scraped to almost nothing still gets judged on its
            # search-result snippet (which the engine ranked relevant).
            gate_text = text if len(text) >= 200 else (f"{snippet}\n{text}")
            rel_score, rel_reason = _source_relevance(
                hit.get("title", ""),
                gate_text,
                signal,
                topic_terms,
                url=url,
                base_signal_count=base_signal_count,
            )
            if rel_score < 1.0:
                self._log(
                    "research_source_rejected",
                    {
                        "round": round_idx,
                        "url": url,
                        "title": hit.get("title", "")[:80],
                        "score": round(rel_score, 2),
                        "reason": rel_reason,
                    },
                )
                continue
            # Low-credibility domain check: GitHub issues/PRs and other
            # code-hosting planning documents are NOT authoritative sources.
            # They pass the relevance gate (they contain the signal terms)
            # but they're project-specific planning docs, not documentation.
            # Tag them so the synthesis knows to down-rank them, and skip
            # them entirely if the URL is a GitHub issue/PR (the lowest
            # quality source type — a random project's todo item).
            is_low_cred = _is_low_credibility_domain(url)
            is_github_iss = _is_github_issue_or_pr(url)
            if is_github_iss:
                # GitHub issues/PRs/discussions are project planning artifacts,
                # not sources. Skip them — a random repo's OAuth issue is not
                # a source about OAuth. This is the root cause of the "links
                # to GitHub repos" problem: search engines return them because
                # the title matches, but they carry no authority.
                self._log(
                    "research_source_skipped_github_issue",
                    {
                        "round": round_idx,
                        "url": url,
                        "title": (hit.get("title", "") or "")[:80],
                    },
                )
                continue
            sources.append(
                {
                    "url": url,
                    "title": hit.get("title", ""),
                    "snippet": snippet,
                    "text": text,
                    "_relevance": rel_score,
                    "_credibility": self.credibility.get(url),
                    "_credibility_label": self.credibility.get_label(url),
                    "_low_credibility_domain": is_low_cred,
                }
            )
            self._log(
                "research_source_accepted",
                {
                    "round": round_idx,
                    "url": url,
                    "title": (hit.get("title", "") or "")[:80],
                    "relevance": round(rel_score, 2),
                    "credibility": round(self.credibility.get(url), 2),
                    "credibility_label": self.credibility.get_label(url),
                    "low_credibility_domain": is_low_cred,
                },
            )
        # --- URL liveness verification ----------------------------------
        # Check that all accepted source URLs actually resolve (return a
        # 2xx/3xx response). Dead links go straight into research notes
        # without this check — the search engine returned a URL, the
        # scraper got content (or the snippet was used), and the URL was
        # cited as a source even though it 404s. This batch-checks all
        # accepted URLs in parallel before returning.
        if _filter_dead_urls is not None and sources:
            candidate_urls = [s["url"] for s in sources]
            alive_urls, dead_urls = _filter_dead_urls(
                candidate_urls,
                timeout=5.0,
                max_workers=5,
                session_logger=self.session_logger,
            )
            if dead_urls:
                alive_set = set(alive_urls)
                before = len(sources)
                sources = [s for s in sources if s["url"] in alive_set]
                self._log(
                    "research_dead_urls_filtered",
                    {
                        "round": round_idx,
                        "checked": before,
                        "alive": len(alive_urls),
                        "dead": len(dead_urls),
                        "dead_urls": [
                            {"url": u, "reason": r} for u, r in dead_urls[:10]
                        ],
                    },
                )
        return sources

    def _search_with_source_policy(
        self,
        query: str,
        round_idx: int,
        topic: str,
        source_allowlist: list[str],
        source_denylist: list[str],
    ) -> list[dict[str, Any]]:
        """Search each allowed domain independently so the policy is OR-based."""
        if not source_allowlist:
            return self._search_round(
                query,
                round_idx,
                topic=topic,
                source_denylist=source_denylist,
            )
        sources: list[dict[str, Any]] = []
        for domain in source_allowlist:
            sources.extend(
                self._search_round(
                    f"{query} site:{domain}",
                    round_idx,
                    topic=topic,
                    source_allowlist=source_allowlist,
                    source_denylist=source_denylist,
                )
            )
        return sources

    def _expand_query(self, base_terms: list[str], discovered_terms: list[str]) -> str:
        """Build a refined query that adds newly-discovered salient terms."""
        # Prefer the base terms plus any discovered terms not already present.
        base_low = {t.lower() for t in base_terms}
        additions = [t for t in discovered_terms if t.lower() not in base_low]
        # Separate site: operators from regular terms — operators always go
        # last and are never dropped by the term cap.
        site_ops = [t for t in base_terms if t.lower().startswith("site:")]
        regular = [t for t in base_terms if not t.lower().startswith("site:")]
        terms = regular + additions[:3]
        # Cap regular terms, then always append site: operators.
        max_regular = max(1, 6 - len(site_ops))
        query_terms = terms[:max_regular] + site_ops
        return " ".join(query_terms)

    def _corroborated_facts(
        self, sentences: list[tuple[str, dict[str, Any]]], keyterms: list[str]
    ) -> list[dict[str, Any]]:
        """Score, dedup, and return the strongest corroborated sentences."""
        # Group near-duplicate sentences (same tokens, different sources).
        fact_buckets: dict[str, dict[str, Any]] = {}
        for sentence, src in sentences:
            toks = tuple(sorted(_tokenize_light(sentence)))
            if not toks:
                continue
            # Use a hashable signature of the most salient 6 tokens.
            sig = tuple(toks[:6])
            bucket = fact_buckets.setdefault(
                sig,
                {
                    "sentence": sentence,
                    "sources": [],
                    "score": 0.0,
                },
            )
            bucket["sources"].append(
                {
                    "url": src["url"],
                    "title": src["title"],
                }
            )
            # Credibility-weighted sentence score: a sentence from a
            # domain with a high credibility score (claims consistently
            # verified) contributes more than one from a domain with a
            # low score (claims frequently unsupported). The score
            # evolves over time as more verifications accumulate.
            _c_weight = self.credibility.get_weight(src.get("url", ""))
            bucket["score"] += (
                _score_sentence(sentence, keyterms, len(bucket["sources"])) * _c_weight
            )

        facts = list(fact_buckets.values())
        facts.sort(key=lambda f: f["score"], reverse=True)
        return facts

    def _identify_gaps(
        self,
        topic: str,
        facets: list[str],
        all_sources: list[dict[str, Any]],
        keyterms: list[str],
    ) -> list[str]:
        """Detect facets that remain under-covered and emit follow-up queries.

        Follow-up queries are built from the topic's SIGNAL terms (proper
        nouns, API names) + the facet keywords — NOT the whole topic string.
        The old code appended facet keywords to the entire topic, producing
        10-word queries like "faiss-IndexIDMap2-serialization-... serialization"
        that matched nothing useful. Signal terms are short and specific, so
        the follow-up query stays focused and the relevance gate still works.
        """
        corpus = " ".join(s["text"][:1500] for s in all_sources).lower()
        signal = _signal_terms(keyterms)
        # Build a compact query base from signal terms (max 4). Fall back to
        # the top keyterms if there are no signal terms (very generic topic).
        base = signal[:4] if signal else keyterms[:4]
        base_query = " ".join(base)
        gaps = []
        for facet in facets:
            fks = _facet_keywords(facet)
            coverage = sum(1 for fk in fks if fk in corpus)
            if coverage < max(1, len(fks) // 3):
                # Targeted follow-up: signal terms + facet keywords.
                gaps.append(f"{base_query} {' '.join(fks[:2])}")
        # Also detect missing keyterms in the corpus.
        for kt in keyterms:
            if kt.lower() not in corpus and len(gaps) < self.max_follow_ups:
                gaps.append(f"{base_query} {kt}")
        return gaps[: self.max_follow_ups]

    def _llm_synthesize(
        self,
        topic: str,
        sources: list[dict[str, Any]],
        llm_client: Any,
        vault_note_titles: list[str] | None = None,
    ) -> str | None:
        """One LLM call to synthesize a structured research note from source texts.

        Delegates to :func:`research_synthesizer.llm_synthesize`.
        """
        return _llm_synthesize_fn(
            topic, sources, llm_client, vault_note_titles, log_fn=self._log
        )

    def _extractive_synthesis(
        self, all_sources: list[dict[str, Any]], base_terms: list[str]
    ) -> tuple[str, set]:
        """Deterministic extractive synthesis (fallback when no LLM available).

        Delegates to :func:`research_synthesizer.extractive_synthesis`,
        passing ``self._corroborated_facts`` as the corroborate function.
        """
        return _extractive_synthesis_fn(
            all_sources, base_terms, corroborate_fn=self._corroborated_facts
        )

    def research(
        self,
        topic: str,
        llm_client: Any = None,
        vault_note_titles: list[str] | None = None,
        source_allowlist: list[str] | None = None,
        source_denylist: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run a full multi-round research dig. Returns a structured report.

        If llm_client is provided, uses one LLM call for the final synthesis
        instead of the extractive sentence-scoring. The deterministic
        search/fetch/clean pipeline is unchanged. Falls back to extractive
        synthesis if the LLM is unavailable or produces too-short output.

        ``source_allowlist`` / ``source_denylist`` (issue #133) restrict
        which domains may be used as sources. When an allowlist is set, only
        matching domains are fetched and synthesized; everything else is
        discarded before scraping. A denylist blocks specific low-quality
        domains (Medium, personal blogs) even when no allowlist is set.
        """
        t0 = time.time()
        topic = topic.strip()
        source_allowlist = _normalize_source_domains(
            source_allowlist, field_name="source_allowlist"
        )
        source_denylist = _normalize_source_domains(
            source_denylist, field_name="source_denylist"
        )
        blocked_allowlist = [
            domain
            for domain in source_allowlist
            if _is_denylisted(f"https://{domain}", source_denylist)
        ]
        if blocked_allowlist:
            raise ValueError(
                "source_allowlist domains are blocked by source_denylist: "
                + ", ".join(blocked_allowlist)
            )
        # Focus overly long topics: the agent sometimes passes a 200-char
        # roadmap bullet as the topic ("Bacterial communication: quorum
        # sensing, how bacteria talk to each other with chemical signals,
        # biofilm formation, ..."). Search engines can't handle that — they
        # return garbage. The full topic stays as the note title; the focused
        # version (first clause, ≤80 chars) is what gets searched.
        search_topic = _focus_topic(topic)
        base_terms = _keyterms(search_topic)
        facets = _detect_facets(search_topic)
        self._log(
            "research_begin",
            {
                "topic": topic,
                "search_topic": search_topic,
                "keyterms": base_terms,
                "facets": facets,
            },
        )

        all_sources: list[dict[str, Any]] = []
        seen_urls: set = set()
        discovered_terms: list[str] = []
        rounds_log: list[dict[str, Any]] = []

        # --- Main multi-round loop -------------------------------------------
        self._progress(
            "research_start",
            {"max_rounds": self.max_rounds, "keyterms": base_terms, "facets": facets},
        )
        for round_idx in range(self.max_rounds):
            # FIRST ROUND: use the natural-language topic string directly as
            # the search query. Search engines (Tavily, SearXNG) understand
            # natural language far better than space-joined keyterms.
            # "how bacteria communicate" gets much better results than
            # "communicate bacteria". Subsequent rounds use keyterm-based
            # queries with progressive refinement.
            if round_idx == 0:
                query = search_topic
            else:
                query = self._expand_query(base_terms, discovered_terms)
            round_t0 = time.time()
            self._progress(
                "search_round",
                {
                    "round": round_idx + 1,
                    "max_rounds": self.max_rounds,
                    "query": query,
                    "total_sources_so_far": len(all_sources),
                },
            )
            sources = self._search_with_source_policy(
                query,
                round_idx,
                search_topic,
                source_allowlist,
                source_denylist,
            )
            # Dedup against already-collected sources (normalized URLs
            # catch http vs https duplicates of the same page).
            # FIX: update seen_urls DURING the loop, not after, so that
            # within-round duplicates (http vs https of same URL) are caught.
            new_sources = []
            for s in sources:
                norm = _normalize_url(s["url"])
                if norm not in seen_urls:
                    seen_urls.add(norm)
                    new_sources.append(s)
            all_sources.extend(new_sources)
            rounds_log.append(
                {
                    "round": round_idx,
                    "query": query,
                    "new_sources": len(new_sources),
                    "total_sources": len(all_sources),
                    "duration_ms": (time.time() - round_t0) * 1000,
                }
            )
            self._progress(
                "search_round_done",
                {
                    "round": round_idx + 1,
                    "max_rounds": self.max_rounds,
                    "new_sources": len(new_sources),
                    "total_sources": len(all_sources),
                },
            )
            # Extract newly-salient terms from this round's corpus to refine
            # the next query — this is the "dig deeper" signal.
            if new_sources:
                round_corpus = " ".join(s["text"] for s in new_sources)
                # Rank tokens in the round corpus by frequency, exclude ones
                # already in our query.
                rtoks = _tokenize_light(round_corpus)
                rcounter = Counter(rtoks)
                existing = {t.lower() for t in base_terms + discovered_terms}
                for tok, _ in rcounter.most_common(12):
                    if tok not in existing and tok not in _STOPWORDS:
                        discovered_terms.append(tok)
                        if len(discovered_terms) >= 8:
                            break
            # Stop early only if we had sources and this round added nothing
            # new (a genuine coverage plateau). If we have ZERO sources so
            # far, keep going — the engines were likely temporarily banned
            # and a later round (or the gap-fill follow-ups) may recover.
            if not new_sources and all_sources:
                self._log(
                    "research_plateau", {"round": round_idx, "reason": "no_new_sources"}
                )
                break

        # --- Gap fill: targeted follow-ups for under-covered facets ----------
        # Gap detection uses source TEXTS, not synthesis output, so it
        # works regardless of whether we use extractive or LLM synthesis.
        # Running gap fill BEFORE synthesis ensures the LLM (when available)
        # sees ALL sources including gap-fill results in one call.
        gaps = self._identify_gaps(search_topic, facets, all_sources, base_terms)
        follow_up_sources: list[dict[str, Any]] = []
        if gaps:
            self._log("research_gap_fill", {"gaps": gaps})
            self._progress("gap_fill", {"queries": len(gaps), "gaps": gaps})
            for _gq_idx, gq in enumerate(gaps):
                gsrc = self._search_with_source_policy(
                    gq,
                    self.max_rounds,
                    search_topic,
                    source_allowlist,
                    source_denylist,
                )
                for s in gsrc:
                    if _normalize_url(s["url"]) not in seen_urls:
                        seen_urls.add(_normalize_url(s["url"]))
                        follow_up_sources.append(s)
                        all_sources.append(s)
            self._progress(
                "gap_fill_done",
                {
                    "follow_up_sources": len(follow_up_sources),
                    "total_sources": len(all_sources),
                },
            )

        if source_allowlist and not all_sources:
            raise RuntimeError(
                "No usable research sources were found in the requested domains: "
                + ", ".join(source_allowlist)
            )

        # --- Synthesis -----------------------------------------------------
        # PRIMARY PATH: when an LLM client is provided, use ONE LLM call to
        # synthesize all source texts into a coherent summary. The LLM
        # naturally filters irrelevant sources (Docker containers, off-topic
        # papers) because it understands the topic. This replaces the old
        # extractive sentence-scoring approach which was keyword-matching
        # garbage into bullet points.
        #
        # If the LLM is available, it is the ONLY synthesis path. If it
        # fails or produces too-short output, raise — no extractive
        # fallback. If no LLM client is available, use extractive only
        # (explicit choice, not a fallback).
        used: set = set()
        llm_synthesized = False
        if llm_client is not None:
            self._progress("llm_synthesizing", {"sources": len(all_sources)})
            llm_synth = self._llm_synthesize(
                topic, all_sources, llm_client, vault_note_titles=vault_note_titles
            )
            if llm_synth and len(llm_synth) >= 100:
                synthesis = llm_synth
                llm_synthesized = True
                used = set(
                    range(
                        len(
                            [
                                line
                                for line in llm_synth.split("\n")
                                if line.strip().startswith("-")
                            ]
                        )
                    )
                )
            else:
                # LLM returned too-short output — raise, don't fall back.
                raise RuntimeError(
                    f"LLM synthesis produced insufficient output "
                    f"({len(llm_synth) if llm_synth else 0} chars, need >=100)"
                )
        else:
            # No LLM client — extractive synthesis is the explicit path,
            # not a fallback. The caller chose to run without an LLM.
            self._progress("synthesizing", {"sources": len(all_sources)})
            synthesis, used = self._extractive_synthesis(all_sources, base_terms)

        report = {
            "topic": topic,
            "keyterms": base_terms,
            "facets_detected": facets,
            "llm_synthesized": llm_synthesized,
            "rounds": rounds_log,
            "gaps_filled": gaps,
            "sources": [
                {
                    "url": s["url"],
                    "title": s["title"],
                    "credibility": s.get(
                        "_credibility", self.credibility.get(s["url"])
                    ),
                    "credibility_label": s.get(
                        "_credibility_label", self.credibility.get_label(s["url"])
                    ),
                    "low_credibility_domain": s.get("_low_credibility_domain", False),
                }
                for s in all_sources
            ],
            "source_count": len(all_sources),
            "synthesis": synthesis,
            "synthesis_facts": len(used),
            "duration_ms": (time.time() - t0) * 1000,
        }
        self._log(
            "research_complete",
            {
                "topic": topic,
                "source_count": report["source_count"],
                "facts": report["synthesis_facts"],
                "duration_ms": report["duration_ms"],
            },
        )
        self._progress(
            "research_complete",
            {
                "source_count": report["source_count"],
                "facts": report["synthesis_facts"],
                "duration_ms": report["duration_ms"],
            },
        )
        return report

    def synthesize_note_markdown(
        self, report: dict[str, Any], summary: str | None = None
    ) -> str:
        """Render a research report as Obsidian markdown (no LLM).

        Delegates to :func:`research_synthesizer.synthesize_note_markdown`.
        """
        return _synthesize_note_markdown_fn(report, summary)

    # --- LLM-assisted note structuring ------------------------------------
    # The extractive synthesis above is a flat bullet dump of corroborated
    # sentences — no frontmatter, no section structure, no wikilinks, no
    # narrative. The notes it produced (Bacterial-communication-...md etc.)
    # were thin stubs, not the argument-driven research notes the vault
    # needs. This method takes the extractive synthesis and asks the LLM
    # (ONE call) to restructure it into a proper research note — same
    # pattern as lazy_condenser.condense_note / concept_card.refine_card:
    # the dig stays LLM-free (deterministic search + extractive synthesis),
    # and the LLM only fires at the final note-structuring step.
    # Falls back to synthesize_note_markdown() if the LLM is unavailable,
    # returns garbage, or produces a note below the safety floor (500 chars).

    # Safety floor: reject any LLM output shorter than this (catches an
    # LLM that collapses a note to nothing). Matches the
    # lazy_condenser safety floor pattern.

    @staticmethod
    def _get_vault_note_titles(vault_path: str) -> list[str]:
        """Get actual note titles (preserving case) from the vault directory.

        Delegates to :func:`research_synthesizer.get_vault_note_titles`.
        """
        return _get_vault_note_titles_fn(vault_path)

    @staticmethod
    def _repair_wikilinks(note_md: str, valid_titles: list[str]) -> str:
        """Fix wikilinks in LLM-generated note text. Zero LLM calls.

        Delegates to :func:`research_synthesizer.repair_wikilinks`.
        """
        return _repair_wikilinks_fn(note_md, valid_titles)

    # Safety floor for LLM-structured notes (reject output shorter than this).
    _STRUCTURED_MIN_CHARS = 500

    def synthesize_structured_note(
        self,
        report: dict[str, Any],
        summary: str | None = None,
        ollama_client: Any = None,
        vault_note_titles: list[str] | None = None,
    ) -> str:
        """Restructure the extractive synthesis into a proper research note.

        Delegates to :func:`research_synthesizer.synthesize_structured_note`.
        """
        return _synthesize_structured_note_fn(
            report, summary, ollama_client, vault_note_titles
        )
