"""
LLM-light deep research engine.

Design goal: "get to the bottom of" a topic while keeping the burden on the
vault/web, NOT on the LLM. The LLM is used only for the *final* synthesis in
the caller (handle_chat), never inside the research loop itself.

Pipeline (no LLM calls):
  1. Extract key terms from the topic (noun phrases, no LLM).
  2. Multi-round Tavily queries with progressive refinement — each round
     adds terms that the previous round's sources revealed but that the
     query didn't yet contain. Digging continues until coverage plateaus.
  3. Fetch the top sources per round, clean to article text.
  4. Extractive synthesis: score sentences by keyword density + source
     agreement (corroboration across multiple sources), then assemble the
     highest-scoring sentences into a structured, faceted summary.
  5. Gap detection: after synthesis, identify facets/questions that remain
     under-covered, and run targeted follow-up queries to fill them.
  6. Return a structured ResearchReport the caller can turn into a note.

Everything here is deterministic/extractive — the LLM only ever sees the
finished, sourced summary, so the model's weights stay out of the dig.
"""

import re
import time
import math
import string
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

try:
    from tavily_client import TavilyClient
except Exception:
    TavilyClient = None  # type: ignore

try:
    from free_search import FreeSearch
except Exception:
    FreeSearch = None  # type: ignore


# --- Source blocklist (Sean's directive: never use Wikipedia) ---------------
# Defense-in-depth: the DDG client already filters, but we check again here
# so that even if a different search backend is swapped in, Wikipedia is
# never used as a source. See [[No-Wikipedia-Directive]].
_BLOCKED_DOMAINS = {
    "wikipedia.org",
    "en.m.wikipedia.org",
    "simple.wikipedia.org",
}

def _is_blocked_source(url: str) -> bool:
    if not url:
        return False
    url_lower = url.lower()
    return any(domain in url_lower for domain in _BLOCKED_DOMAINS)

# --- Stopwords for keyterm extraction (no external dep) -------------------
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "when", "of",
    "in", "on", "at", "to", "for", "with", "without", "into", "from", "by",
    "as", "is", "are", "was", "were", "be", "been", "being", "this", "that",
    "these", "those", "it", "its", "they", "them", "their", "we", "you",
    "your", "our", "i", "me", "my", "he", "she", "his", "her", "him", "who",
    "whom", "which", "what", "why", "how", "where", "there", "here", "about",
    "than", "so", "do", "does", "did", "doing", "have", "has", "had", "not",
    "no", "yes", "can", "could", "should", "would", "may", "might", "will",
    "shall", "must", "between", "within", "among", "through", "during",
    "before", "after", "above", "below", "over", "under", "up", "down",
    "out", "off", "again", "further", "once", "such", "also", "only", "own",
    "same", "other", "some", "any", "all", "both", "each", "few", "more",
    "most", "many", "much", "little", "less", "least", "vs", "versus",
    "via", "per", "etc", "ie", "eg", "upon", "because", "while", "since",
    "however", "therefore", "thus", "hence", "whether", "either", "neither",
}

# Facets are the kinds of questions a "deep" answer should cover. The engine
# detects which facets a topic implies and then checks coverage of each.
_FACET_PATTERNS = [
    ("definition", re.compile(r"\b(what (is|are)|define|definition|meaning of)\b", re.I)),
    ("history", re.compile(r"\b(history|origin|when (did|was)|who (discovered|invented|founded))\b", re.I)),
    ("mechanism", re.compile(r"\b(how (does|do|can)|mechanism|process|works?|algorithm)\b", re.I)),
    ("examples", re.compile(r"\b(examples?|instances?|cases?|kinds?|types?|categories)\b", re.I)),
    ("comparison", re.compile(r"\b(vs|versus|compare|difference|better|alternatives?)\b", re.I)),
    ("pros_cons", re.compile(r"\b(pros?|cons?|advantages?|disadvantages?|benefits?|drawbacks?|risks?)\b", re.I)),
    ("recent", re.compile(r"\b(recent|latest|current|2024|2025|2026|new)\b", re.I)),
]


def _keyterms(text: str, max_terms: int = 6) -> List[str]:
    """Extract salient noun-ish keyterms without an LLM.

    Ranks tokens by frequency * length, filters stopwords, and keeps proper
    nouns (capitalized mid-sentence) and capitalized multi-word phrases.
    """
    text = text.replace("?", " ").replace("!", " ").strip().lower()
    # Pull out quoted phrases first (the user often telegraphs the topic).
    quoted = re.findall(r"[\"']([^\"']+)[\"']", text)
    # Pull out capitalized noun phrases from the original-cased text.
    phrases = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b", text)
    # Tokenize the lowercased text.
    tokens = re.findall(r"[a-z][a-z0-9\-]+", text)
    # Score single tokens: freq * length, skip stopwords.
    scored: Dict[str, float] = {}
    tok_counter = Counter(tokens)
    for tok, count in tok_counter.items():
        if tok in _STOPWORDS or len(tok) < 3:
            continue
        scored[tok] = count * (1 + math.log(len(tok)))
    # Merge: quoted > phrases > single tokens.
    result: List[str] = []
    seen = set()
    for q in quoted:
        ql = q.lower()
        if ql and ql not in seen:
            result.append(q.strip())
            seen.add(ql)
    for p in phrases:
        pl = p.lower()
        words = pl.split()
        # Skip if all words are stopwords.
        if any(w not in _STOPWORDS for w in words) and pl not in seen:
            result.append(p.strip())
            seen.add(pl)
    # Top single tokens.
    ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)
    for tok, _ in ranked:
        if tok in seen:
            continue
        result.append(tok)
        seen.add(tok)
        if len(result) >= max_terms:
            break
    return result[:max_terms]


def _split_sentences(text: str) -> List[str]:
    """Cheap sentence splitter that's good enough for scraped web text."""
    text = re.sub(r"\s+", " ", text).strip()
    # Split on . ! ? followed by whitespace+capital, or newlines.
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text)
    sentences = []
    for p in parts:
        p = p.strip()
        if 30 <= len(p) <= 400:  # ignore headings/fragments/giant blobs
            sentences.append(p)
    return sentences


def _tokenize_light(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9][a-z0-9\-]+", text.lower())
            if w not in _STOPWORDS and len(w) > 2]


def _score_sentence(sentence: str, keyterms: List[str],
                    source_count: int) -> float:
    """Extractive score: keyword density * corroboration boost."""
    toks = _tokenize_light(sentence)
    if not toks:
        return 0.0
    tok_set = set(toks)
    hits = 0
    for kt in keyterms:
        kt_low = kt.lower()
        if " " in kt_low:
            if kt_low in sentence.lower():
                hits += 2
        elif kt_low in tok_set:
            hits += 1
    density = hits / math.sqrt(len(toks))
    # Corroboration: a fact supported by N independent sources is worth more.
    corroboration = 1.0 + 0.15 * max(0, source_count - 1)
    return density * corroboration


def _detect_facets(topic: str) -> List[str]:
    facets = []
    for name, pattern in _FACET_PATTERNS:
        if pattern.search(topic):
            facets.append(name)
    if not facets:
        # Default: at least try to cover definition + examples.
        facets = ["definition", "examples"]
    return facets


def _facet_keywords(facet: str) -> List[str]:
    return {
        "definition": ["definition", "means", "refers to", "is a", "are a"],
        "history": ["history", "origin", "introduced", "founded", "invented",
                    "first described", "etymology"],
        "mechanism": ["works", "mechanism", "process", "algorithm", "how",
                      "steps", "procedure", "method"],
        "examples": ["example", "such as", "including", "instance", "case",
                      "types of", "kinds of"],
        "comparison": ["versus", "compared to", "difference", "alternative",
                        "whereas", "unlike"],
        "pros_cons": ["advantage", "disadvantage", "benefit", "drawback",
                       "risk", "limitation", "downside"],
        "recent": ["recent", "latest", "current", "2024", "2025", "2026",
                   "new study", "recently"],
    }.get(facet, [])


class ResearchEngine:
    """Multi-round, LLM-free deep research over the web.

    Search backend is pluggable: any client exposing search() + scrape() +
    is_configured (TavilyClient, DuckDuckGoClient, or FreeSearch — the
    default). Pass it via `search_client=`.
    """

    def __init__(self, session_logger=None,
                 max_rounds: int = 4, max_sources_per_round: int = 5,
                 scrape_timeout: float = 12.0, max_follow_ups: int = 3,
                 search_client: Any = None,
                 tavily: Optional["TavilyClient"] = None,
                 progress_callback=None):
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

    def _log(self, event: str, data: Optional[Dict[str, Any]] = None):
        if self.session_logger is None:
            return
        self.session_logger.log(event, data)

    def _progress(self, stage: str, detail: Optional[Dict[str, Any]] = None) -> None:
        """Emit a progress event to the live UI (if a callback is wired)."""
        if self.progress_callback is None:
            return
        try:
            self.progress_callback(stage, detail or {})
        except Exception:
            # A UI callback failure must never break research.
            pass

    def _search_round(self, query: str, round_idx: int,
                      topic: str = "") -> List[Dict[str, Any]]:
        """Run one search query and return fetched, cleaned sources.

        Tavily is the sole search backend. If Tavily is unset or returns
        nothing, the round yields no sources (no SearXNG fallback). Tavily
        often returns raw_content inline, so we skip scraping; when it
        doesn't, we fetch the URL directly via tavily.scrape().
        """
        t0 = time.time()
        results: Dict[str, Any] = {}

        if not (self.search_client and self.search_client.is_configured):
            self._log("research_search_unconfigured",
                      {"round": round_idx, "query": query})
            return []

        try:
            results = self.search_client.search(
                query, max_results=self.max_sources_per_round)
        except Exception as e:
            self._log("research_search_failed",
                      {"round": round_idx, "query": query,
                       "backend": getattr(self.search_client, "name",
                                          "search_client"),
                       "error": str(e)})
            results = {"results": [],
                      "unresponsive_engines": [
                          [getattr(self.search_client, "name", "search_client"),
                           str(e)]]}
        hits = results.get("results", [])[: self.max_sources_per_round]
        self._log("research_search", {
            "round": round_idx, "query": query,
            "backend": getattr(self.search_client, "name", "search_client"),
            "hits": len(hits), "duration_ms": (time.time() - t0) * 1000,
        })
        sources = []
        for hit in hits:
            url = hit.get("url")
            if not url:
                continue
            # Defense-in-depth: skip blocked sources (Wikipedia per Sean's directive)
            if _is_blocked_source(url):
                self._log("research_source_blocked",
                          {"round": round_idx, "url": url})
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
                    save_source(url, text, title=hit.get("title", ""),
                                topic=topic)
                else:
                    fetch_and_save(url, title=hit.get("title", ""),
                                   topic=topic)
            except Exception:
                pass  # archiving is best-effort; never blocks the dig
            # Tavily often returns raw_content inline; use it directly and
            # skip scraping. Only fetch when raw_content is missing/short.
            if not text or len(text) < 80:
                self._progress("scraping", {
                    "round": round_idx, "url": url, "title": hit.get("title", "")})
                try:
                    text = self.search_client.scrape(
                        url, timeout=int(self.scrape_timeout))
                except Exception as e:
                    self._log("research_scrape_failed",
                              {"url": url, "error": str(e)})
                    text = ""
            if (not text or len(text) < 80) and snippet and len(snippet) > 30:
                text = snippet
                self._log("research_scrape_fallback_snippet", {"url": url})
            if not text or len(text) < 30:
                continue
            sources.append({
                "url": url,
                "title": hit.get("title", ""),
                "snippet": snippet,
                "text": text,
            })
        return sources

    def _expand_query(self, base_terms: List[str],
                      discovered_terms: List[str]) -> str:
        """Build a refined query that adds newly-discovered salient terms."""
        # Prefer the base terms plus any discovered terms not already present.
        base_low = {t.lower() for t in base_terms}
        additions = [t for t in discovered_terms if t.lower() not in base_low]
        # Keep queries short — concise queries retrieve better results.
        terms = base_terms + additions[:3]
        return " ".join(terms[:6])

    def _corroborated_facts(self, sentences: List[Tuple[str, Dict[str, Any]]],
                            keyterms: List[str]) -> List[Dict[str, Any]]:
        """Score, dedup, and return the strongest corroborated sentences."""
        # Group near-duplicate sentences (same tokens, different sources).
        fact_buckets: Dict[str, Dict[str, Any]] = {}
        for sentence, src in sentences:
            toks = tuple(sorted(_tokenize_light(sentence)))
            if not toks:
                continue
            # Use a hashable signature of the most salient 6 tokens.
            sig = tuple(toks[:6])
            bucket = fact_buckets.setdefault(sig, {
                "sentence": sentence,
                "sources": [],
                "score": 0.0,
            })
            bucket["sources"].append({
                "url": src["url"], "title": src["title"],
            })
            bucket["score"] += _score_sentence(
                sentence, keyterms, len(bucket["sources"]))

        facts = list(fact_buckets.values())
        facts.sort(key=lambda f: f["score"], reverse=True)
        return facts

    def _identify_gaps(self, topic: str, facets: List[str],
                       all_sources: List[Dict[str, Any]],
                       keyterms: List[str]) -> List[str]:
        """Detect facets that remain under-covered and emit follow-up queries."""
        corpus = " ".join(s["text"][:1500] for s in all_sources).lower()
        gaps = []
        for facet in facets:
            fks = _facet_keywords(facet)
            coverage = sum(1 for fk in fks if fk in corpus)
            if coverage < max(1, len(fks) // 3):
                # Build a follow-up query that targets the facet.
                fk_query = f"{topic.strip()} {' '.join(fks[:2])}"
                gaps.append(fk_query)
        # Also detect missing keyterms in the corpus.
        for kt in keyterms:
            if kt.lower() not in corpus and len(gaps) < self.max_follow_ups:
                gaps.append(f"{topic.strip()} {kt}")
        return gaps[: self.max_follow_ups]

    def research(self, topic: str) -> Dict[str, Any]:
        """Run a full multi-round research dig. Returns a structured report.

        No LLM is used here. The caller may pass the report's `synthesis`
        to an LLM for a final prose answer if desired.
        """
        t0 = time.time()
        topic = topic.strip()
        base_terms = _keyterms(topic)
        facets = _detect_facets(topic)
        self._log("research_begin", {
            "topic": topic, "keyterms": base_terms, "facets": facets,
        })

        all_sources: List[Dict[str, Any]] = []
        seen_urls: set = set()
        discovered_terms: List[str] = []
        rounds_log: List[Dict[str, Any]] = []

        # --- Main multi-round loop -------------------------------------------
        self._progress("research_start", {
            "max_rounds": self.max_rounds, "keyterms": base_terms,
            "facets": facets})
        for round_idx in range(self.max_rounds):
            query = self._expand_query(base_terms, discovered_terms)
            round_t0 = time.time()
            self._progress("search_round", {
                "round": round_idx + 1, "max_rounds": self.max_rounds,
                "query": query, "total_sources_so_far": len(all_sources)})
            sources = self._search_round(query, round_idx, topic=topic)
            # Dedup against already-collected sources.
            new_sources = [s for s in sources if s["url"] not in seen_urls]
            for s in new_sources:
                seen_urls.add(s["url"])
            all_sources.extend(new_sources)
            rounds_log.append({
                "round": round_idx,
                "query": query,
                "new_sources": len(new_sources),
                "total_sources": len(all_sources),
                "duration_ms": (time.time() - round_t0) * 1000,
            })
            self._progress("search_round_done", {
                "round": round_idx + 1, "max_rounds": self.max_rounds,
                "new_sources": len(new_sources),
                "total_sources": len(all_sources)})
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
                self._log("research_plateau",
                          {"round": round_idx, "reason": "no_new_sources"})
                break

        # --- Extractive synthesis -------------------------------------------
        self._progress("synthesizing", {"sources": len(all_sources)})
        sentences: List[Tuple[str, Dict[str, Any]]] = []
        for src in all_sources:
            for sent in _split_sentences(src["text"]):
                sentences.append((sent, src))
        facts = self._corroborated_facts(sentences, base_terms)
        # Take the top facts but cap total synthesis length.
        synthesis_lines: List[str] = []
        total_len = 0
        max_synth = 3500
        used = set()
        for fact in facts:
            s = fact["sentence"]
            sig = tuple(sorted(_tokenize_light(s)))
            if sig in used:
                continue
            used.add(sig)
            srcs = ", ".join(f["title"] or f["url"] for f in fact["sources"][:2])
            line = f"- {s}  [sources: {srcs}]"
            if total_len + len(line) > max_synth:
                break
            synthesis_lines.append(line)
            total_len += len(line)
        synthesis = "\n".join(synthesis_lines)

        # --- Gap fill: targeted follow-ups for under-covered facets ----------
        gaps = self._identify_gaps(topic, facets, all_sources, base_terms)
        follow_up_sources: List[Dict[str, Any]] = []
        if gaps:
            self._log("research_gap_fill", {"gaps": gaps})
            self._progress("gap_fill", {"queries": len(gaps), "gaps": gaps})
            for gq_idx, gq in enumerate(gaps):
                gsrc = self._search_round(gq, round_idx=self.max_rounds,
                                            topic=topic)
                for s in gsrc:
                    if s["url"] not in seen_urls:
                        seen_urls.add(s["url"])
                        follow_up_sources.append(s)
                        all_sources.append(s)
            self._progress("gap_fill_done", {
                "follow_up_sources": len(follow_up_sources),
                "total_sources": len(all_sources)})
            # Re-synthesize with the new sources included.
            more_sentences = []
            for src in follow_up_sources:
                for sent in _split_sentences(src["text"]):
                    more_sentences.append((sent, src))
            more_facts = self._corroborated_facts(more_sentences, base_terms)
            for fact in more_facts:
                s = fact["sentence"]
                sig = tuple(sorted(_tokenize_light(s)))
                if sig in used:
                    continue
                used.add(sig)
                srcs = ", ".join(f["title"] or f["url"]
                                 for f in fact["sources"][:2])
                line = f"- {s}  [sources: {srcs}]"
                if total_len + len(line) > max_synth:
                    break
                synthesis_lines.append(line)
                total_len += len(line)
            synthesis = "\n".join(synthesis_lines)

        report = {
            "topic": topic,
            "keyterms": base_terms,
            "facets_detected": facets,
            "rounds": rounds_log,
            "gaps_filled": gaps,
            "sources": [{"url": s["url"], "title": s["title"]}
                        for s in all_sources],
            "source_count": len(all_sources),
            "synthesis": synthesis,
            "synthesis_facts": len(used),
            "duration_ms": (time.time() - t0) * 1000,
        }
        self._log("research_complete", {
            "topic": topic,
            "source_count": report["source_count"],
            "facts": report["synthesis_facts"],
            "duration_ms": report["duration_ms"],
        })
        self._progress("research_complete", {
            "source_count": report["source_count"],
            "facts": report["synthesis_facts"],
            "duration_ms": report["duration_ms"]})
        return report

    def synthesize_note_markdown(self, report: Dict[str, Any],
                                 summary: Optional[str] = None) -> str:
        """Render a research report as Obsidian markdown (no LLM)."""
        lines = [f"# {report['topic']}", ""]
        if summary:
            lines += ["## Summary", summary, ""]
        lines += [
            "## Key Findings",
            report["synthesis"] or "(no corroborated findings extracted)",
            "",
            "## Sources",
        ]
        for s in report["sources"]:
            # Link to the local archived copy if one exists (provenance to the
            # saved snapshot, not just the live URL which may rot). Falls back
            # to the live URL if the source wasn't archived.
            try:
                from web_source_store import find_source
                archived = find_source(s["url"])
            except Exception:
                archived = None
            if archived:
                local = f"[[learningMaterial/web/{archived['file']}|archived]]"
                lines.append(f"- [{s['title'] or s['url']}]({s['url']}) ({local})")
            else:
                lines.append(f"- [{s['title'] or s['url']}]({s['url']})")
        if report.get("gaps_filled"):
            lines += ["", "## Follow-up Queries (gap fill)",
                      "\n".join(f"- {g}" for g in report["gaps_filled"])]
        lines += ["",
                  f"<!-- research: {report['source_count']} sources, "
                  f"{report['synthesis_facts']} facts, "
                  f"{len(report.get('rounds', []))} rounds -->"]
        return "\n".join(lines)