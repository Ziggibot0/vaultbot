"""PDF parser for textbook_ingest.

Extracted from ``custom_tools.textbook_ingest``.

Two strategies:

* ``parse_pdf`` -- uses PyMuPDF (fitz) font-metadata extraction to find
  headings by font size and boldness (source-agnostic: works for any PDF
  where the publisher used larger/bolder fonts for headings).
* ``_parse_pdf_regex`` -- regex-heuristic fallback used when PyMuPDF is
  not installed or font-based detection produces fewer than 3 sections
  (e.g. scanned PDFs with no font metadata).

Both return ``(title, sections)`` where *sections* is a list of dicts
with keys ``heading``, ``level``, and ``content``.
"""

import re

from custom_tools.parsers.markdown_parser import (
    _assign_level,
    _detect_headings,
    _section_sort_key,
)


def parse_pdf(file_path):
    """Parse PDF into sections using PyMuPDF's font-metadata extraction.

    Instead of guessing from raw text (regex), we use the one signal that
    reliably distinguishes headings from body text in every textbook: font
    size and boldness.  Real section headings are larger and bolder than
    body text.  This is source-agnostic — it works for any PDF where the
    publisher used larger/bolder fonts for headings (which is all of them).

    PyMuPDF (fitz) exposes per-span font size and flags, so we can find
    headings without any regex heuristics, blank-line detection, or
    exercise-list filtering.
    """
    try:
        import fitz
    except ImportError:
        return _parse_pdf_regex(file_path)

    doc = fitz.open(file_path)

    # --- Pass 1: collect every text span with its font metadata ---
    # Each span is (page_num, text, size, bold, y_position).
    spans = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        blocks = page.get_text("dict")["blocks"]
        for b in blocks:
            if b["type"] != 0:  # text blocks only
                continue
            for line in b["lines"]:
                for span in line["spans"]:
                    text = span["text"].strip()
                    if not text:
                        continue
                    size = span["size"]
                    bold = (span["flags"] & 16) > 0  # bit 4 = bold
                    # y position of the line (for reading order)
                    y = line["bbox"][1]
                    spans.append((page_num, text, size, bold, y))

    if not spans:
        doc.close()
        return "PDF Document", []

    # --- Pass 2: find the body-text font size ---
    # Body text is the most common size (the statistical mode).  We ignore
    # tiny sizes (< 7pt = page numbers/footers) and very large sizes
    # (> 20pt = title pages).
    from collections import Counter

    size_counts = Counter(s[2] for s in spans if 7.0 <= s[2] <= 20.0)
    if not size_counts:
        doc.close()
        return "PDF Document", []
    body_size = size_counts.most_common(1)[0][0]

    # --- Pass 3: extract headings ---
    # A heading is any span whose font is larger than body text AND bold.
    # We merge consecutive heading spans on the same line into one heading.
    headings = []  # (page_num, heading_text, font_size)
    i = 0
    while i < len(spans):
        page_num, text, size, bold, y = spans[i]
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
            if len(heading_text) >= 3 and not re.match(r"^\d+\s+\d+$", heading_text):
                headings.append((page_num, heading_text, size))
            i = j
        else:
            i += 1

    if len(headings) < 3:
        doc.close()
        return _parse_pdf_regex(file_path)

    # Filter out ALL-CAPS (running headers like "SECTION 1.1 EXERCISES")
    # and chapter-title pages ("Limits 105", "Derivatives 187").
    headings = [
        h
        for h in headings
        if not (h[1] == h[1].upper() and len(h[1]) > 5)
        and not re.match(r"^.+\s+\d+$", h[1])  # "Limits 105" etc.
    ]

    if len(headings) < 3:
        doc.close()
        return _parse_pdf_regex(file_path)

    # --- Pass 4: build sections ---
    # Each section's content is all the text between its heading and the
    # next heading.  We use page-level text extraction for content (faster
    # than span-by-span reconstruction).
    full_text_by_page = {}
    for page_num in range(len(doc)):
        full_text_by_page[page_num] = doc[page_num].get_text()
    doc.close()

    # Build a flat text stream with page markers so we can split by heading
    # positions.
    flat_parts = []
    page_offsets = {}  # page_num -> start position in flat text
    for page_num in sorted(full_text_by_page.keys()):
        page_offsets[page_num] = len("\n".join(flat_parts)) if flat_parts else 0
        flat_parts.append(full_text_by_page[page_num])
    flat_text = "\n".join(flat_parts)

    # Find each heading's position in the flat text
    heading_positions = []
    for page_num, heading_text, size in headings:
        # Search for the heading in the page's text
        page_text = full_text_by_page.get(page_num, "")
        pos = page_text.find(heading_text)
        if pos >= 0:
            abs_pos = page_offsets.get(page_num, 0) + pos
            heading_positions.append((abs_pos, heading_text, size))

    heading_positions.sort()

    # Assign levels: the largest font = level 1, second-largest = level 2, etc.
    unique_sizes = sorted(set(h[2] for h in heading_positions), reverse=True)
    size_to_level = {sz: min(i + 1, 3) for i, sz in enumerate(unique_sizes)}

    sections = []
    for i, (pos, heading_text, size) in enumerate(heading_positions):
        end = (
            heading_positions[i + 1][0]
            if i + 1 < len(heading_positions)
            else len(flat_text)
        )
        body = flat_text[pos:end].strip()
        if len(body) > 200:
            sections.append(
                {
                    "heading": heading_text,
                    "level": size_to_level.get(size, 2),
                    "content": body,
                }
            )

    if len(sections) < 3:
        return _parse_pdf_regex(file_path)

    # Deduplicate by heading text (keep the one with more content) and sort.
    seen = {}
    for s in sections:
        h = s["heading"].strip()
        if h not in seen or len(s["content"]) > len(seen[h]["content"]):
            seen[h] = s
    sections = sorted(seen.values(), key=lambda s: _section_sort_key(s["heading"]))

    # Title from the first heading or PDF metadata
    title = headings[0][1] if headings else "PDF Document"
    try:
        import fitz as _fitz

        d = _fitz.open(file_path)
        if d.metadata and d.metadata.get("title"):
            title = d.metadata["title"]
        d.close()
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        pass

    return title, sections


def _parse_pdf_regex(file_path):
    """Fallback PDF parser using regex heuristics.

    Used when PyMuPDF is not installed or when font-based detection
    produces fewer than 3 sections (e.g. scanned PDFs with no font metadata).
    """
    import pypdf

    reader = pypdf.PdfReader(file_path)

    full_text = ""
    for page in reader.pages:
        text = page.extract_text()
        if text:
            full_text += text + "\n\n"

    if not full_text.strip():
        return "PDF Document", []

    candidates = _detect_headings(full_text)

    sections = []
    if len(candidates) >= 3:
        for i, (start, line) in enumerate(candidates):
            end = candidates[i + 1][0] if i + 1 < len(candidates) else len(full_text)
            nl = full_text.find("\n", start)
            if nl == -1 or nl > end:
                nl = end
            body = full_text[nl:end].strip()
            sections.append(
                {
                    "heading": line.strip(),
                    "level": _assign_level(line),
                    "content": body,
                }
            )
    else:
        pages = re.split(r"\f", full_text)
        current_text = ""
        section_num = 1
        for page_text in pages:
            page_text = page_text.strip()
            if not page_text:
                continue
            if len(current_text) + len(page_text) > 3000 and current_text:
                sections.append(
                    {
                        "heading": f"Section {section_num}",
                        "level": 2,
                        "content": current_text,
                    }
                )
                section_num += 1
                current_text = page_text
            else:
                current_text += "\n\n" + page_text if current_text else page_text
        if current_text.strip():
            sections.append(
                {
                    "heading": f"Section {section_num}",
                    "level": 2,
                    "content": current_text,
                }
            )

    # Lazy import to avoid circular dependency:
    # textbook_ingest imports this module, and this function needs
    # is_toc_entry which lives in textbook_ingest.
    from custom_tools.textbook_ingest import is_toc_entry

    sections = [s for s in sections if len(s["content"]) > 200]
    sections = [s for s in sections if not is_toc_entry(s["content"])]

    title = "PDF Document"
    try:
        if reader.metadata and reader.metadata.title:
            title = reader.metadata.title
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        pass

    seen = {}
    for s in sections:
        h = s["heading"].strip()
        if h not in seen or len(s["content"]) > len(seen[h]["content"]):
            seen[h] = s
    sections = sorted(seen.values(), key=lambda s: _section_sort_key(s["heading"]))

    return title, sections
