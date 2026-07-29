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

import math
import re
import time
from collections import Counter
from typing import Any, Optional

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

# Non-academic domains that pollute results with keyword-matched software.
# See [[How-to-Fix-Research-Engine-Returning-Garbage]].
_BLOCKED_NONACADEMIC = {
    "hub.docker.com", "docker.com", "github.com",
    "stackoverflow.com", "reddit.com", "twitter.com", "x.com",
    "youtube.com", "npmjs.com", "pypi.org", "bintray.com",
    "packagist.org", "rubygems.org", "maven.org", "chat.marginalia.nu",
}

# Academic/authoritative domains get a relevance boost.
_ACADEMIC_DOMAINS = {
    "arxiv.org", "pubmed.ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov",
    "nature.com", "science.org", "sciencedirect.com", "springer.com",
    "wiley.com", "oxfordacademic.com", "plos.org", "frontiersin.org",
    "mdpi.com", "biomedcentral.com", "royalsocietypublishing.org",
    "cell.com", "elifesciences.org", "ncbi.nlm.nih.gov",
    "openstax.org", "khanacademy.org", "britannica.com",
    "sciencedaily.com", "phys.org", "eurekalert.org",
    "annualreviews.org", "jstor.org", "doi.org", "bmglabtech.com",
}

def _is_blocked_source(url: str) -> bool:
    if not url:
        return False
    url_lower = url.lower()
    if any(domain in url_lower for domain in _BLOCKED_DOMAINS):
        return True
    if any(domain in url_lower for domain in _BLOCKED_NONACADEMIC):
        return True
    return False

def _is_academic_source(url: str) -> bool:
    """Check if a URL is from an academic/authoritative domain."""
    if not url:
        return False
    url_lower = url.lower()
    return any(domain in url_lower for domain in _ACADEMIC_DOMAINS)


def _normalize_url(url: str) -> str:
    """Normalize URL for dedup: strip protocol, www., trailing slash, fragments.

    Fixes the duplicate-source bug where the same arXiv paper appeared twice
    (once as https://arxiv.org/... and once as http://arxiv.org/...). The
    dedup set used exact string comparison, so protocol variants slipped
    through. See [[How-to-Fix-Research-Engine-Returning-Garbage]].
    """
    if not url:
        return ""
    u = url.lower().strip()
    u = re.sub(r'^https?://', '', u)
    u = re.sub(r'^www\.', '', u)
    u = u.split('#')[0]
    u = u.rstrip('/')
    return u

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


def _keyterms(text: str, max_terms: int = 6) -> list[str]:
    """Extract salient noun-ish keyterms without an LLM.

    Ranks tokens by frequency * length, filters stopwords, and keeps proper
    nouns (capitalized mid-sentence) and capitalized multi-word phrases.
    Also preserves site:domain.com search operators so the search backend
    can target specific domains (e.g., site:github.com for forum discussions).
    """
    # Extract site: operators BEFORE any preprocessing — the : character is
    # lost by the tokenizer, so we must capture them from the original text.
    site_operators = re.findall(r"\bsite:\S+", text, re.I)
    # Slug detection: the autonomous researcher passes vault note names (e.g.
    # ``FAISS-IndexIDMap2-remove_ids-vector-removal-API-documentation``) as
    # the topic. These use hyphens as word separators, not intra-token
    # punctuation. A topic with no spaces but with hyphens is treated as a
    # slug and split on hyphens BEFORE tokenizing, so each concept becomes its
    # own keyterm candidate instead of the whole slug collapsing into one
    # giant token like "how-to-safe_write".
    original = text
    if " " not in text.strip() and "-" in text and len(text) > 8:
        token_source = text.replace("-", " ")
    else:
        token_source = text
    # Pull capitalized noun phrases from the ORIGINAL-cased text BEFORE
    # lowercasing. The previous code lowercased first, so the [A-Z] regex
    # matched nothing and proper nouns like "FAISS IndexIDMap2" were never
    # extracted — generic tokens ("python", "vectors") then dominated and
    # arXiv returned any paper mentioning "Python".
    phrases = re.findall(r"\b([A-Z][a-zA-Z0-9_]+(?:\s+[A-Z][a-zA-Z0-9_]+){0,3})\b", original)
    # Pull quoted phrases (the user often telegraphs the topic).
    quoted = re.findall(r"[\"']([^\"']+)[\"']", original)
    work = token_source.replace("?", " ").replace("!", " ").strip().lower()
    # Tokenize the lowercased text — KEEP underscores so remove_ids stays
    # one token (was split into "remove"+"ids" by the old [a-z][a-z0-9\-]+
    # regex, losing the actual API name and matching generic "remove").
    tokens = re.findall(r"[a-z][a-z0-9_]+", work)
    # Score single tokens: freq * length, skip stopwords.
    scored: dict[str, float] = {}
    tok_counter = Counter(tokens)
    for tok, count in tok_counter.items():
        if tok in _STOPWORDS or len(tok) < 3:
            continue
        scored[tok] = count * (1 + math.log(len(tok)))
    # Merge: site: operators > quoted > phrases > single tokens.
    result: list[str] = []
    seen = set()
    # site: operators get highest priority — they're explicit user intent.
    for so in site_operators:
        so_low = so.lower()
        if so_low not in seen:
            result.append(so)
            seen.add(so_low)
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
    # Top single tokens — leave room for site: operators.
    ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)
    for tok, _ in ranked:
        if tok in seen:
            continue
        result.append(tok)
        seen.add(tok)
        if len(result) >= max_terms:
            break
    return result[:max_terms]


def _split_sentences(text: str) -> list[str]:
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


def _tokenize_light(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9][a-z0-9\-]+", text.lower())
            if w not in _STOPWORDS and len(w) > 2]


def _score_sentence(sentence: str, keyterms: list[str],
                    source_count: int) -> float:
    """Extractive score: keyword density * corroboration boost.

    Signal terms (proper nouns, API names, multi-word phrases — the terms
    that actually disambiguate the topic) are weighted 5× higher than
    generic single-word keyterms. A sentence that mentions "FAISS" and
    "remove_ids" is almost certainly on-topic; one that only mentions
    "python" and "index" is not, even if those are in the keyterm list.
    """
    toks = _tokenize_light(sentence)
    if not toks:
        return 0.0
    tok_set = set(toks)
    sig = _signal_terms(keyterms)
    sig_set = {s.lower() for s in sig}
    hits = 0.0
    for kt in keyterms:
        kt_low = kt.lower()
        if " " in kt_low:
            if kt_low in sentence.lower():
                # Multi-word phrases are always signal → heavy weight.
                hits += 5.0
        elif kt_low in sig_set:
            if kt_low in tok_set:
                hits += 5.0
            elif kt_low in sentence.lower():
                hits += 2.0
        elif kt_low in tok_set:
            # Generic term — small weight, just density filler.
            hits += 1.0
    density = hits / math.sqrt(len(toks))
    # Corroboration: a fact supported by N independent sources is worth more.
    corroboration = 1.0 + 0.15 * max(0, source_count - 1)
    return density * corroboration


# --- Source relevance gate ------------------------------------------------
#
# The core reason the research engine returns "garbage": every hit from every
# engine flows into synthesis with no source-level relevance check. A source
# that matches one generic word ("python", "vector", "index") but is about a
# completely different topic gets the same treatment as a real hit. The
# synthesis then picks sentences by keyword *density*, and wrong-topic sources
# that happen to be longer / use the generic words more often win.
#
# The gate separates high-specificity "signal" terms from generic English, and
# requires a source to carry enough of the signal to count. Signal terms are:
#   - Proper nouns / capitalized phrases (FAISS, IndexIDMap2, DuckDuckGo)
#   - Underscored / hyphenated API names (remove_ids, write_index)
#   - Quoted phrases the user telegraphed
#   - site: operator domains (the user explicitly targeted a site)
# Generic single words ("python", "vectors", "how", "delete") are NOT signal
# — millions of pages contain them and they carry no topical information.

def _signal_terms(keyterms: list[str]) -> list[str]:
    """Return the high-specificity subset of keyterms.

    These are the terms that actually disambiguate the topic from the
    millions of pages that share its generic vocabulary. Used as the
    relevance gate's yardstick: a source must contain at least one
    signal term (or, for very generic topics, a quorum of the keyterms).
    """
    sig: list[str] = []
    for kt in keyterms:
        low = kt.lower()
        if low.startswith("site:"):
            # site:github.com -> the domain is the signal.
            sig.append(low[5:])
            continue
        # Multi-word phrases are always signal (rare by definition).
        if " " in low:
            sig.append(low)
            continue
        # Underscored or hyphenated compound tokens are API/library names.
        if "_" in low or "-" in low:
            sig.append(low)
            continue
        # Capitalized single tokens (proper nouns) are signal. We can't see
        # case from keyterms (already lowered by _keyterms), but tokens ≥5
        # chars that aren't in a broad generic-tech stoplist are treated as
        # signal. Short common words ("python", "index", "vector") are NOT
        # signal alone — they need a signal partner.
        if len(low) >= 5 and low not in _GENERIC_TERMS:
            sig.append(low)
        # Short all-caps acronyms (AHL, DNA, RNA, ATP) are always signal
        # even though they're < 5 chars. They're highly specific terms
        # that disambiguate biology/chemistry topics.
        elif len(low) >= 2 and low == low.upper() and low.isalpha():
            sig.append(low)
    # Dedup preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for s in sig:
        if s not in seen:
            out.append(s)
            seen.add(s)
    return out


# Broad generic tech vocabulary that appears in millions of unrelated pages.
# These are NOT signal on their own — a source mentioning only "python" and
# "index" tells you nothing about whether it's about FAISS. A source must pair
# them with a real signal term (FAISS, IndexIDMap2, remove_ids, …) to pass.
_GENERIC_TERMS = {
    "python", "index", "vector", "vectors", "array", "arrays", "data",
    "code", "function", "method", "class", "object", "value", "values",
    "list", "dict", "string", "file", "files", "system", "server", "client",
    "model", "models", "training", "learning", "search", "query", "database",
    "api", "config", "config", "build", "run", "test", "error", "bug",
    "issue", "problem", "solution", "example", "tutorial", "guide",
    "library", "package", "module", "import", "install", "version",
    "performance", "memory", "time", "size", "type", "name", "key", "keys",
    "add", "delete", "remove", "update", "create", "read", "write", "load",
    "save", "load", "open", "close", "start", "stop", "set", "get", "new",
    "old", "best", "good", "bad", "how", "what", "why", "when", "where",
    "without", "with", "from", "into", "using", "use", "used", "uses",
    "research", "study", "paper", "analysis", "study", "results", "method",
    # Biology terms that are too generic alone — need specific partners
    "communicate", "communication", "communicating", "signaling",
    "molecules", "sensing", "formation", "behavior", "coordination",
    "through", "group", "groups",
    "approach", "based", "proposed", "novel", "new", "recent", "current",
}


def _source_relevance(title: str, text: str, signal: list[str],
                       all_keyterms: list[str],
                       url: str = "") -> tuple[float, str]:
    """Score how on-topic a source is. Returns (score, reason).

    score >= 1.0 means the source passes the relevance gate.
    - Signal-term match: each distinct signal term found in title+text adds
      1.0. A source carrying the topic's proper nouns / API names is almost
      certainly on-topic regardless of generic-word density.
    - Generic-term fallback: if the topic has NO signal terms (very generic
      query like "how to evaluate source credibility"), fall back to a
      quorum of all keyterms — a source must share >= 40% of them.
    - Title match boost: signal terms in the title count double (titles are
      the author's own claim of what the page is about).
    """
    if not signal:
        # No signal terms → use generic quorum. This is the case for soft
        # topics ("how to evaluate credibility of sources") where there are
        # no proper nouns. Require >= 40% of all keyterms present.
        if not all_keyterms:
            return 1.0, "no_keyterms"
        title_low = (title or "").lower()
        text_low = (text or "").lower()[:8000]
        present = 0
        for kt in all_keyterms:
            kt_low = kt.lower()
            if " " in kt_low:
                if kt_low in text_low or kt_low in title_low:
                    present += 1
            elif kt_low in text_low or kt_low in title_low:
                present += 1
        ratio = present / len(all_keyterms)
        return ratio * 2.5, f"generic_quorum:{present}/{len(all_keyterms)}"
    title_low = (title or "").lower()
    text_low = (text or "").lower()[:8000]
    score = 0.0
    matched: list[str] = []
    for s in signal:
        in_title = s in title_low
        in_text = s in text_low
        if in_title and in_text:
            score += 2.0
            matched.append(s)
        elif in_title or in_text:
            score += 1.0
            matched.append(s)
    if not matched:
        return 0.0, "no_signal_match"
    # Require at least 2 signal matches for non-academic sources.
    # Prevents Docker "quorum" images from passing with just 1 match.
    is_academic = _is_academic_source(url)
    if not is_academic and len(matched) < 2:
        return 0.5, f"insufficient_signal:{len(matched)}/2 (non-academic)"
    return score, f"signal:{','.join(matched[:4])}{' [academic]' if is_academic else ''}"


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
    all_words = re.findall(r'[a-zA-Z][a-zA-Z0-9_-]+', topic.lower())
    # Find all-caps acronyms from the ORIGINAL text (case-sensitive).
    acronyms = re.findall(r'\b([A-Z]{2,5}[0-9]?)\b', topic)
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

    def _log(self, event: str, data: dict[str, Any] | None = None):
        if self.session_logger is None:
            return
        self.session_logger.log(event, data)

    def _progress(self, stage: str, detail: dict[str, Any] | None = None) -> None:
        """Emit a progress event to the live UI (if a callback is wired)."""
        if self.progress_callback is None:
            return
        try:
            self.progress_callback(stage, detail or {})
        except Exception:
            # A UI callback failure must never break research.
            pass

    def _search_round(self, query: str, round_idx: int,
                      topic: str = "") -> list[dict[str, Any]]:
        """Run one search query and return fetched, cleaned sources.

        Tavily is the sole search backend. If Tavily is unset or returns
        nothing, the round yields no sources (no SearXNG fallback). Tavily
        often returns raw_content inline, so we skip scraping; when it
        doesn't, we fetch the URL directly via tavily.scrape().
        """
        t0 = time.time()
        results: dict[str, Any] = {}

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
        # Compute the topic's signal terms ONCE for the relevance gate.
        # The gate drops sources that don't carry the topic's
        # high-specificity terms (proper nouns, API names) — this is the
        # fix for "the pile has some good stuff but the bot finds garbage":
        # without a gate, off-topic sources that happen to share generic
        # words ("python", "vector") flow into synthesis and crowd out the
        # real hits. See [[How-to-Fix-Research-Engine-Returning-Garbage]].
        topic_terms = _keyterms(topic) if topic else []
        signal = _signal_terms(topic_terms)
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
            # Relevance gate: drop sources that don't carry the topic's
            # signal terms. This is what separates the "good stuff" from
            # the pile. A source must score >= 1.0 to pass. We use the
            # snippet/title for the gate when text is short so a source
            # that scraped to almost nothing still gets judged on its
            # search-result snippet (which the engine ranked relevant).
            gate_text = text if len(text) >= 200 else (
                f"{snippet}\n{text}")
            rel_score, rel_reason = _source_relevance(
                hit.get("title", ""), gate_text, signal, topic_terms,
                url=url)
            if rel_score < 1.0:
                self._log("research_source_rejected",
                          {"round": round_idx, "url": url,
                           "title": hit.get("title", "")[:80],
                           "score": round(rel_score, 2),
                           "reason": rel_reason})
                continue
            sources.append({
                "url": url,
                "title": hit.get("title", ""),
                "snippet": snippet,
                "text": text,
                "_relevance": rel_score,
            })
        return sources

    def _expand_query(self, base_terms: list[str],
                      discovered_terms: list[str]) -> str:
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

    def _corroborated_facts(self, sentences: list[tuple[str, dict[str, Any]]],
                            keyterms: list[str]) -> list[dict[str, Any]]:
        """Score, dedup, and return the strongest corroborated sentences."""
        # Group near-duplicate sentences (same tokens, different sources).
        fact_buckets: dict[str, dict[str, Any]] = {}
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

    def _identify_gaps(self, topic: str, facets: list[str],
                       all_sources: list[dict[str, Any]],
                       keyterms: list[str]) -> list[str]:
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

    def research(self, topic: str) -> dict[str, Any]:
        """Run a full multi-round research dig. Returns a structured report.

        No LLM is used here. The caller may pass the report's `synthesis`
        to an LLM for a final prose answer if desired.
        """
        t0 = time.time()
        topic = topic.strip()
        # Focus overly long topics: the agent sometimes passes a 200-char
        # roadmap bullet as the topic ("Bacterial communication: quorum
        # sensing, how bacteria talk to each other with chemical signals,
        # biofilm formation, ..."). Search engines can't handle that — they
        # return garbage. The full topic stays as the note title; the focused
        # version (first clause, ≤80 chars) is what gets searched.
        search_topic = _focus_topic(topic)
        base_terms = _keyterms(search_topic)
        facets = _detect_facets(search_topic)
        self._log("research_begin", {
            "topic": topic, "search_topic": search_topic,
            "keyterms": base_terms, "facets": facets,
        })

        all_sources: list[dict[str, Any]] = []
        seen_urls: set = set()
        discovered_terms: list[str] = []
        rounds_log: list[dict[str, Any]] = []

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
            sources = self._search_round(query, round_idx, topic=search_topic)
            # Dedup against already-collected sources (normalized URLs
            # catch http vs https duplicates of the same page).
            new_sources = [s for s in sources
                           if _normalize_url(s["url"]) not in seen_urls]
            for s in new_sources:
                seen_urls.add(_normalize_url(s["url"]))
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
        sentences: list[tuple[str, dict[str, Any]]] = []
        for src in all_sources:
            for sent in _split_sentences(src["text"]):
                sentences.append((sent, src))
        facts = self._corroborated_facts(sentences, base_terms)
        # Take the top facts but cap total synthesis length.
        synthesis_lines: list[str] = []
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
        gaps = self._identify_gaps(search_topic, facets, all_sources, base_terms)
        follow_up_sources: list[dict[str, Any]] = []
        if gaps:
            self._log("research_gap_fill", {"gaps": gaps})
            self._progress("gap_fill", {"queries": len(gaps), "gaps": gaps})
            for gq_idx, gq in enumerate(gaps):
                gsrc = self._search_round(gq, round_idx=self.max_rounds,
                                            topic=search_topic)
                for s in gsrc:
                    if _normalize_url(s["url"]) not in seen_urls:
                        seen_urls.add(_normalize_url(s["url"]))
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

    def synthesize_note_markdown(self, report: dict[str, Any],
                                 summary: str | None = None) -> str:
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
    _STRUCTURED_MIN_CHARS = 500

    def synthesize_structured_note(
        self,
        report: dict[str, Any],
        summary: str | None = None,
        ollama_client: Any = None,
        vault_note_titles: list[str] | None = None,
    ) -> str:
        """Restructure the extractive synthesis into a proper research note.

        ONE LLM call. Produces a note with YAML frontmatter, H2 sections,
        argument-driven narrative, preserved [sources: ...] citations, and
        [[wikilinks]] to existing vault notes.

        Falls back to ``synthesize_note_markdown`` (the extractive format)
        if the LLM is unavailable or the output is below the safety floor.
        Never raises — the caller always gets a valid markdown note.
        """
        # Safety: if no LLM client, fall back to the extractive format.
        if ollama_client is None:
            return self.synthesize_note_markdown(report, summary)
        # Safety: if no synthesis content, fall back (nothing to structure).
        synth = str(report.get("synthesis", "") or "")
        if len(synth) < 80:
            return self.synthesize_note_markdown(report, summary)

        topic = report.get("topic", "Research Note")
        source_count = report.get("source_count", 0)
        facts = report.get("synthesis_facts", 0)

        # Build the source list for the prompt (title + url).
        sources_block = "\n".join(
            f"- {s.get('title') or s.get('url', '')} — {s.get('url', '')}"
            for s in report.get("sources", [])[:12]
        )

        # Build a vault-link hint: a compact list of existing note titles so
        # the LLM can insert [[wikilinks]] to relevant concepts. Capped to
        # avoid flooding the prompt (the LLM only needs a sample to spot
        # relevant ones).
        titles_hint = ""
        if vault_note_titles:
            sample = vault_note_titles[:120]
            titles_hint = (
                "\n\nEXISTING VAULT NOTES (use [[Note-Name]] to link to any "
                "that are topically relevant — do NOT force links):\n"
                + "\n".join(f"- {t}" for t in sample)
            )

        # Build the prompt. The system message sets the format contract;
        # the user message provides the raw synthesis + sources.
        system = (
            "You are a research note structuring assistant. You take raw "
            "extractive synthesis (corroborated sentences with [sources: ...] "
            "tags) and restructure it into a proper Obsidian research note. "
            "You MUST:\n"
            "1. Start with YAML frontmatter (--- ... ---) with keys: type, "
            "status, created, summary, tags, sources, depends_on.\n"
            "2. Use ## H2 section headings to organize the content into a "
            "narrative (NOT flat bullet points). Each section should build "
            "an argument, not just list facts.\n"
            "3. PRESERVE every [sources: ...] citation tag inline — these "
            "are the provenance links.\n"
            "4. Insert [[wikilinks]] to existing vault notes ONLY where "
            "topically relevant (use the EXISTING VAULT NOTES list). Never "
            "invent note titles that aren't in that list.\n"
            "5. Keep ALL the factual content from the synthesis — don't "
            "drop facts, just restructure them into readable prose.\n"
            "6. End with a ## Sources section listing each source as a "
            "markdown link.\n"
            "Do NOT add a top-level # heading (the caller adds it). Start "
            "directly with the YAML frontmatter."
        )
        user = (
            f"Topic: {topic}\n\n"
            f"Summary line: {summary or ''}\n\n"
            f"Raw extractive synthesis ({source_count} sources, {facts} "
            f"facts):\n\n{synth}\n\n"
            f"Sources:\n{sources_block}"
            f"{titles_hint}\n\n"
            "Restructure this into a proper research note with YAML "
            "frontmatter, H2 sections, preserved citations, and "
            "[[wikilinks]] to relevant existing vault notes. Output ONLY "
            "the note content (starting with ---)."
        )

        try:
            result = ollama_client.generate(
                prompt=user,
                system=system,
                temperature=0.3,
                max_tokens=2048,
                stream=False,
            )
            if isinstance(result, dict):
                note_md = result.get("response", "")
            else:
                # A generator fallback — drain it (shouldn't happen with
                # stream=False, but be safe).
                note_md = "".join(
                    c.get("response", "") for c in result)
        except Exception:
            # LLM unavailable / errored → fall back to extractive format.
            return self.synthesize_note_markdown(report, summary)

        note_md = (note_md or "").strip()
        if len(note_md) < self._STRUCTURED_MIN_CHARS:
            # Too short — the LLM collapsed it. Fall back.
            return self.synthesize_note_markdown(report, summary)

        # The LLM may have included a top-level # heading despite the
        # instruction not to. Strip it so the caller's own # heading is
        # the only one at the top.
        note_md = re.sub(r"\A#\s+.+\n+", "", note_md)

        # Ensure the research provenance marker is present (the extractive
        # format has it; the LLM may drop it). Append if missing.
        marker = (
            f"<!-- research: {source_count} sources, {facts} facts, "
            f"{len(report.get('rounds', []))} rounds -->"
        )
        if marker not in note_md:
            note_md = note_md.rstrip() + "\n\n" + marker + "\n"

        return note_md
