"""
Index-only textbook ingest — the paradigm shift.

Old way: ingest copied the whole textbook into the vault as verbatim notes
(slow OCR, monolith files, cluttered graph, retrieval smears across whole
chapters). The math-textbook problem was unsolvable in that model: equations
are vector-drawn, so any text extraction drops them, and OCR was the only
recourse — hours per book.

New way: ingest builds ONLY an index. It reads the PDF's heading structure
(fast — PyMuPDF font metadata, seconds not hours) and writes one TOC note
per book whose entries are POINTERS, not content:

    - [[textbook-page-22]] Finding Zeros and y-Intercepts of a Function
      > page 22 · calculus-volume-1.pdf

Each pointer says "this topic lives on page N of this PDF." The PDF stays
the source of truth, untouched. No content is copied, no OCR runs, no
monolith notes clutter the graph.

The LLM reads on demand: when the user asks about a topic, the LLM uses the
TOC to find the relevant page, calls the `textbook_read_page` tool (which
renders that one page to an image and sends it to a vision-capable model),
and writes a NOTE capturing what it learned — with provenance:

    > source: [[calculus-volume-1.pdf]] page 22

So the graph is ONLY ever LLM-curated: every note is something the model
actually read, understood, and chose to record. Drift/retrieval operate on
clean small LLM-written units (the file-unit principle). No OCR, ever — the
model sees the rendered page the way a human does, equations and all.

This module:
  - build_pdf_index(pdf_path) -> (title, [entries]) where each entry is
    {heading, page, level}. Fast (font metadata only, no content extraction).
  - write_index_toc(title, source_path, entries) -> markdown for the TOC note.
  - index_learning_material(dir) -> ingest all new PDFs as index-only TOCs.

Idempotent: a book already indexed (its source-key is in an existing TOC)
is skipped. Re-indexing replaces the old TOC in place.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Resolve paths relative to this file. textbook_index.py lives in
# vaultbot_backend/, so parent = vaultbot_backend, parent.parent = Vault2
# (the vault root). VAULT_DIR is the vault root, not one level above it.
try:
    VAULT_DIR = Path(__file__).resolve().parent.parent
except NameError:
    VAULT_DIR = Path(".").resolve()
BACKEND_DIR = VAULT_DIR / "vaultbot_backend"
TEXTBOOKS_DIR = VAULT_DIR / "vaultbot" / "textbooks"


# ---------------------------------------------------------------------------
# PDF index extraction (fast: font metadata only, no content copy, no OCR)
# ---------------------------------------------------------------------------

def _source_key(source: str) -> str:
    """Stable hash of a source path — identifies a prior index of it."""
    return hashlib.sha1(source.encode("utf-8", "replace")).hexdigest()[:12]


def _source_key_line(key: str) -> str:
    return "<!-- vaultbot:textbook-source-key %s -->" % key


def _slugify(text: str, max_len: int = 80) -> str:
    text = re.sub(r"\[edit\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[^a-zA-Z0-9\s-]", "", text)
    text = re.sub(r"\s+", "-", text.strip())
    text = re.sub(r"-+", "-", text)
    text = text.strip("-").lower()
    if len(text) > max_len:
        text = text[:max_len].rsplit("-", 1)[0]
    return text or "untitled"


# Front-matter noise patterns to reject as headings. Textbook title/copyright
# pages have bold large text (ISBN numbers, publisher names, author names with
# letter-spacing) that the font heuristic flags as headings but aren't real
# section titles. This keeps the LLM's index navigable.
_FRONTMATTER_RE = re.compile(
    r"(ISBN|978-|©|Copyright|All rights reserved|OpenStax|Rice University|"
    r"Trademarks|Philanthropic|PAPERBACK|DIGITAL VERSION|ORIGINAL PUBLICATION|"
    r"highlighting and note-taking|Study where you want|Access\. The future)",
    re.IGNORECASE,
)


def _is_real_heading(text: str) -> bool:
    """Filter out front-matter junk the font heuristic mis-flags as headings."""
    if not text:
        return False
    if _FRONTMATTER_RE.search(text):
        return False
    # All-caps lines longer than 60 chars are almost always publisher/author
    # blocks, not section titles (real section titles are title-case or
    # sentence-case). Short all-caps like "LIMITS" or "PREFACE" are kept
    # UNLESS they look like a letter-spaced publisher name.
    if len(text) > 60 and text == text.upper():
        return False
    # Short all-caps words that are publisher/author artifacts after the
    # letter-spacing collapse ("OPENS TAX", "RICEU NIVERSITY"). A real short
    # all-caps heading is one word ("LIMITS"); two all-caps tokens is noise.
    if text == text.upper() and len(text.split()) >= 2 and len(text) < 40:
        return False
    # Lines that are mostly digits (page-number lists in a printed TOC).
    digits = sum(1 for c in text if c.isdigit())
    if digits > len(text) * 0.4 and len(text) > 10:
        return False
    # Printed-TOC lines: end in a number (e.g. "Limits 105", "Derivatives 187").
    # Real section headings don't end in a bare page number.
    if re.search(r"\s\d{1,4}$", text) and len(text.split()) <= 6:
        return False
    return True


def build_pdf_index(pdf_path: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Build a heading→page index from a PDF, fast.

    Uses PyMuPDF's font metadata to find headings (larger + bolder than body
    text) and records the PAGE each heading starts on. No content is
    extracted — only the heading text + page number + level. This is seconds
    for a whole book, not hours, because it never runs OCR and never copies
    body text.

    Returns (title, entries) where entries is a list of
    {heading, page, level}. Falls back to a regex text-layer scan if PyMuPDF
    font detection finds too few headings (scanned PDFs); in that case pages
    are still recorded so the LLM can still be pointed at the right page.
    """
    try:
        import fitz
    except ImportError:
        return _build_pdf_index_regex(pdf_path)

    doc = fitz.open(pdf_path)
    spans: List[Tuple[int, str, float, bool, float]] = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        for b in page.get_text("dict")["blocks"]:
            if b.get("type") != 0:
                continue
            for line in b.get("lines", []):
                for span in line.get("spans", []):
                    text = span["text"].strip()
                    if not text:
                        continue
                    spans.append((page_num, text, span["size"],
                                  bool(span["flags"] & 16), line["bbox"][1]))

    if not spans:
        doc.close()
        return _pdf_title(doc, pdf_path), []

    # Body text = most common font size (statistical mode).
    from collections import Counter
    size_counts = Counter(s[2] for s in spans if 7.0 <= s[2] <= 20.0)
    if not size_counts:
        doc.close()
        return _pdf_title(doc, pdf_path), []
    body_size = size_counts.most_common(1)[0][0]

    # Headings = larger than body AND bold. Merge consecutive heading spans
    # on the same line into one heading, and record the page it starts on.
    # Filter out front-matter noise (title pages, copyright, ISBN, all-caps
    # letter-spaced author names) so the index is clean enough for the LLM
    # to navigate.
    entries: List[Dict[str, Any]] = []
    seen_headings: set = set()
    i = 0
    while i < len(spans):
        page_num, text, size, bold, _y = spans[i]
        if size > body_size and bold:
            parts = [text]
            j = i + 1
            while j < len(spans):
                p2, t2, s2, b2, _y2 = spans[j]
                if s2 > body_size and b2 and p2 == page_num:
                    parts.append(t2)
                    j += 1
                else:
                    break
            heading_text = " ".join(parts).strip()
            # Collapse the letter-spacing artifact ("G ILBERT S TRANG" ->
            # "GILBERT STRANG") then drop all-caps author/publisher lines.
            collapsed = re.sub(r"([A-Z])\s([A-Z])", r"\1\2", heading_text)
            if (len(collapsed) >= 3
                    and not re.match(r"^\d+\s+\d+$", collapsed)
                    and collapsed not in seen_headings
                    and _is_real_heading(collapsed)):
                seen_headings.add(collapsed)
                entries.append({
                    "heading": collapsed,
                    "page": page_num + 1,  # 1-indexed for humans
                    "level": 2,
                })
            i = j
        else:
            i += 1
    doc.close()

    title = _pdf_title(fitz.open(pdf_path), pdf_path) if entries else _pdf_title_fallback(pdf_path)
    if len(entries) < 3:
        # Too few headings via fonts (maybe a scanned book, or unusual
        # typesetting). Fall back to a regex text-layer scan which still
        # records page numbers so the LLM can be pointed at the right page.
        t2, e2 = _build_pdf_index_regex(pdf_path)
        if len(e2) > len(entries):
            return t2, e2
    return title, entries


def _pdf_title(doc, pdf_path: str) -> str:
    try:
        if doc.metadata and doc.metadata.get("title"):
            t = doc.metadata["title"]
            if t and t not in ("Untitled", "PDF Document"):
                doc.close()
                return t
    except Exception:
        pass
    doc.close()
    return _pdf_title_fallback(pdf_path)


def _pdf_title_fallback(pdf_path: str) -> str:
    stem = Path(pdf_path).stem
    stem = re.sub(r"_-__WEB.*$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"_-_WEB.*$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"[_\-]+", " ", stem).strip()
    return stem or Path(pdf_path).stem


def _build_pdf_index_regex(pdf_path: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Fallback index builder: regex heading detection over the text layer.

    Still records page numbers (via form-feed page breaks) so the LLM can be
    pointed at the right page even when font metadata is unavailable.
    """
    try:
        import fitz
        doc = fitz.open(pdf_path)
        title = _pdf_title(doc, pdf_path)
        entries: List[Dict[str, Any]] = []
        # Use the structural heading pattern from textbook_ingest for
        # consistency, scanning page by page so we keep page numbers.
        heading_re = re.compile(
            r"^(CHAPTER\s+[IVXLCDM\d]+|Chapter\s+\d+|Section\s+\d+(?:\.\d+)*"
            r"|Part\s+[IVXLCDM\d]+|\d+\.\s+\S|\d+\.\d+(?:\.\d+)*\s+\S)",
            re.MULTILINE,
        )
        for page_num in range(len(doc)):
            text = doc[page_num].get_text("text")
            for m in heading_re.finditer(text):
                line = m.group(0).strip()
                if len(line) >= 3:
                    entries.append({"heading": line, "page": page_num + 1, "level": 2})
        doc.close()
        # Dedupe by heading, keep first page.
        seen: set = set()
        deduped: List[Dict[str, Any]] = []
        for e in entries:
            if e["heading"] not in seen:
                seen.add(e["heading"])
                deduped.append(e)
        return title, deduped
    except Exception:
        return _pdf_title_fallback(pdf_path), []


# ---------------------------------------------------------------------------
# TOC note writing (pointers, not content)
# ---------------------------------------------------------------------------

def write_index_toc(title: str, source_path: str,
                    entries: List[Dict[str, Any]],
                    skey: str = "") -> str:
    """Build the markdown for an index-only TOC note.

    Each entry is a pointer: the heading text + which page of which PDF it
    lives on. No content is copied. The LLM later reads the page on demand
    via the textbook_read_page tool.
    """
    rel_pdf = os.path.relpath(source_path, VAULT_DIR).replace("\\", "/")
    lines = [
        "# %s — Index" % title,
        "",
        "> **Source PDF:** %s" % rel_pdf,
        "> **Pages:** %d" % (max((e["page"] for e in entries), default=0)),
        "> **Indexed:** %s" % time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "> This is an index, not a copy. Each entry points to a page in the "
        "source PDF. Ask me about a topic and I'll read the page and write "
        "what I learn into the vault with provenance.",
        "",
        "## Contents",
        "",
    ]
    for e in entries:
        lines.append("- **%s** — page %d" % (e["heading"], e["page"]))
    lines.append("")
    lines.append("#textbook #index #ingested")
    if skey:
        lines.append(_source_key_line(skey))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Idempotency + ingest driver
# ---------------------------------------------------------------------------

def _find_prior_index(skey: str) -> Optional[Path]:
    """Find an existing index TOC carrying this source-key, if any."""
    if not TEXTBOOKS_DIR.exists():
        return None
    marker = _source_key_line(skey)
    for toc in TEXTBOOKS_DIR.glob("*-index.md"):
        try:
            if marker in toc.read_text(encoding="utf-8", errors="replace"):
                return toc
        except Exception:
            continue
    return None


def index_one_pdf(pdf_path: str) -> Dict[str, Any]:
    """Index a single PDF: build the heading→page index, write a TOC note.

    Returns a result dict {status, file, title, entries, toc_note, error?}.
    Idempotent: a book already indexed (source-key present) is skipped.
    """
    pdf_path = str(pdf_path)
    skey = _source_key(pdf_path)
    prior = _find_prior_index(skey)
    if prior is not None:
        return {"status": "skipped", "file": os.path.basename(pdf_path),
                "toc_note": str(prior.relative_to(VAULT_DIR))}

    title, entries = build_pdf_index(pdf_path)
    if not entries:
        return {"status": "error", "file": os.path.basename(pdf_path),
                "error": "no headings found (scanned PDF with no text layer? "
                         "marker OCR fallback not yet wired for index mode)"}

    TEXTBOOKS_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slugify(title)
    toc_slug = "%s-index" % slug
    toc_filename = "%s.md" % toc_slug
    toc_path = TEXTBOOKS_DIR / toc_filename
    toc_content = write_index_toc(title, pdf_path, entries, skey=skey)
    toc_path.write_text(toc_content, encoding="utf-8")
    return {
        "status": "ok",
        "file": os.path.basename(pdf_path),
        "title": title,
        "entries": len(entries),
        "toc_note": str(toc_path.relative_to(VAULT_DIR)),
    }


def index_learning_material(learning_dir: str) -> Dict[str, Any]:
    """Index every new PDF in learningMaterial/ as an index-only TOC.

    Returns a summary {indexed, skipped, errors, details}. This is what the
    /ingest_learning_material endpoint calls in the new paradigm.
    """
    learning_dir = Path(learning_dir)
    if not learning_dir.exists():
        return {"error": "learningMaterial/ not found at %s" % learning_dir,
                "indexed": 0, "skipped": 0, "errors": 0, "details": []}
    pdfs = sorted(learning_dir.glob("*.pdf"))
    if not pdfs:
        return {"indexed": 0, "skipped": 0, "errors": 0, "details": [],
                "message": "No PDFs in learningMaterial/"}

    details = []
    indexed = skipped = errors = 0
    for pdf in pdfs:
        r = index_one_pdf(str(pdf))
        details.append(r)
        if r["status"] == "ok":
            indexed += 1
        elif r["status"] == "skipped":
            skipped += 1
        else:
            errors += 1
    return {
        "indexed": indexed,
        "skipped": skipped,
        "errors": errors,
        "details": details,
        "message": "Indexed %d new textbook(s); %d skipped, %d errors."
                   % (indexed, skipped, errors),
    }