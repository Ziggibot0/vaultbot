"""Source URL classification and scoring for the research engine.

Classifies URLs as blocked/academic, normalizes URLs for dedup, and scores
source relevance against a topic's signal terms. No I/O, no state, no
dependencies on the research engine.
"""

import re

# --- Source blocklist (the operator's directive: never use Wikipedia) ---------------
# Defense-in-depth: the DDG client already filters, but we check again here
# so that even if a different search backend is swapped in, Wikipedia is
# never used as a source. See [[No-Wikipedia-Directive]].
_BLOCKED_DOMAINS = {
    "wikipedia.org",
    "en.m.wikipedia.org",
    "simple.wikipedia.org",
}

# Academic/authoritative domains get a relevance boost.
_ACADEMIC_DOMAINS = {
    "arxiv.org",
    "pubmed.ncbi.nlm.nih.gov",
    "pmc.ncbi.nlm.nih.gov",
    "nature.com",
    "science.org",
    "sciencedirect.com",
    "springer.com",
    "wiley.com",
    "oxfordacademic.com",
    "plos.org",
    "frontiersin.org",
    "mdpi.com",
    "biomedcentral.com",
    "royalsocietypublishing.org",
    "cell.com",
    "elifesciences.org",
    "ncbi.nlm.nih.gov",
    "openstax.org",
    "khanacademy.org",
    "britannica.com",
    "sciencedaily.com",
    "phys.org",
    "eurekalert.org",
    "annualreviews.org",
    "jstor.org",
    "doi.org",
    "bmglabtech.com",
}


def is_blocked_source(url: str) -> bool:
    if not url:
        return False
    url_lower = url.lower()
    if any(domain in url_lower for domain in _BLOCKED_DOMAINS):
        return True
    return False


def is_academic_source(url: str) -> bool:
    """Check if a URL is from an academic/authoritative domain."""
    if not url:
        return False
    url_lower = url.lower()
    return any(domain in url_lower for domain in _ACADEMIC_DOMAINS)


def normalize_url(url: str) -> str:
    """Normalize URL for dedup: strip protocol, www., trailing slash, fragments.

    Fixes the duplicate-source bug where the same arXiv paper appeared twice
    (once as https://arxiv.org/... and once as http://arxiv.org/...). The
    dedup set used exact string comparison, so protocol variants slipped
    through. See [[How-to-Fix-Research-Engine-Returning-Garbage]].
    """
    if not url:
        return ""
    u = url.lower().strip()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = u.split("#")[0]
    u = u.rstrip("/")
    return u


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


def source_relevance(
    title: str, text: str, signal: list[str], all_keyterms: list[str], url: str = ""
) -> tuple[float, str]:
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

    def _sig_match(s: str, text: str) -> bool:
        """Check if signal term s appears in text, allowing morphological variants.
        For terms >= 7 chars, match on a stem of max(7, len(s)-3) chars so
        'communicate' catches 'communication', 'communicating', etc. but NOT
        'community' (unrelated). For shorter terms, use exact substring match.
        """
        if s in text:
            return True
        if len(s) >= 7:
            stem_len = max(7, len(s) - 3)
            stem = s[:stem_len]
            # Word-boundary match on the stem.
            if re.search(r"\b" + re.escape(stem), text):
                return True
        return False

    for s in signal:
        in_title = _sig_match(s, title_low)
        in_text = _sig_match(s, text_low)
        if in_title and in_text:
            score += 2.0
            matched.append(s)
        elif in_title or in_text:
            score += 1.0
            matched.append(s)
    if not matched:
        return 0.0, "no_signal_match"
    # Require at least 50% of signal terms to match for ALL sources.
    # Previously academic sources only needed 1 match, letting through
    # off-topic arXiv papers that happened to contain one signal word
    # (e.g. "Bacteria are not Lamarckian" matched "bacteria" but had
    # nothing about communication). With 2 signal terms, 50% = 1, but
    # with 3+ terms it requires 2+, filtering out marginally-related sources.
    is_academic = is_academic_source(url)
    # Require ALL signal terms to match when there are <= 3 (typical case).
    # With 4+ signal terms, require at least 60%. This prevents sources that
    # match just one generic signal word (e.g. "bacteria" in a paper about
    # bacterial mutation) from passing the gate.
    if len(signal) <= 3:
        min_matches = len(signal)  # ALL must match
    else:
        min_matches = max(2, int(len(signal) * 0.6))
    if not is_academic:
        min_matches = max(2, min_matches)  # non-academic needs at least 2
    if len(matched) < min_matches:
        return (
            0.5,
            f"insufficient_signal:{len(matched)}/{min_matches}{' (non-academic)' if not is_academic else ''}",
        )
    return (
        score,
        f"signal:{','.join(matched[:4])}{' [academic]' if is_academic else ''}",
    )
