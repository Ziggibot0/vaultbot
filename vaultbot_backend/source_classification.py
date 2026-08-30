"""Source URL classification and scoring for the research engine.

Classifies URLs as blocked/academic, normalizes URLs for dedup, and scores
source relevance against a topic's signal terms. No I/O, no state, no
dependencies on the research engine.
"""

import re

# --- Source blocklist (the operator's directive: never use Wikipedia) ---------------
# Only Wikipedia is hard-blocked. All other sources are scored by the
# empirical credibility tracker (see source_credibility.py), which
# measures how often a domain's claims hold up under verification.
_BLOCKED_DOMAINS = {
    "wikipedia.org",
    "en.m.wikipedia.org",
    "simple.wikipedia.org",
}

# --- Low-credibility source domains --------------------------------------
# Code-hosting platforms (GitHub, GitLab, Bitbucket, Gitee) are NOT
# authoritative research sources. A GitHub issue titled "Implement OAuth2
# authentication" is a project planning document for a specific app, not
# documentation about OAuth2. Search engines return these because the
# title/keywords match, but the content is project-specific and carries
# no authority. They pass the relevance gate (they contain the signal
# terms) but are down-ranked by the credibility system.
#
# The research engine uses is_low_credibility_domain() to:
#   1. Skip these sources when enough better sources are available.
#   2. Start their credibility score below neutral (0.3 instead of 0.5).
#   3. Never use them as the sole source for a claim (corroboration gate).
_LOW_CREDIBILITY_DOMAINS = {
    "github.com",
    "gitlab.com",
    "bitbucket.org",
    "gitee.com",
    "codeberg.org",
    "sourcehut.org",
}

# Within GitHub, issue/PR/wiki pages are even less authoritative than
# README/docs pages. This regex matches the path patterns that indicate
# a project planning document rather than documentation.
_GITHUB_LOW_CREDIBILITY_PATH = re.compile(
    r"github\.com/[^/]+/[^/]+/(issues|pull|wiki|discussions|projects)/",
    re.IGNORECASE,
)

# Academic domains — used only by citation_exporter.py for DOI extraction
# and by is_academic_source() (legacy callers). NOT used for credibility
# scoring (that's the credibility tracker's job).
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


# NOTE: DOI extraction from publisher URLs (nature.com -> 10.1038/...,
# arxiv.org -> 10.48550/arXiv...., etc.) lives in ``citation_exporter.py``
# (``extract_doi``), NOT here. This module classifies domains; DOI pattern
# matching is a citation-export concern and is kept separate so this module
# stays dependency-free. See [[Citation-Export-BibTeX]].


def is_blocked_source(url: str) -> bool:
    """Check if a URL should be hard-blocked from use as a research source.

    Only Wikipedia is hard-blocked (per [[No-Wikipedia-Directive]]).
    All other sources are scored by the empirical credibility tracker
    (see source_credibility.py), not blocked.
    """
    if not url:
        return False
    url_lower = url.lower()
    return any(domain in url_lower for domain in _BLOCKED_DOMAINS)


def is_academic_source(url: str) -> bool:
    """Check if a URL is from an academic/authoritative domain."""
    if not url:
        return False
    url_lower = url.lower()
    return any(domain in url_lower for domain in _ACADEMIC_DOMAINS)


def is_low_credibility_domain(url: str) -> bool:
    """Check if a URL is from a low-credibility domain (code-hosting platforms).

    GitHub/GitLab/Bitbucket issues, PRs, and wikis are project planning
    documents, not authoritative sources. Search engines return them because
    keyword matching hits, but a GitHub issue titled "Implement OAuth2" is
    about one team's specific implementation plan — it's not documentation
    about OAuth2. The research engine down-ranks these sources and never
    uses them as the sole backing for a claim.
    """
    if not url:
        return False
    url_lower = url.lower()
    return any(domain in url_lower for domain in _LOW_CREDIBILITY_DOMAINS)


def is_github_issue_or_pr(url: str) -> bool:
    """Check if a URL points to a GitHub issue, PR, wiki, or discussion.

    These are the LEAST authoritative pages on GitHub — they're project
    planning artifacts. A README or docs/ page at least documents the project;
    an issue is just someone's todo item with keywords in the title.
    """
    if not url:
        return False
    return bool(_GITHUB_LOW_CREDIBILITY_PATH.search(url.lower()))


def _hostname(url: str | None) -> str:
    """Extract the bare hostname (no scheme, no port, no path) from a URL.

    Used by the source-authority allowlist/denylist (issue #133) so a
    domain like ``developers.google.com`` matches regardless of scheme,
    ``www.`` prefix, or path. Returns "" on a malformed URL.
    """
    if not url:
        return ""
    try:
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").lower()
        # Strip a leading "www." so "www.google.com" == "google.com".
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:  # noqa: BLE001 — best-effort, returns "" on malformed URL
        return ""


def normalize_source_domains(
    domains: list[str] | None, *, field_name: str
) -> list[str]:
    """Validate and canonicalize a domain-only source policy."""
    normalized: list[str] = []
    for value in domains or []:
        domain = (value or "").strip().lower().strip(".")
        if domain.startswith("www."):
            domain = domain[4:]
        try:
            domain = domain.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError(f"Invalid domain in {field_name}: {value!r}") from exc
        labels = domain.split(".")
        if (
            not domain
            or ":" in domain
            or "/" in domain
            or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or not re.fullmatch(r"[a-z0-9-]+", label)
                for label in labels
            )
        ):
            raise ValueError(f"{field_name} accepts domains only, not URLs: {value!r}")
        if domain not in normalized:
            normalized.append(domain)
    return normalized


def is_allowlisted(url: str, allowlist: list[str] | None) -> bool:
    """True if ``url``'s hostname matches any allowlisted domain.

    An empty/None allowlist means "no restriction" (everything passes).
    Matching is suffix-based: ``developers.google.com`` matches
    ``google.com`` and ``developers.google.com``, but NOT ``notgoogle.com``.
    """
    if not allowlist:
        return True
    host = _hostname(url)
    if not host:
        return False
    for domain in allowlist:
        d = (domain or "").strip().lower().lstrip(".")
        if not d:
            continue
        if host == d or host.endswith("." + d):
            return True
    return False


def is_denylisted(url: str, denylist: list[str] | None) -> bool:
    """True if ``url``'s hostname matches any denylisted domain.

    Used to block known-low-quality sources (Medium, personal blogs) when
    authoritative sources are required. Same suffix-matching as
    ``is_allowlisted``.
    """
    if not denylist:
        return False
    host = _hostname(url)
    if not host:
        return False
    for domain in denylist:
        d = (domain or "").strip().lower().lstrip(".")
        if not d:
            continue
        if host == d or host.endswith("." + d):
            return True
    return False


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
    title: str,
    text: str,
    signal: list[str],
    all_keyterms: list[str],
    url: str = "",
    base_signal_count: int | None = None,
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
    - base_signal_count: the number of signal terms BEFORE compound signals
      were added. The min_matches threshold is computed from this, not from
      len(signal), so compound signals (which are alternative match
      opportunities, not additional requirements) don't inflate the gate.
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
        For multi-word phrases (e.g., 'sea shells'), also try the de-spaced
        variant ('seashells') — many sources use compound words. Mirror rule
        (issue #417): hyphenated terms like 'go-to-definition' also try the
        hyphens→spaces form ('go to definition') — docs routinely spell
        hyphenated concepts with spaces.
        """
        if s in text:
            return True
        # Multi-word phrase: also try de-spaced match ("sea shells" → "seashells")
        if " " in s:
            despaced = s.replace(" ", "")
            if despaced in text:
                return True
        # Hyphenated term: also try the hyphens→spaces form
        # ("go-to-definition" → "go to definition") — issue #417.
        if "-" in s:
            respaced = s.replace("-", " ")
            if respaced in text:
                return True
            # And the fully-joined form ("go-to-definition" → "gotodefinition")
            joined = s.replace("-", "")
            if joined != s and joined in text:
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
    # The threshold is based on the ORIGINAL signal count (before compound
    # signals were merged in). Compound signals are alternative match
    # opportunities (e.g. "sea shells" vs "shells"), not additional
    # requirements — inflating the threshold with them would reject
    # legitimate sources that match 6/7 original signals just because
    # they don't also match every compound variant.
    _thresh_count = base_signal_count if base_signal_count is not None else len(signal)
    # Require ALL signal terms to match when there are <= 3 (typical case).
    # With 4+ signal terms, require at least 60%. This prevents sources that
    # match just one generic signal word (e.g. "bacteria" in a paper about
    # bacterial mutation) from passing the gate.
    if _thresh_count <= 3:
        min_matches = _thresh_count  # ALL must match
    else:
        min_matches = max(2, int(_thresh_count * 0.6))
    # Minimum floor of 2 signal matches for ALL sources (academic or not)
    # when there are 2+ keyterms available. Without this, a single-signal-term
    # query like "sea shells" (signal=["shells"]) lets through any arxiv paper
    # that mentions "shells" — including astrophysics papers about "shell
    # galaxies" that have nothing to do with mollusk sea shells. Academic
    # sources previously bypassed this floor, which allowed off-topic arxiv
    # papers to pass with a single generic signal match.
    if len(all_keyterms) >= 2:
        min_matches = max(2, min_matches)
    if len(matched) < min_matches:
        return (
            0.5,
            f"insufficient_signal:{len(matched)}/{min_matches}"
            f"{' (non-academic)' if not is_academic else ''}",
        )
    return (
        score,
        f"signal:{','.join(matched[:4])}{' [academic]' if is_academic else ''}",
    )
