"""Plain-text parser and fragmentation utilities for textbook_ingest.

Extracted from ``custom_tools.textbook_ingest``.

This module contains:

* ``parse_plain_text`` -- split plain text into sections using the same
  source-agnostic structural heading detection as the PDF parser.
* ``_split_on_subheadings`` / ``_split_on_paragraphs`` -- helpers for
  fragmenting oversized sections.
* ``fragment_section`` / ``fragment_sections`` -- fragment an oversized
  section into linked child pages so every note stays under the
  file-unit line ceiling.

Constants ``MAX_NOTE_LINES`` and ``FRAGMENT_TARGET_LINES`` are
redefined here (from ``custom_tools.textbook_ingest``) to avoid a
circular import: ``textbook_ingest`` imports this module, and importing
the constants back from it would create a cycle.
"""

import re

from custom_tools.parsers.markdown_parser import (
    _assign_level,
    _detect_headings,
    _section_sort_key,
)

# ---------------------------------------------------------------------------
# Constants — kept in sync with custom_tools.textbook_ingest.
# Redefined locally to avoid a circular import (textbook_ingest imports
# this module; importing the constants back from it would cycle).
# ---------------------------------------------------------------------------

MAX_NOTE_LINES = 500
FRAGMENT_TARGET_LINES = 350


def parse_plain_text(content_text):
    """Parse plain text into sections."""
    # Detect structural headings with the same source-agnostic logic as the
    # PDF parser: full-line capture + exercise filtering + natural sort.
    candidates = _detect_headings(content_text)

    sections = []
    if len(candidates) >= 3:
        for i, (start, line) in enumerate(candidates):
            end = candidates[i + 1][0] if i + 1 < len(candidates) else len(content_text)
            nl = content_text.find("\n", start)
            if nl == -1 or nl > end:
                nl = end
            body = content_text[nl:end].strip()
            sections.append(
                {
                    "heading": line.strip(),
                    "level": _assign_level(line),
                    "content": body,
                }
            )
    else:
        # Split by double newlines, group into ~3000 char sections
        chunks = re.split(r"\n\s*\n", content_text)
        current_text = ""
        section_num = 1
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            if len(current_text) + len(chunk) > 3000 and current_text:
                sections.append(
                    {
                        "heading": f"Section {section_num}",
                        "level": 2,
                        "content": current_text,
                    }
                )
                section_num += 1
                current_text = chunk
            else:
                current_text += "\n\n" + chunk if current_text else chunk
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
    # Natural-order sort (1, 1.1, 1.2, 2, ...) so navigation follows the book.
    sections = sorted(sections, key=lambda s: _section_sort_key(s["heading"]))
    return "Plain Text Document", sections


# ---------------------------------------------------------------------------
# Fragmentation — keep every note a small, linked file (the file-unit rule)
# ---------------------------------------------------------------------------
#
# A section longer than MAX_NOTE_LINES is fragmented into child pages so the
# vault stays a graph of small connected files, not a pile of monoliths. Two
# strategies, tried in order:
#
#   1. Sub-heading split: if the section body contains markdown headings
#      (#, ##, ###) or structural heading lines (Chapter/Section/1.1), split
#      at those boundaries. Each sub-heading becomes its own child page. This
#      preserves the book's own structure.
#
#   2. Paragraph-boundary split: if there are no usable sub-headings (a wall
#      of text, or a parse-failure mega-section), split on blank-line
#      paragraph boundaries into ~FRAGMENT_TARGET_LINE chunks. The children
#      get sequential names ("part 1", "part 2", ...).
#
# The original section's slug becomes a thin INDEX page: it keeps the same
# filename (so existing TOC links stay valid) but its body is replaced with a
# list of [[child]] links + a one-line summary. The children link back up to
# it via an "Up:" nav link, and to each other via prev/next. No content is
# lost — it's just spread across small connected files, which is exactly what
# the wikilink-graph + embedding-drift retrieval model needs to route well.


def _split_on_subheadings(content):
    """Split a section body at markdown sub-headings.

    Returns a list of (sub_heading, sub_body) pairs, or [] if there are
    fewer than 2 usable sub-headings (caller falls back to paragraph split).

    Only markdown #/##/### headings are used as split points — NOT the
    structural _HEADING_PATTERN. That pattern matches numbered exercise
    lines ("1. A section of wire...") as headings, which would fragment a
    problem set into one-line pages. Sub-heading splitting is for sections
    that genuinely contain nested markdown structure (e.g. a marker-parsed
    chapter with ## subsections).
    """
    lines = content.split("\n")
    md_heading = re.compile(r"^#{1,4}\s+\S")
    bounds = []
    for i, line in enumerate(lines):
        if md_heading.match(line):
            bounds.append(i)
    if len(bounds) < 2:
        return []
    # First heading at line 0 means the section starts with a heading; that's
    # fine. If the first heading is well into the body, the lines before it
    # become a preamble child (so nothing is dropped).
    if bounds[0] > 5:
        bounds.insert(0, 0)
    pieces = []
    for idx, start in enumerate(bounds):
        end = bounds[idx + 1] if idx + 1 < len(bounds) else len(lines)
        body = "\n".join(lines[start:end]).strip()
        if not body:
            continue
        # Derive a sub-heading: first line if it's a heading, else "Introduction".
        first_line = body.split("\n", 1)[0]
        if md_heading.match(first_line):
            sub_h = re.sub(r"^#{1,4}\s+", "", first_line).strip()
        else:
            sub_h = "Introduction"
        pieces.append((sub_h, body))
    return pieces if len(pieces) >= 2 else []


def _split_on_paragraphs(content, target_lines=FRAGMENT_TARGET_LINES):
    """Split a wall-of-text body into ~target_line chunks at paragraph breaks.

    Returns a list of (sub_heading, sub_body) pairs with sequential names.
    If the body has no blank-line paragraph breaks, falls back to splitting
    on single newlines so a giant single-line-per-paragraph block still
    fragments.
    """
    # Prefer blank-line paragraph breaks; if there are none, split on single
    # newlines (some ingested text has one line per paragraph with no blanks).
    # Track the joiner so line accounting matches the emitted child (joining
    # single-newline paragraphs with \n\n would double the line count).
    if re.search(r"\n\s*\n", content):
        paragraphs = re.split(r"\n\s*\n", content)
        joiner = "\n\n"
    else:
        paragraphs = content.split("\n")
        joiner = "\n"
    chunks = []
    current = []
    current_lines = 0
    part = 1
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        plines = para.count("\n") + 1
        if current and current_lines + plines > target_lines:
            chunks.append((f"Part {part}", joiner.join(current)))
            part += 1
            current = [para]
            current_lines = plines
        else:
            current.append(para)
            current_lines += plines
    if current:
        chunks.append((f"Part {part}", joiner.join(current)))
    return chunks


def fragment_section(section):
    """Fragment an oversized section into child pages.

    Returns a list of section dicts [parent_index, child_1, child_2, ...]
    where:
      - parent_index keeps the original 'heading' and 'level' but its
        'content' is replaced with a thin index linking to the children.
        It carries a 'is_fragment_parent' flag so the writer knows to emit
        the index body instead of truncating it.
      - each child has 'heading' = "<parent> — <sub>", 'level' = parent+1,
        'content' = the sub-body, and 'parent_slug_slot' = the parent's
        eventual slug (filled in by the caller after slug generation).

    Returns [section] unchanged if it's under the line cap (no fragmentation
    needed).
    """
    content = section.get("content", "")
    line_count = content.count("\n") + 1
    if line_count <= MAX_NOTE_LINES:
        return [section]

    # Try sub-heading split first (preserves the book's structure).
    pieces = _split_on_subheadings(content)
    if not pieces:
        # Fall back to paragraph-boundary split.
        pieces = _split_on_paragraphs(content)

    if len(pieces) < 2:
        # Couldn't split it meaningfully; leave it alone rather than emit a
        # single identical child + empty parent.
        return [section]

    # Build the parent index page: a thin list of [[child]] links. The
    # actual child slugs are filled in by the writer after slug generation,
    # via the 'fragment_children' + 'is_fragment_parent' markers.
    parent = {
        "heading": section["heading"],
        "level": section.get("level", 2),
        "content": "",  # replaced at write time with the child link list
        "is_fragment_parent": True,
        "fragment_children": [],  # filled in after slug generation
    }
    children = []
    for sub_h, body in pieces:
        child = {
            "heading": f"{section['heading']} — {sub_h}",
            "level": (section.get("level", 2) + 1),
            "content": body,
            "is_fragment_child": True,
        }
        children.append(child)
    parent["fragment_children"] = children
    return [parent, *children]


def fragment_sections(sections):
    """Apply fragment_section across the section list, preserving order.

    A parent that fragments expands in place to [parent, child_1, ...], so
    the TOC and navigation still flow in reading order.
    """
    out = []
    for s in sections:
        out.extend(fragment_section(s))
    return out
