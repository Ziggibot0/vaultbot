"""
Agent-authored tool: textbook_ingest

Downloads or reads a textbook/reference resource, parses it into sections,
and writes each section as a linked markdown note in the vault.

Supported source types:
  - HTML (web pages, Wikipedia articles, OpenStax, etc.)
  - PDF (via pypdf)
  - Plain text (Project Gutenberg .txt files, etc.)
  - Markdown (local .md files)

Each section becomes a vault note with:
  - Navigation links (prev / next / table of contents)
  - Subject tags
  - Source attribution

Safety:
  - Only writes to 09-Textbooks/ -- never touches backend code
  - Size limits: max 100 sections, max 10,000 chars per section
  - All errors are caught and returned, never raised
  - File paths are sanitized to prevent directory traversal
"""

SCHEMA = {
    "name": "textbook_ingest",
    "description": (
        "Download or read a textbook/reference resource and ingest it into "
        "the vault as linked notes. Accepts URLs (HTTP/HTTPS) or local file "
        "paths. Supports HTML, PDF, plain text, and Markdown. Each section "
        "becomes a linked vault note with navigation. This is how VaultBot "
        "learns systematically -- ingesting a textbook is literally adding "
        "knowledge to its mind."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": (
                    "URL (http/https) or local file path to the resource. "
                    "Examples: 'https://en.wikipedia.org/wiki/Thermodynamics', "
                    "'C:/path/to/textbook.pdf', 'https://www.gutenberg.org/files/1342/1342-0.txt'"
                ),
            },
            "subject": {
                "type": "string",
                "description": (
                    "Subject tag for the notes (e.g., 'physics', 'biology', "
                    "'thermodynamics'). Used in filenames and tags."
                ),
                "default": "",
            },
            "max_sections": {
                "type": "integer",
                "description": (
                    "Maximum number of sections to ingest. Default 50. Use a "
                    "smaller number for testing."
                ),
                "default": 50,
            },
            "title": {
                "type": "string",
                "description": (
                    "Override the auto-detected title for the table of contents note."
                ),
                "default": "",
            },
            # NOTE: force_ocr was for the old marker-pdf OCR fallback.
            # Removed along with marker-pdf — the vision model reads
            # pages on-demand via textbook_read_page now. The text-layer
            # extract (parse_pdf) is always used at ingest time.
        },
        "required": ["source"],
    },
}

import contextlib  # noqa: E402
import hashlib  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import requests  # noqa: E402

# ---------------------------------------------------------------------------
# Path setup -- resolve from this file's location, never assume CWD
# ---------------------------------------------------------------------------
from paths import FRAMEWORK_ROOT  # noqa: E402

BACKEND_DIR = FRAMEWORK_ROOT / "vaultbot_backend"
VAULT_DIR = FRAMEWORK_ROOT
TEXTBOOKS_DIR = VAULT_DIR / "09-Textbooks"

# Limits
MAX_SECTIONS = 1000
# Full chapters are ingested verbatim — no content truncation at the old
# 10K cap (which was cutting off ~90% of a chapter and losing both info and
# wikilink opportunities).  The 200K safety cap only catches pathological
# mega-sections (e.g. a PDF parsing error that merges a whole book into one
# section); real sections run 2K-80K chars.  Retrieval quality is preserved
# for long notes by chunked embedding in vault_indexer._get_chunked_embedding.
MAX_SECTION_CHARS = 200_000
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

# File-unit principle: an Obsidian page is the unit of intelligence, NOT a
# vector chunk. A page that's too long smears its whole-file embedding across
# unrelated sub-topics (asking for "mitochondria" returns a whole averaged
# "Cell Structure" chapter) and starves the link graph of hop points. So any
# section longer than this gets FRAGMENTED into linked child pages at ingest
# time: the parent becomes a thin index page linking to its children, and the
# children carry prev/next/up navigation. Nothing is lost — the full text is
# just spread across small connected files instead of one monolith. This is
# what makes `embedding_drift`'s per-file vector scooching actually work:
# each child is a clean unit whose vector can move toward the queries it
# proves useful for. ~500 lines is the cap the operator set as the file-unit ceiling.
MAX_NOTE_LINES = 500
# When fragmenting a wall-of-text section (no sub-headings to split on), the
# target size of each child page. Aimed at the 500-line ceiling with margin.
FRAGMENT_TARGET_LINES = 350


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def slugify(text, max_len=80):
    """Convert heading text to a filename-safe slug (no .md extension)."""
    text = re.sub(r"\[edit\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[^a-zA-Z0-9\s-]", "", text)
    text = re.sub(r"\s+", "-", text.strip())
    text = re.sub(r"-+", "-", text)
    text = text.strip("-").lower()
    if len(text) > max_len:
        text = text[:max_len].rsplit("-", 1)[0]
    return text or "untitled"


def truncate_content(content, max_chars=MAX_SECTION_CHARS):
    """Truncate content to max_chars, adding a note if truncated.

    The cap is now 200K (was 10K) — effectively no truncation for real
    textbook sections, which keeps full content + all wikilink opportunities.
    The cap only catches pathological parse errors that merge a whole book
    into one section.
    """
    if len(content) <= max_chars:
        return content
    return (
        content[:max_chars]
        + "\n\n*[... content truncated (section exceeded 200K char safety cap) ...]*"
    )


def is_toc_entry(content):
    """Check if a section's content is just a table of contents entry."""
    stripped = content.strip()
    lines = [ln for ln in stripped.split("\n") if ln.strip()]

    # TOC entries are typically 1-3 lines
    if len(lines) > 3:
        return False

    # Check for dot-leader pattern: dots separated by spaces, 5+ occurrences
    if re.search(r"(\.\s){5,}", stripped):
        return True

    # Also check: 5+ consecutive dots (no spaces)
    return bool(re.search(r"\.{5,}", stripped))


def safe_filename(slug):
    """Ensure a slug is safe and ends with .md."""
    # Remove any path components
    slug = os.path.basename(slug)
    # Remove dangerous characters
    slug = re.sub(r'[<>:"/\\|?*]', "", slug)
    # Ensure it ends with .md
    if not slug.endswith(".md"):
        slug = slug + ".md"
    return slug


def source_key(source):
    """Stable hash of a source URL/path — identifies a prior ingest of it.

    Used to make ingestion idempotent: the TOC note carries this key, so a
    re-ingest can find the old TOC, read its old section slugs, and delete
    any notes that no longer exist in the new run (stale orphans).
    """
    return hashlib.sha1(source.encode("utf-8", "replace")).hexdigest()[:12]


def _source_key_line(key):
    """The hidden marker line written into the TOC note."""
    return f"<!-- vaultbot:textbook-source-key {key} -->"


def _max_sections_line(max_sections):
    """Hidden marker recording the cap used for this ingest.

    Lets the /ingest_learning_material button detect a prior ingest that
    was capped below the ingester's own ceiling and top it up by re-ingesting
    with a higher cap (the ingester's stale-note replacement makes this a
    clean append, not a duplication).
    """
    return f"<!-- vaultbot:textbook-max-sections {int(max_sections or 0)} -->"


def find_prior_max_sections(toc_path):
    """Read the max_sections marker from a TOC note. Returns 0 if absent."""
    try:
        text = Path(toc_path).read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        return 0
    m = re.search(r"vaultbot:textbook-max-sections (\d+)", text)
    return int(m.group(1)) if m else 0


def find_prior_ingest(key):
    """Scan existing TOC notes for one carrying the given source key.

    Returns (toc_path, old_section_slugs) or (None, []).
    The old section slugs are read from the TOC's [[slug|heading]] entries
    so we can delete notes that a re-ingest no longer produces.
    """
    if not TEXTBOOKS_DIR.exists():
        return None, []
    for toc_path in TEXTBOOKS_DIR.glob("*-toc.md"):
        try:
            text = toc_path.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            continue
        if _source_key_line(key) not in text:
            continue
        # Found the prior TOC. Extract its section slugs from wikilinks.
        slugs = re.findall(r"\[\[([^\]|]+)\|", text)
        # Strip any trailing .md the slug might have picked up.
        slugs = [s.rstrip(".md") for s in slugs]
        return toc_path, slugs
    return None, []


def remove_stale_notes(stale_slugs):
    """Delete notes in textbooks/ whose slug is in stale_slugs.

    Returns the list of relative paths actually removed. Never raises — a
    missing file or permission error is skipped.
    """
    removed = []
    for slug in stale_slugs:
        filename = safe_filename(slug)
        path = TEXTBOOKS_DIR / filename
        if path.exists():
            try:
                path.unlink()
                removed.append(str(path.relative_to(VAULT_DIR)))
            except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                pass
    return removed


# ---------------------------------------------------------------------------
# Source detection and fetching
# ---------------------------------------------------------------------------
#
# NOTE: The old marker-pdf OCR fallback (parse_with_marker) was removed.
# It pulled in torch + surya-ocr + transformers (~3 GB of downloads), which
# made the first-time install take forever on a fresh machine.  The vision
# model now reads individual pages on-demand via textbook_read_page (which
# uses PyMuPDF to render a page to an image and sends it to the LLM), so
# the heavy OCR pipeline is no longer needed at ingest time.  parse_pdf()
# (PyMuPDF font-metadata extraction) builds the TOC index; equations are
# read later, one page at a time, only when the user asks about them.


def detect_source_type(source):
    """Detect the type of source from URL or file path."""
    source_lower = source.lower()
    if source.startswith(("http://", "https://")):
        if source_lower.endswith(".pdf"):
            return "pdf_url"
        elif source_lower.endswith((".txt", ".text")):
            return "text_url"
        elif source_lower.endswith((".md", ".markdown")):
            return "markdown_url"
        else:
            return "html_url"
    else:
        # Local file
        if source_lower.endswith(".pdf"):
            return "pdf_file"
        elif source_lower.endswith((".txt", ".text")):
            return "text_file"
        elif source_lower.endswith((".md", ".markdown")):
            return "markdown_file"
        elif source_lower.endswith((".html", ".htm")):
            return "html_file"
        else:
            return "auto_file"


def _auto_detect_subject(source, source_type, title="", sections=None):
    """When the caller passes no meaningful subject (e.g. "unknown" or ""),
    derive one from the source so note filenames are navigable instead of
    all being "unknown-section-N.md".

    Strategy (first hit wins):
      1. PDF metadata title (if it's meaningful, not "Untitled"/"PDF Document").
      2. First section heading (the first real chapter/section title).
      3. The filename stem (cleaned up — "college-physics-2e_-_WEB" →
         "college-physics-2e").
    """
    # If the caller gave a real subject (not "unknown"/empty), respect it.
    if sections is None:
        sections = []
    # 1. Metadata title
    if title and title not in (
        "Untitled",
        "PDF Document",
        "Markdown Document",
        "Plain Text Document",
    ):
        clean = re.sub(r"[_\-]+", " ", title).strip()
        if clean and len(clean) > 2:
            return slugify(clean)[:60]
    # 2. First section heading
    if sections:
        first_heading = sections[0].get("heading", "").strip()
        if first_heading and first_heading.lower() not in ("untitled", "section 1"):
            return slugify(first_heading)[:60]
    # 3. Filename stem
    try:
        from pathlib import Path as _P

        stem = _P(source).stem
        # Strip common suffixes like "_-__WEB", "_-_WEB_oNlbGYl".
        stem = re.sub(r"_-__WEB.*$", "", stem, flags=re.IGNORECASE)
        stem = re.sub(r"_-_WEB.*$", "", stem, flags=re.IGNORECASE)
        stem = re.sub(r"[_\-]+", " ", stem).strip()
        if stem and len(stem) > 2:
            return slugify(stem)[:60]
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        pass
    return "textbook"


def fetch_url(url, timeout=30):
    """Fetch content from a URL with size limit."""
    r = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0 (VaultBot/1.0; textbook ingest tool)"},
        stream=True,
    )
    r.raise_for_status()

    # Download with size limit
    content = b""
    for chunk in r.iter_content(chunk_size=8192):
        content += chunk
        if len(content) > MAX_DOWNLOAD_BYTES:
            raise ValueError(
                f"Download exceeds {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB limit"
            )

    # Detect encoding
    encoding = r.encoding or "utf-8"
    return content.decode(encoding, errors="replace")


def fetch_pdf_url(url, timeout=60):
    """Download a PDF from URL to a temp file."""
    import tempfile

    r = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0 (VaultBot/1.0)"},
        stream=True,
    )
    r.raise_for_status()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        total = 0
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)
            total += len(chunk)
            if total > MAX_DOWNLOAD_BYTES:
                f.close()
                os.unlink(f.name)
                raise ValueError(
                    f"PDF download exceeds "
                    f"{MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB limit"
                )
        return f.name


# ---------------------------------------------------------------------------
# Parsers -- extracted into custom_tools.parsers package
# ---------------------------------------------------------------------------
#
# The four format parsers (HTML, PDF, Markdown, plain text) plus the
# structural heading-detection utilities and section-fragmentation helpers
# live in custom_tools/parsers/.  They are imported here so run() and
# external callers can use them with the same API as before.
#
# is_toc_entry (defined above) is imported lazily inside each parser to
# avoid a circular import: this module imports the parsers, and the
# parsers need is_toc_entry from this module.

from custom_tools.parsers.html_parser import parse_html  # noqa: E402
from custom_tools.parsers.markdown_parser import (  # noqa: E402, F401 — re-exported for backward compat
    _assign_level,
    _clean_heading,
    _detect_headings,
    _full_line,
    _is_learning_objective,
    _looks_like_heading,
    _section_sort_key,
    parse_markdown,
)
from custom_tools.parsers.pdf_parser import (  # noqa: E402, F401 — _parse_pdf_regex re-exported for backward compat
    _parse_pdf_regex,
    parse_pdf,
)
from custom_tools.parsers.text_parser import (  # noqa: E402, F401 — re-exported for backward compat
    _split_on_paragraphs,
    _split_on_subheadings,
    fragment_section,
    fragment_sections,
    parse_plain_text,
)

# ---------------------------------------------------------------------------
# Note writing
# ---------------------------------------------------------------------------


def create_section_note(
    section,
    subject,
    source_url,
    index,
    total,
    prev_slug,
    next_slug,
    toc_slug,
    base_title,
):
    """Create the markdown content for a section note.

    Slugs should NOT include .md extension -- they are wikilink references.

    Three note shapes:
      - normal: the section's full content.
      - fragment parent: a thin index page linking to its child pages (the
        section was too long and got fragmented; the parent keeps the
        original slug so TOC links stay valid).
      - fragment child: the sub-body, with an "Up:" nav link back to the
        parent index page.
    """
    heading = section["heading"]
    section.get("level", 2)

    # Build navigation (wikilinks without .md)
    nav_parts = []
    if prev_slug:
        nav_parts.append(f"[[{prev_slug}|Previous]]")
    nav_parts.append(f"[[{toc_slug}|Table of Contents]]")
    if next_slug:
        nav_parts.append(f"[[{next_slug}|Next]]")
    # Fragment children link back up to their parent index page.
    parent_slug = section.get("parent_slug")
    if parent_slug:
        nav_parts.append(f"[[{parent_slug}|Up]]")
    nav = " | ".join(nav_parts)

    # Build tags
    tags = ["#textbook", "#ingested"]
    if subject:
        tags.append(f"#{slugify(subject)}")

    # Source attribution
    source_line = (
        f"> **Source:** {source_url}" if source_url else "> **Source:** local file"
    )

    # --- Fragment parent: thin index page linking to its children ----------
    if section.get("is_fragment_parent"):
        child_slugs = section.get("fragment_child_slugs", [])
        child_headings = [c["heading"] for c in section.get("fragment_children", [])]
        list_lines = []
        for cslug, chead in zip(child_slugs, child_headings, strict=False):
            list_lines.append(f"- [[{cslug}|{chead}]]")
        list_body = "\n".join(list_lines)
        body = (
            "This section was long enough that it was split into smaller "
            f"linked pages so each stays a clean file-sized unit:\n\n"
            f"{list_body}\n"
        )
        note = (
            f"# {heading}\n\n{source_line}\n> **Part of:** [[{toc_slug}]]\n\n"
            f"{body}\n\n---\n**Navigation:** {nav}\n\n{' '.join(tags)}\n"
        )
        return note

    # --- Normal note or fragment child: the actual content ----------------
    content = truncate_content(section["content"])
    note = (
        f"# {heading}\n\n{source_line}\n> **Part of:** [[{toc_slug}]]\n\n"
        f"{content}\n\n---\n**Navigation:** {nav}\n\n{' '.join(tags)}\n"
    )
    return note


def create_toc_note(
    title, subject, source_url, sections, section_slugs, skey="", max_sections=0
):
    """Create the table of contents note.

    Slugs should NOT include .md extension -- they are wikilink references.
    A hidden ``source_key`` marker is embedded so a later re-ingest of the
    same source can find this TOC and delete orphaned section notes (making
    ingestion idempotent).
    """
    tags = ["#textbook", "#ingested", "#table-of-contents"]
    if subject:
        tags.append(f"#{slugify(subject)}")

    source_line = (
        f"> **Source:** {source_url}" if source_url else "> **Source:** local file"
    )

    # Build TOC entries with indentation based on heading level
    toc_lines = []
    for _i, (section, slug) in enumerate(zip(sections, section_slugs, strict=False)):
        level = section.get("level", 2)
        indent = "  " * (level - 1) if level > 1 else ""
        toc_lines.append(f"{indent}- [[{slug}|{section['heading']}]]")

    marker = ("\n" + _source_key_line(skey)) if skey else ""
    max_sections_marker = (
        ("\n" + _max_sections_line(max_sections)) if max_sections else ""
    )
    toc_body = "\n".join(toc_lines)
    tag_str = " ".join(tags)
    toc_content = (
        f"# {title} - Table of Contents\n\n{source_line}\n"
        f"> **Ingested:** {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}\n"
        f"> **Sections:** {len(sections)}\n\n## Contents\n\n"
        f"{toc_body}\n\n{tag_str}{marker}"
        f"{max_sections_marker}\n"
    )
    return toc_content


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(args):
    """
    Main entry point for the textbook_ingest tool.

    Args:
        source: URL or local file path
        subject: subject tag (optional)
        max_sections: max sections to ingest (default 50)
        title: override title (optional)

    Returns:
        Dict with status, sections_ingested, notes_created, and any errors.
    """
    result = {
        "status": "success",
        "source": args.get("source", ""),
        "subject": args.get("subject", ""),
        "sections_found": 0,
        "sections_ingested": 0,
        "notes_created": [],
        "notes_updated": [],
        "notes_removed": [],
        "reingest": False,
        "toc_note": "",
        "errors": [],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    try:
        source = args.get("source", "")
        if not source:
            return {"status": "error", "error": "No source provided"}

        subject = args.get("subject", "")
        max_sections = min(args.get("max_sections", 50), MAX_SECTIONS)
        override_title = args.get("title", "")

        # Detect source type
        source_type = detect_source_type(source)
        result["source_type"] = source_type

        # Fetch/read content
        title = ""
        sections = []
        temp_pdf_path = None
        source_url = ""

        if source_type == "html_url":
            content_text = fetch_url(source)
            source_url = source
            title, sections = parse_html(content_text, source)

        elif source_type == "text_url":
            content_text = fetch_url(source)
            source_url = source
            title, sections = parse_plain_text(content_text)

        elif source_type == "markdown_url":
            content_text = fetch_url(source)
            source_url = source
            title, sections = parse_markdown(content_text)

        elif source_type == "pdf_url":
            temp_pdf_path = fetch_pdf_url(source)
            source_url = source
            # PyMuPDF font-metadata extraction builds the TOC index in
            # seconds. Equations/figures on individual pages are read
            # later by textbook_read_page (vision model), not here.
            title, sections = parse_pdf(temp_pdf_path)
            result["parser"] = "pdf_text_layer"

        elif source_type in (
            "pdf_file",
            "text_file",
            "markdown_file",
            "html_file",
            "auto_file",
        ):
            file_path = Path(source)
            if not file_path.exists():
                return {"status": "error", "error": f"File not found: {source}"}

            if source_type == "pdf_file":
                # PyMuPDF font-metadata extraction builds the TOC index
                # in seconds. Equations/figures on individual pages are
                # read later by textbook_read_page (vision model).
                title, sections = parse_pdf(str(file_path))
                result["parser"] = "pdf_text_layer"
            elif source_type == "text_file":
                content_text = file_path.read_text(encoding="utf-8", errors="replace")
                title, sections = parse_plain_text(content_text)
            elif source_type == "markdown_file":
                content_text = file_path.read_text(encoding="utf-8", errors="replace")
                title, sections = parse_markdown(content_text)
            elif source_type == "html_file":
                content_text = file_path.read_text(encoding="utf-8", errors="replace")
                title, sections = parse_html(content_text)
            else:
                # auto_file: try HTML first, then markdown, then plain text
                try:
                    content_text = file_path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    title, sections = parse_html(content_text)
                    if not sections:
                        title, sections = parse_markdown(content_text)
                    if not sections:
                        title, sections = parse_plain_text(content_text)
                except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                    content_text = file_path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    title, sections = parse_plain_text(content_text)

        else:
            return {
                "status": "error",
                "error": f"Unknown source type: {source_type}",
            }

        # Clean up temp PDF
        if temp_pdf_path:
            with contextlib.suppress(Exception):
                os.unlink(temp_pdf_path)

        # Apply overrides
        if override_title:
            title = override_title
        if subject and not title:
            title = subject.title()

        # Auto-detect a meaningful subject if the caller passed "unknown" or
        # left it empty. Without this, generically-named PDFs (Full.pdf, etc.)
        # produce useless note filenames like "unknown-section-2.md".
        if not subject or subject.strip().lower() in ("unknown", "none", ""):
            detected = _auto_detect_subject(
                source, source_type, title=title, sections=sections
            )
            if detected and detected != "textbook":
                subject = detected
                result["subject"] = subject
                result["subject_auto_detected"] = True
            elif detected:
                subject = detected
                result["subject"] = subject

        result["title"] = title
        result["sections_found"] = len(sections)

        if not sections:
            result["status"] = "warning"
            result["errors"].append("No sections with sufficient content found")
            return result

        # Limit sections
        if len(sections) > max_sections:
            sections = sections[:max_sections]
            result["errors"].append(
                f"Limited to {max_sections} sections (found {result['sections_found']})"
            )

        # Fragment any oversized sections into linked child pages so every
        # note stays under the MAX_NOTE_LINES file-unit ceiling. A parent
        # that fragments expands in place to [parent_index, child_1, ...];
        # the parent keeps the original slug (TOC links stay valid) and
        # becomes a thin index, the children get parent-derived slugs. This
        # is what keeps the vault a graph of small connected files instead
        # of a pile of monoliths — the file-unit rule that makes per-file
        # embedding drift actually route well.
        pre_fragment_count = len(sections)
        sections = fragment_sections(sections)
        if len(sections) != pre_fragment_count:
            result["fragments_created"] = len(sections) - pre_fragment_count

        # Generate slugs (WITHOUT .md -- these are wikilink references)
        subject_slug = slugify(subject) if subject else slugify(title)
        toc_slug = f"{subject_slug}-toc"

        section_slugs = []
        for _i, s in enumerate(sections):
            slug = f"{subject_slug}-{slugify(s['heading'])}"
            # Ensure uniqueness
            base_slug = slug
            counter = 2
            while slug in section_slugs:
                slug = f"{base_slug}-{counter}"
                counter += 1
            section_slugs.append(slug)

        # Backfill parent index pages with their children's slugs so the
        # parent note body can link to them. Each fragment parent's
        # 'fragment_children' list parallels the children that immediately
        # follow it in the sections list.
        i = 0
        while i < len(sections):
            s = sections[i]
            if s.get("is_fragment_parent") and s.get("fragment_children"):
                child_slugs = []
                for j, _child in enumerate(s["fragment_children"]):
                    # The children immediately follow the parent in the list.
                    ci = i + 1 + j
                    if ci < len(sections):
                        child_slugs.append(section_slugs[ci])
                s["fragment_child_slugs"] = child_slugs
                # Also tag each child with its parent's slug for the "Up" link.
                parent_slug = section_slugs[i]
                for j, _child in enumerate(s["fragment_children"]):
                    ci = i + 1 + j
                    if ci < len(sections):
                        sections[ci]["parent_slug"] = parent_slug
            i += 1

        # Create textbooks directory
        TEXTBOOKS_DIR.mkdir(parents=True, exist_ok=True)

        # --- Idempotency: reconcile against any prior ingest of this source --
        # The TOC note carries a hidden source-key marker. If a prior ingest
        # exists, we read its old section slugs and delete any notes that the
        # new run no longer produces (stale orphans from a smaller max_sections
        # or a changed source). Notes that still exist are overwritten in
        # place (same slug => same filename), so a re-ingest is a clean
        # replacement, never a duplication. If the old TOC's slug differs from
        # the new one (subject/title changed), the old TOC is removed too.
        skey = source_key(source)
        old_toc_path, old_slugs = find_prior_ingest(skey)
        set(old_slugs)
        new_slug_set = set(section_slugs) | {toc_slug}
        stale_slugs = [s for s in old_slugs if s not in new_slug_set]
        if old_toc_path is not None and old_toc_path.name != safe_filename(toc_slug):
            # The old TOC itself is now orphaned (slug changed).
            stale_slugs.append(os.path.splitext(old_toc_path.name)[0])
        removed = remove_stale_notes(stale_slugs)
        result["notes_removed"] = removed
        result["reingest"] = old_toc_path is not None

        # Write section notes (filename = slug + ".md", wikilink = slug).
        # Same slug => same filename => overwrites the old note in place.
        for i, (section, slug) in enumerate(zip(sections, section_slugs, strict=False)):
            prev_slug = section_slugs[i - 1] if i > 0 else None
            next_slug = section_slugs[i + 1] if i + 1 < len(section_slugs) else None

            note_content = create_section_note(
                section,
                subject,
                source_url,
                i,
                len(sections),
                prev_slug,
                next_slug,
                toc_slug,
                title,
            )

            filename = safe_filename(slug)
            note_path = TEXTBOOKS_DIR / filename
            existed = note_path.exists()
            note_path.write_text(note_content, encoding="utf-8")
            rel = str(note_path.relative_to(VAULT_DIR))
            if existed:
                result["notes_updated"].append(rel)
            else:
                result["notes_created"].append(rel)

        # Write TOC note (with the source-key marker so the next ingest can
        # find it and reconcile again).
        toc_content = create_toc_note(
            title,
            subject,
            source_url,
            sections,
            section_slugs,
            skey=skey,
            max_sections=max_sections,
        )
        toc_filename = safe_filename(toc_slug)
        toc_path = TEXTBOOKS_DIR / toc_filename
        toc_path.write_text(toc_content, encoding="utf-8")
        result["toc_note"] = str(toc_path.relative_to(VAULT_DIR))
        result["sections_ingested"] = len(sections)

    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        result["status"] = "error"
        result["errors"].append(str(e))
        import traceback

        result["errors"].append(traceback.format_exc())

    return result
