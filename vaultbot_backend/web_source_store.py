"""
Web source store — preserve scraped sources for on-demand re-reading.

Mirrors the textbook index-only paradigm, applied to web research. The
research engine scrapes sources to build its synthesis, but historically
threw the raw pages away — so if the LLM later needed to re-examine a
source (verify a claim, pull a quote, answer a follow-up), it had to
re-scrape, and the page might have changed or gone offline.

This module saves every scraped source as raw HTML in
``learningMaterial/web/`` and keeps a lightweight index so the LLM can find
and re-read any source on demand. The raw HTML stays OUT of the vault graph
(it's source material, like a textbook PDF); only the LLM's *notes about* a
source enter the graph, with provenance pointing back to the saved file.

Layout:
  learningMaterial/web/<slug>.html   — the raw scraped page
  learningMaterial/web/_index.json   — {url, file, title, date, topic} entries

The index is a single JSON file (not a vault note) so it doesn't clutter the
graph. A `web_read_source` tool (parallel to textbook_read_page) lets the
LLM re-read a saved source later: it renders the HTML to text, or to an
image for a vision model when the page has figures/equations.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

try:
    VAULT_DIR = (
        Path(__file__).resolve().parent.parent
    )  # vaultbot/ (framework root, 2 levels up from vaultbot/vaultbot_backend/)
except NameError:
    VAULT_DIR = Path(".").resolve()
WEB_DIR = VAULT_DIR / "learningMaterial" / "web"
INDEX_PATH = WEB_DIR / "_index.json"


def _slugify(url: str, max_len: int = 80) -> str:
    """Build a filename-safe slug from a URL (stable per URL)."""
    # Strip scheme + query, keep host + path.
    s = re.sub(r"^https?://", "", url.lower())
    s = re.sub(r"[?#].*$", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    if len(s) > max_len:
        s = s[:max_len].rsplit("-", 1)[0]
    # Append a short hash so two different URLs that slugify identically
    # don't collide, and so the slug is stable regardless of truncation.
    h = hashlib.sha1(url.encode("utf-8", "replace")).hexdigest()[:8]
    return f"{s or 'source'}-{h}"


def _ensure_dirs() -> None:
    WEB_DIR.mkdir(parents=True, exist_ok=True)


def _load_index() -> list[dict[str, Any]]:
    """Load the web source index. Returns [] when the file doesn't exist.

    On corruption (JSON parse error), attempts to rebuild the index from
    the HTML files on disk so archived sources aren't lost. Logs the
    corruption event.
    """
    if not INDEX_PATH.exists():
        return []
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, ValueError) as e:
        # Index is corrupt — rebuild from disk so archived sources
        # aren't lost. This is a notified recovery, not a silent reset.
        import logging

        logging.getLogger(__name__).error(
            "web_source_store: index corrupt (%s), rebuilding from disk", e
        )
        return _rebuild_index_from_disk()
    return []


def _rebuild_index_from_disk() -> list[dict[str, Any]]:
    """Rebuild the index by scanning WEB_DIR for .html files."""
    _ensure_dirs()
    entries: list[dict[str, Any]] = []
    for html_file in WEB_DIR.glob("*.html"):
        # The filename is the slug; the URL is stored in the HTML's
        # <meta name="source-url"> tag if available, otherwise we
        # use the filename as a fallback identifier.
        slug = html_file.stem
        url = ""
        title = ""
        try:
            content = html_file.read_text(encoding="utf-8", errors="replace")
            import re

            m = re.search(r'<meta\s+name="source-url"\s+content="([^"]+)"', content)
            if m:
                url = m.group(1)
            m2 = re.search(r"<title>([^<]+)</title>", content)
            if m2:
                title = m2.group(1).strip()
        except OSError:
            pass
        entries.append(
            {
                "url": url or f"unknown:{slug}",
                "file": html_file.name,
                "title": title or slug,
                "date": "",
                "topics": [],
            }
        )
    _save_index(entries)
    return entries


def _save_index(entries: list[dict[str, Any]]) -> None:
    _ensure_dirs()
    tmp = INDEX_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, INDEX_PATH)


def _find_by_url(entries: list[dict[str, Any]], url: str) -> dict[str, Any] | None:
    for e in entries:
        if e.get("url") == url:
            return e
    return None


def save_source(
    url: str, html: str, title: str = "", topic: str = ""
) -> dict[str, Any] | None:
    """Save a raw HTML page to learningMaterial/web/ and index it.

    Returns the index entry {url, file, title, date, topic}, or None on
    failure. Idempotent: if the URL is already saved, the existing entry is
    returned unchanged (we don't re-download or overwrite — the saved copy
    is the archival snapshot).
    """
    if not url or not html or len(html) < 80:
        return None
    _ensure_dirs()
    entries = _load_index()
    existing = _find_by_url(entries, url)
    if existing is not None:
        # Already archived; don't overwrite the snapshot. Update topic if
        # a new research context referenced it.
        if topic and topic not in (existing.get("topics") or []):
            existing.setdefault("topics", []).append(topic)
            _save_index(entries)
        return existing

    slug = _slugify(url)
    filename = f"{slug}.html"
    path = WEB_DIR / filename
    path.write_text(html, encoding="utf-8", errors="replace")

    entry = {
        "url": url,
        "file": filename,
        "title": title or url,
        "date": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "topics": [topic] if topic else [],
    }
    entries.append(entry)
    _save_index(entries)
    return entry


def fetch_and_save(
    url: str, title: str = "", topic: str = "", timeout: int = 15
) -> dict[str, Any] | None:
    """Fetch the raw HTML for a URL and save it.

    Used when we have a URL but not yet the HTML (e.g. a search hit that
    only returned a snippet). Returns the index entry, or None if the
    URL is empty/short. Raises on fetch or save failure.
    """
    import requests

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()
    html = resp.text
    return save_source(url, html, title=title, topic=topic)


def find_source(url: str) -> dict[str, Any] | None:
    """Look up a saved source by URL. Returns the index entry or None."""
    return _find_by_url(_load_index(), url)


def list_sources(topic: str | None = None) -> list[dict[str, Any]]:
    """List saved sources, optionally filtered by topic."""
    entries = _load_index()
    if topic is None:
        return entries
    return [e for e in entries if topic in (e.get("topics") or [])]


def source_path(filename: str) -> Path:
    """Resolve a saved-source filename to its full path."""
    return WEB_DIR / filename


def read_source_text(filename: str) -> str:
    """Extract clean article text from a saved HTML source (fast fallback).

    Strips scripts/nav and returns the article text — the same cleaning
    FreeSearch.scrape does, but from the saved local copy. Used when no
    vision model is available (the raw HTML has no figures/equations as
    images, but the text is complete and stable).
    """
    path = source_path(filename)
    if not path.exists():
        return ""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "lxml")
        for tag in soup(
            [
                "script",
                "style",
                "nav",
                "footer",
                "header",
                "aside",
                "form",
                "noscript",
                "svg",
            ]
        ):
            tag.decompose()
        main = soup.find("article") or soup.find("main") or soup.find("body")
        text = (
            main.get_text(separator="\n", strip=True)
            if main
            else soup.get_text(separator="\n", strip=True)
        )
        return re.sub(r"\n{3,}", "\n\n", text)[:50000]
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        return ""
