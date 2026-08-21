"""Citation export (BibTeX + RIS) with DOI extraction for research notes.

Parses the ``## Sources`` section of a VaultBot research note, extracts
DOIs from publisher-specific URL patterns, and renders BibTeX / RIS
citation strings. Designed for academic use (e.g. a PhD reviewer who
publishes in Nature Scientific Reports and Molecular Ecology) where
markdown links alone are insufficient — they need BibTeX export and
DOI extraction.

All functions are pure (no I/O, no network) except
``export_citations_to_file``, which writes a ``.bib`` file alongside the
note. This keeps the module unit-testable: DOI extraction, BibTeX, and
RIS generation are pure string transforms.

DOI extraction patterns (URL → DOI):

  - ``doi.org/10.xxxx/...``                → path after ``doi.org/``
  - ``nature.com/articles/s41597-024-...`` → ``10.1038/s41597-024-...``
  - ``sciencedirect.com/.../pii/...``      → None (needs API lookup)
  - ``arxiv.org/abs/2404.16130``           → ``10.48550/arXiv.2404.16130``
  - ``arxiv.org/pdf/2404.16130``           → ``10.48550/arXiv.2404.16130``
  - ``pubmed.ncbi.nlm.nih.gov/<id>/``      → None (needs PubMed API)
  - ``springer.com/article/10.1007/...``   → the DOI after ``/article/``
  - ``plos.org/articles?id=10.1371/...``   → the ``id`` query param
  - ``frontiersin.org/articles/10.3389/..``→ the path after ``/articles/``
  - ``mdpi.com/<vol>/<issue>/<page>``      → None (needs lookup)
  - ``wiley.com/doi/10.1002/ecy.4567``     → the path after ``/doi/``
  - ``cell.com/.../fulltext/S0092-...``    → None (PII, needs API)

See [[Citation-Export-BibTeX]] for the design note.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from source_classification import is_academic_source

# ---------------------------------------------------------------------------
# Academic domains that yield a DOI via a deterministic URL pattern.
# ``doi.org`` is already in ``source_classification._ACADEMIC_DOMAINS`` —
# no change needed there. DOI extraction from URLs lives HERE (this module),
# not in ``source_classification.py``.
# ---------------------------------------------------------------------------


# Regex patterns for DOI extraction. Each is (pattern, group_name_or_None).
# Order matters: more specific patterns first. All patterns are case-
# insensitive and anchored to the URL path/host (not the full DOI syntax,
# which could appear in query strings we don't want to catch).
_DOI_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # doi.org/<doi>  — the canonical DOI resolver
    (re.compile(r"doi\.org/(10\.\d{4,}/[^\s?#]+)", re.I), "doi_org"),
    # nature.com/articles/<suffix> → 10.1038/<suffix>
    (re.compile(r"nature\.com/articles/(s\d{4,}[^\s?#/]*)", re.I), "nature"),
    # arxiv.org/abs/<id> or arxiv.org/pdf/<id>
    (re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", re.I), "arxiv"),
    # springer.com/article/<doi>
    (re.compile(r"springer\.com/article/(10\.\d{4,}/[^\s?#]+)", re.I), "springer"),
    # plos.org/articles?id=<doi> (query param)
    (re.compile(r"plos\.org/articles\?id=(10\.\d{4,}/[^\s&#]+)", re.I), "plos"),
    # frontiersin.org/articles/<doi>
    (
        re.compile(r"frontiersin\.org/articles/(10\.\d{4,}/[^\s?#]+)", re.I),
        "frontiers",
    ),
    # wiley.com/doi/<doi>
    (re.compile(r"wiley\.com/doi/(10\.\d{4,}/[^\s?#]+)", re.I), "wiley"),
]

# Domains where a DOI CANNOT be extracted from the URL alone (need an API
# lookup). Listed explicitly so callers can add a ``note`` field to the
# BibTeX entry explaining why no DOI is present.
_NO_DOI_DOMAINS: set[str] = {
    "sciencedirect.com",
    "pubmed.ncbi.nlm.nih.gov",
    "mdpi.com",
    "cell.com",
}


def extract_doi(url: str) -> str | None:
    """Extract a DOI from a publisher URL using pattern matching.

    Returns the DOI string (e.g. ``"10.1038/s41597-024-03668-4"``) or
    ``None`` if no pattern matches. For domains that require an API
    lookup (ScienceDirect PII, PubMed ID, MDPI, Cell PII), returns
    ``None`` — the caller can check ``_NO_DOI_DOMAINS`` to distinguish
    "no pattern" from "needs API lookup".
    """
    if not url:
        return None
    for pattern, kind in _DOI_PATTERNS:
        m = pattern.search(url)
        if not m:
            continue
        captured = m.group(1)
        if kind == "nature":
            return f"10.1038/{captured}"
        if kind == "arxiv":
            return f"10.48550/arXiv.{captured}"
        # doi.org, springer, plos, frontiers, wiley → captured IS the DOI
        return captured
    return None


def _needs_api_lookup(url: str) -> bool:
    """Check if the URL is from a domain where DOI extraction needs an API."""
    if not url:
        return False
    host = (urlparse(url).hostname or "").lower()
    return any(d in host for d in _NO_DOI_DOMAINS)


# ---------------------------------------------------------------------------
# Parsing the ## Sources section of a research note
# ---------------------------------------------------------------------------

# Matches:  - [Title](URL)   optionally followed by  — DOI: 10.xxxx/...
_SOURCE_RE = re.compile(
    r"-\s+\[([^\]]*)\]\(([^)]+)\)"  # - [Title](URL)
    r"(?:\s*[—-]\s*DOI:\s*(10\.\d{4,}/[^\s)]+))?"  # optional — DOI: ...
)


def parse_sources_from_note(note_text: str) -> list[dict[str, str]]:
    """Parse the ``## Sources`` section of a research note into source dicts.

    Extracts ``[Title](URL)`` pairs (and optional inline ``— DOI: ...``
    annotations) from the section starting at the ``## Sources`` heading.
    Returns a list of ``{"title": ..., "url": ..., "doi": ...}`` dicts.
    The ``doi`` key is the inline DOI if present, otherwise extracted from
    the URL via :func:`extract_doi`, otherwise ``None``.
    """
    if not note_text:
        return []
    # Slice from the ## Sources heading to the next ## heading or EOF.
    lines = note_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("## sources"):
            start = i + 1
            break
    if start is None:
        return []
    section_lines: list[str] = []
    for line in lines[start:]:
        if line.strip().startswith("## ") and section_lines:
            break
        section_lines.append(line)
    section = "\n".join(section_lines)

    sources: list[dict[str, str]] = []
    for m in _SOURCE_RE.finditer(section):
        title = m.group(1).strip()
        url = m.group(2).strip()
        inline_doi = m.group(3)
        doi = inline_doi or extract_doi(url)
        sources.append({"title": title, "url": url, "doi": doi})
    return sources


# ---------------------------------------------------------------------------
# BibTeX generation
# ---------------------------------------------------------------------------


def _bibtex_key(source: dict[str, str], index: int, year: str) -> str:
    """Generate a BibTeX citation key.

    Format: ``firstauthor_lastname + year`` or ``title_firstword + year``
    or fallback ``sourceN``. We don't have author info from the Sources
    section (only title + url), so we use the title-first-word path unless
    the source dict has an ``author`` key.
    """
    author = source.get("author", "")
    if author:
        # First author's last name: take the first name in the list and
        # grab the last token (handles "Smith, John" and "John Smith").
        first_author = author.split(" and ")[0].strip()
        if "," in first_author:
            lastname = first_author.split(",")[0].strip()
        else:
            parts = first_author.split()
            lastname = parts[-1] if parts else first_author
        lastname = re.sub(r"[^A-Za-z]", "", lastname).lower()
        if lastname:
            return f"{lastname}{year}"
    title = source.get("title", "")
    if title:
        # First word, alpha-only, lowercase, ≥3 chars.
        for word in re.findall(r"[A-Za-z]{3,}", title):
            return f"{word.lower()}{year}"
    return f"source{index}"


def _escape_bibtex(value: str) -> str:
    """Escape a string for BibTeX field values."""
    if not value:
        return ""
    return value.replace("\\", "\\textbackslash{}").replace("&", "\\&")


def to_bibtex(sources: list[dict[str, Any]]) -> str:
    """Render a list of source dicts as BibTeX entries.

    Each source dict may contain: ``title``, ``url``, ``doi``, ``author``,
    ``year``. If the source has an academic domain and a DOI, an
    ``@article`` entry is produced; otherwise ``@misc``.

    Fields included: ``title``, ``url``, ``doi`` (if present), ``urldate``
    (today's date), ``note`` (if DOI not extracted but domain is academic
    / needs API lookup), ``author`` + ``year`` (if provided).
    """
    if not sources:
        return ""
    today = date.today().isoformat()
    entries: list[str] = []
    for i, src in enumerate(sources, start=1):
        title = src.get("title") or src.get("url") or f"Source {i}"
        url = src.get("url", "")
        doi = src.get("doi")
        year = str(src.get("year", "")).strip() or "n.d."
        author = src.get("author", "")
        key = _bibtex_key(src, i, year)

        is_article = bool(doi and is_academic_source(url))
        entry_type = "article" if is_article else "misc"

        fields: list[str] = []
        fields.append(f"  title        = {{{_escape_bibtex(title)}}}")
        if author:
            fields.append(f"  author       = {{{_escape_bibtex(author)}}}")
        fields.append(f"  url          = {{{url}}}")
        if doi:
            fields.append(f"  doi          = {{{doi}}}")
        fields.append(f"  urldate      = {{{today}}}")
        # If the source is academic but we couldn't extract a DOI, add a
        # note so the reader knows it needs a manual/API lookup.
        if not doi and _needs_api_lookup(url):
            fields.append("  note         = {DOI not extracted; requires API lookup}")
        entries.append(f"@{entry_type}{{{key},\n" + ",\n".join(fields) + "\n}")
    return "\n".join(entries) + "\n"


# ---------------------------------------------------------------------------
# RIS generation
# ---------------------------------------------------------------------------


def to_ris(sources: list[dict[str, Any]]) -> str:
    """Render a list of source dicts as RIS entries.

    Standard RIS fields: ``TY`` (type), ``TI`` (title), ``UR`` (url),
    ``DO`` (doi), ``ER`` (end record). One record per source.
    """
    if not sources:
        return ""
    records: list[str] = []
    for src in sources:
        title = src.get("title") or src.get("url") or ""
        url = src.get("url", "")
        doi = src.get("doi")
        is_article = bool(doi and is_academic_source(url))
        lines = [
            f"TY  - {'JOUR' if is_article else 'ELEC'}",
            f"TI  - {title}",
            f"UR  - {url}",
        ]
        if doi:
            lines.append(f"DO  - {doi}")
        author = src.get("author")
        if author:
            lines.append(f"AU  - {author}")
        year = src.get("year")
        if year:
            lines.append(f"PY  - {year}")
        lines.append("ER  - ")
        records.append("\n".join(lines))
    return "\n".join(records) + "\n"


# ---------------------------------------------------------------------------
# Main export API
# ---------------------------------------------------------------------------


def export_citations(
    note_path: str,
    sources: list[dict[str, Any]] | None = None,
    format: str = "bibtex",
) -> str:
    """Export citations from a research note as a formatted string.

    If ``sources`` is ``None``, parses them from the note's ``## Sources``
    section. ``format`` may be ``"bibtex"`` or ``"ris"``. Returns the
    formatted citation string (does not write to disk).
    """
    if sources is None:
        note_text = Path(note_path).read_text(encoding="utf-8")
        sources = parse_sources_from_note(note_text)
    if not sources:
        return ""
    fmt = (format or "bibtex").lower().strip()
    if fmt == "ris":
        return to_ris(sources)
    return to_bibtex(sources)


def export_citations_to_file(
    note_path: str,
    sources: list[dict[str, Any]] | None = None,
) -> str:
    """Write a ``.bib`` file alongside the note and return its path.

    ``note_path`` is the path to the ``.md`` research note. The BibTeX
    file is written to the same directory with the same stem and a
    ``.bib`` extension (e.g. ``Knowledge/Research/topic.bib`` next to
    ``topic.md``). Returns the absolute path to the written file.
    """
    note_p = Path(note_path)
    bib_path = note_p.with_suffix(".bib")
    content = export_citations(note_path, sources=sources, format="bibtex")
    bib_path.write_text(content, encoding="utf-8")
    return str(bib_path)
