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
        "Download or read a textbook/reference resource and ingest it into the vault as "
        "linked notes. Accepts URLs (HTTP/HTTPS) or local file paths. Supports HTML, PDF, "
        "plain text, and Markdown. Each section becomes a linked vault note with navigation. "
        "This is how VaultBot learns systematically -- ingesting a textbook is literally adding "
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
                "description": "Subject tag for the notes (e.g., 'physics', 'biology', 'thermodynamics'). Used in filenames and tags.",
                "default": "",
            },
            "max_sections": {
                "type": "integer",
                "description": "Maximum number of sections to ingest. Default 50. Use a smaller number for testing.",
                "default": 50,
            },
            "title": {
                "type": "string",
                "description": "Override the auto-detected title for the table of contents note.",
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

import hashlib
import os
import re
import time
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Path setup -- resolve from this file's location, never assume CWD
# ---------------------------------------------------------------------------
try:
    BACKEND_DIR = Path(__file__).resolve().parent.parent
except NameError:
    BACKEND_DIR = Path(".").resolve()

VAULT_DIR = BACKEND_DIR.parent
TEXTBOOKS_DIR = VAULT_DIR / "09-Textbooks"

# Limits
MAX_SECTIONS = 1000
# Full chapters are ingested verbatim — no content truncation at the old
# 10K cap (which was cutting off ~90% of a chapter and losing both info and
# wikilink opportunities).  The 200K safety cap only catches pathological
# mega-sections (e.g. a PDF parsing error that merges a whole book into one
# section); real sections run 2K–80K chars.  Retrieval quality is preserved
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
    text = re.sub(r'\[edit\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'[^a-zA-Z0-9\s-]', '', text)
    text = re.sub(r'\s+', '-', text.strip())
    text = re.sub(r'-+', '-', text)
    text = text.strip('-').lower()
    if len(text) > max_len:
        text = text[:max_len].rsplit('-', 1)[0]
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
    return content[:max_chars] + "\n\n*[... content truncated (section exceeded 200K char safety cap) ...]*"

def is_toc_entry(content):
    """Check if a section's content is just a table of contents entry."""
    stripped = content.strip()
    lines = [l for l in stripped.split('\n') if l.strip()]

    # TOC entries are typically 1-3 lines
    if len(lines) > 3:
        return False

    # Check for dot-leader pattern: dots separated by spaces, 5+ occurrences
    if re.search(r'(\.\s){5,}', stripped):
        return True

    # Also check: 5+ consecutive dots (no spaces)
    if re.search(r'\.{5,}', stripped):
        return True

    return False




def safe_filename(slug):
    """Ensure a slug is safe and ends with .md."""
    # Remove any path components
    slug = os.path.basename(slug)
    # Remove dangerous characters
    slug = re.sub(r'[<>:"/\\|?*]', '', slug)
    # Ensure it ends with .md
    if not slug.endswith('.md'):
        slug = slug + '.md'
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
    return "<!-- vaultbot:textbook-source-key %s -->" % key

def _max_sections_line(max_sections):
    """Hidden marker recording the cap used for this ingest.

    Lets the /ingest_learning_material button detect a prior ingest that
    was capped below the ingester's own ceiling and top it up by re-ingesting
    with a higher cap (the ingester's stale-note replacement makes this a
    clean append, not a duplication).
    """
    return "<!-- vaultbot:textbook-max-sections %d -->" % int(max_sections or 0)

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
    if source.startswith(('http://', 'https://')):
        if source_lower.endswith('.pdf'):
            return 'pdf_url'
        elif source_lower.endswith(('.txt', '.text')):
            return 'text_url'
        elif source_lower.endswith(('.md', '.markdown')):
            return 'markdown_url'
        else:
            return 'html_url'
    else:
        # Local file
        if source_lower.endswith('.pdf'):
            return 'pdf_file'
        elif source_lower.endswith(('.txt', '.text')):
            return 'text_file'
        elif source_lower.endswith(('.md', '.markdown')):
            return 'markdown_file'
        elif source_lower.endswith(('.html', '.htm')):
            return 'html_file'
        else:
            return 'auto_file'


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
    if title and title not in ("Untitled", "PDF Document", "Markdown Document",
                                "Plain Text Document"):
        clean = re.sub(r'[_\-]+', ' ', title).strip()
        if clean and len(clean) > 2:
            return slugify(clean)[:60]
    # 2. First section heading
    if sections:
        first_heading = sections[0].get('heading', '').strip()
        if first_heading and first_heading.lower() not in ('untitled', 'section 1'):
            return slugify(first_heading)[:60]
    # 3. Filename stem
    try:
        from pathlib import Path as _P
        stem = _P(source).stem
        # Strip common suffixes like "_-__WEB", "_-_WEB_oNlbGYl".
        stem = re.sub(r'_-__WEB.*$', '', stem, flags=re.IGNORECASE)
        stem = re.sub(r'_-_WEB.*$', '', stem, flags=re.IGNORECASE)
        stem = re.sub(r'[_\-]+', ' ', stem).strip()
        if stem and len(stem) > 2:
            return slugify(stem)[:60]
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        pass
    return "textbook"


def fetch_url(url, timeout=30):
    """Fetch content from a URL with size limit."""
    r = requests.get(url, timeout=timeout, headers={
        "User-Agent": "Mozilla/5.0 (VaultBot/1.0; textbook ingest tool)"
    }, stream=True)
    r.raise_for_status()

    # Download with size limit
    content = b""
    for chunk in r.iter_content(chunk_size=8192):
        content += chunk
        if len(content) > MAX_DOWNLOAD_BYTES:
            raise ValueError("Download exceeds %d MB limit" % (MAX_DOWNLOAD_BYTES // (1024*1024)))

    # Detect encoding
    encoding = r.encoding or 'utf-8'
    return content.decode(encoding, errors='replace')


def fetch_pdf_url(url, timeout=60):
    """Download a PDF from URL to a temp file."""
    import tempfile
    r = requests.get(url, timeout=timeout, headers={
        "User-Agent": "Mozilla/5.0 (VaultBot/1.0)"
    }, stream=True)
    r.raise_for_status()

    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
        total = 0
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)
            total += len(chunk)
            if total > MAX_DOWNLOAD_BYTES:
                f.close()
                os.unlink(f.name)
                raise ValueError("PDF download exceeds %d MB limit" % (MAX_DOWNLOAD_BYTES // (1024*1024)))
        return f.name


# ---------------------------------------------------------------------------
# Parsers -- each returns (title, sections) where sections is a list of dicts
# with keys: heading, level, content
# ---------------------------------------------------------------------------

def parse_html(content_text, base_url=""):
    """Parse HTML into sections by headings."""
    import html2text
    from bs4 import BeautifulSoup, Tag

    soup = BeautifulSoup(content_text, 'lxml')

    # Try to find main content area (try multiple strategies)
    main = (
        soup.find('main') or
        soup.find('article') or
        soup.find('div', {'class': 'mw-parser-output'}) or
        soup.find('div', {'id': 'content'}) or
        soup.find('div', {'class': 'page-content'}) or
        soup
    )

    h2t = html2text.HTML2Text()
    h2t.body_width = 0
    h2t.ignore_images = True
    h2t.ignore_links = False
    h2t.unicode_snob = True

    # Collect headings and content elements in document order
    elements = []
    for elem in main.descendants:
        if not isinstance(elem, Tag):
            continue

        # Skip elements inside unwanted containers
        in_unwanted = False
        p = elem.parent
        while p:
            if p.get('class'):
                classes = set(p.get('class', []))
                if any(x in classes for x in [
                    'infobox', 'sidebar', 'navbox', 'vertical-navbox',
                    'reflist', 'references', 'mw-editsection', 'toc',
                    'hatnote', 'ambox', 'mw-jump-link', 'printfooter',
                    'catlinks', 'mw-navigation'
                ]):
                    in_unwanted = True
                    break
            if p.name == 'table':
                in_unwanted = True
                break
            p = p.parent
        if in_unwanted:
            continue

        if elem.name in ('h1', 'h2', 'h3', 'h4'):
            text = elem.get_text(strip=True).replace('[edit]', '').strip()
            if text and len(text) > 1:
                elements.append(('heading', text, int(elem.name[1]), elem))
        elif elem.name in ('p', 'ul', 'ol', 'blockquote'):
            elements.append(('content', '', 0, elem))

    # Build sections
    sections = []
    current_heading = None
    current_level = 0
    current_parts = []

    for etype, text, level, tag in elements:
        if etype == 'heading':
            if current_heading is not None:
                sections.append({
                    'heading': current_heading,
                    'level': current_level,
                    'content': '\n'.join(current_parts)
                })
            current_heading = text
            current_level = level
            current_parts = []
        elif etype == 'content':
            md = h2t.handle(str(tag))
            md = re.sub(r'\[edit\]\([^)]*\)', '', md)
            md = re.sub(r'\[Edit section[^]]*\]\([^)]*\)', '', md)
            if md.strip():
                current_parts.append(md.strip())

    if current_heading is not None:
        sections.append({
            'heading': current_heading,
            'level': current_level,
            'content': '\n'.join(current_parts)
        })

    # Filter: only keep sections with real content
    sections = [s for s in sections if len(s['content']) > 50]
    sections = [s for s in sections if not is_toc_entry(s['content'])]

    # Extract title
    title_tag = soup.find('title')
    title = title_tag.get_text(strip=True) if title_tag else "Untitled"
    title = re.sub(r'\s*-\s*Wikipedia\s*$', '', title)
    title = re.sub(r'\s*\|\s*.*$', '', title)

    return title, sections


def parse_markdown(content_text):
    """Parse Markdown into sections by # headings."""
    lines = content_text.split('\n')
    sections = []
    current_heading = "Introduction"
    current_level = 1
    current_lines = []

    for line in lines:
        m = re.match(r'^(#{1,4})\s+(.+)$', line)
        if m:
            if current_lines:
                sections.append({
                    'heading': current_heading,
                    'level': len(m.group(1)),
                    'content': '\n'.join(current_lines).strip()
                })
            current_heading = m.group(2).strip()
            current_level = len(m.group(1))
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections.append({
            'heading': current_heading,
            'level': current_level,
            'content': '\n'.join(current_lines).strip()
        })

    sections = [s for s in sections if len(s['content']) > 50]
    sections = [s for s in sections if not is_toc_entry(s['content'])]
    return "Markdown Document", sections


# ---------------------------------------------------------------------------
# Structural-heading detection (shared by PDF and plain-text parsers)
# ---------------------------------------------------------------------------
#
# The old PDF parser matched only the *prefix* of a heading line (e.g. it
# captured "1.4 I" from "1.4 Inverse Functions") and treated any line
# starting with "<number>. <Capital>" as a section -- which promoted
# numbered exercises ("1. Consider the graph...") into their own notes.
# These helpers fix both problems in a source-agnostic way.

_HEADING_PATTERN = re.compile(
    r'^('
    r'CHAPTER\s+[IVXLCDM\d]+'              # CHAPTER IV / CHAPTER 4
    r'|Chapter\s+\d+'                      # Chapter 1
    r'|Section\s+\d+(?:\.\d+)*'           # Section 1.2
    r'|Part\s+[IVXLCDM\d]+'               # Part I / Part 2
    r'|\d+\.\s+\S'                       # 1. Title (period glued to number)
    r'|\d+\.\d+(?:\.\d+)*\s+\S'         # 1.1 Title / 1.1.1 Title
    r')',
    re.MULTILINE
)


def _full_line(text, pos):
    """Return the whole line of `text` starting at `pos`, without the newline."""
    end = text.find('\n', pos)
    if end == -1:
        end = len(text)
    line = text[pos:end]
    if line.endswith('\r'):
        line = line[:-1]
    return line.strip()


def _looks_like_heading(text, start, line, require_blank=True):
    """Heuristic separating real section/chapter TITLES from numbered
    exercises, list items, or run-on sentences.

    Signals (all source-agnostic):
      * short (<= 90 chars) -- titles are concise
      * does not end in sentence punctuation (. ? !)
      * after stripping the numbering prefix, the remainder is short
        (<= 75 chars) and <= 12 words
      * optionally, the line is preceded by a blank line / page break /
        start of document (the reliable textbook-layout signal that a real
        heading stands alone, while exercises continue a list)
    """
    line = line.strip()
    if not line:
        return False
    if len(line) > 90:
        return False
    if re.search(r'[.?!]\s*$', line):
        return False
    body = re.sub(
        r'^(\d+(?:\.\d+)*\s*|Chapter\s+\d+\s*|Section\s+\d+(?:\.\d+)*\s*|Part\s+[IVXLCDM\d]+\s*)',
        '', line, flags=re.IGNORECASE).strip()
    if not body or len(body) > 75:
        return False
    if len(body.split()) > 12:
        return False
    # Body must start with an UPPERCASE letter -- rejects decimals ('0.01
    # that contains a'), lowercase fragments ('1.1  and'), page bleeds,
    # and symbol-led lines.  Real section/chapter titles in English-language
    # textbooks always start with a capital letter.
    if not re.match(r'[A-Z]', body):
        return False
    # Reject sentence fragments -- real titles never end with a function
    # word like 'is', 'the', 'of', 'a'.  Filters answer-key references
    # ('4.13 The absolute maximum is') and wrapped sentences.
    _TRAILING_FN_WORDS = frozenset(
        ['is', 'are', 'was', 'were', 'the', 'a', 'an', 'of', 'in', 'for', 'to', 'with', 'by', 'at', 'from', 'and', 'or', 'but', 'be', 'this', 'that', 'these', 'those', 'as', 'into', 'on', 'upon', 'over', 'under', 'than', 'within', 'without', 'about', 'above', 'below', 'behind', 'between', 'during', 'through', 'throughout', 'across', 'against', 'around', 'beyond', 'despite', 'except', 'inside', 'near', 'outside', 'toward', 'towards', 'until', 'up', 'upon', 'down', 'off', 'per', 'via', 'using'])
    words = body.split()
    if words[-1].lower().rstrip('.,;:') in _TRAILING_FN_WORDS:
        return False
    # Reject headings containing math symbols or answer-key artifacts.
    # Real section titles are prose noun phrases; they never contain '=',
    # '∞', '≈', '±', or semicolons (which separate answer-key list items
    # like '6.17 Use the method of washers;').  Source-agnostic: these
    # characters are universal math/answer markers, not book-specific.
    if re.search(r'[=∞≈±∓≤≥≠∈∉√∑∏∫;]', body):
        return False
    # Real section titles are at least 2 characters.  A single short word
    # can be a legitimate title ('4.10 Antiderivatives') so we don't
    # reject single-word titles outright -- the other content filters
    # (uppercase, no math symbols, no trailing function words, no sentence
    # markers) handle answer-key labels like '1.13 Algebraic' when they
    # are adjectives, and the 200-char content threshold filters tiny
    # answer fragments.
    if len(body) < 2:
        return False
    # Reject headings starting with answer-key quantifiers/adverbs.  These
    # appear in answer appendices ('6.28 Approximately 7,164,520,000 lb')
    # and are never section titles.  Source-agnostic: these are universal
    # numeric-result lead-ins.
    _ANSWER_LEADINS = frozenset(
        ['approximately', 'about', 'around', 'roughly', 'nearly', 'exactly', 'since', 'because', 'therefore', 'thus', 'hence', 'consequently', 'yes', 'no', 'true', 'false'])
    if words[0].lower().rstrip('.,;:') in _ANSWER_LEADINS:
        return False
    # Reject sentence fragments.  Real section titles are noun phrases
    # (Title Case or proper nouns), not clauses.  They never contain
    # lowercase auxiliary verbs or sentence connectives like 'will be',
    # 'there will', 'let ... assume', 'we can', 'it is'.  We check for
    # these universal sentence-fragment markers anywhere in the body.
    _SENTENCE_MARKERS = re.compile(
        r'\b(there\s+will|there\s+is|there\s+are|let\s+us|let\s+choose|'
        r'we\s+can|we\s+have|we\s+need|we\s+use|we\s+want|it\s+is|it\s+was|'
        r'this\s+is|they\s+are|she\s+must|he\s+must|one\s+must|you\s+must|'
        r'will\s+be|has\s+been|have\s+been|can\s+be|must\s+be|'
        r'after\s+years|after\s+months|after\s+days|at\s+interest|'
        r'if\s+we|if\s+you|if\s+there)\b',
        re.IGNORECASE)
    if _SENTENCE_MARKERS.search(body):
        return False
    if require_blank and start > 0:
        if not _blank_before(text, start) and not _blank_after(text, start, line):
            return False  # embedded in a paragraph -> not a heading
    return True


def _blank_before(text, start):
    """True if the line before `start` is blank (or start of document)."""
    if start == 0:
        return True
    p = start - 1
    if p >= 0 and text[p] == '\n':
        p -= 1
    if p >= 0 and text[p] == '\r':
        p -= 1
    while p >= 0 and text[p] in ' \t':
        p -= 1
    return p < 0 or text[p] in '\n\r\f'


def _blank_after(text, start, line):
    """True if the line after the heading line is blank (or end of doc)."""
    nl = text.find('\n', start)
    if nl == -1:
        return True  # end of document
    p = nl + 1
    while p < len(text) and text[p] in ' \t':
        p += 1
    return p >= len(text) or text[p] in '\n\r\f'


def _assign_level(line):
    """Heading depth from its numbering: '1.' -> 1, '1.1' -> 2, '1.1.1' -> 3.
    'Chapter'/'Part' -> 1, 'Section' -> 2. Default 2."""
    s = line.strip()
    m = re.match(r'^(\d+(?:\.\d+)*)', s)
    if m:
        return m.group(1).count('.') + 1
    if re.match(r'^(Chapter|Part)\b', s, re.IGNORECASE):
        return 1
    if re.match(r'^Section\b', s, re.IGNORECASE):
        return 2
    return 2


def _section_sort_key(heading):
    """Natural-order sort key: '1.2.3' -> (1,2,3); 'Chapter 2' -> (2,);
    'Section 1.1 Exercises' -> (1,1,0) so it sorts right after 1.1;
    non-numeric -> (inf,) so unknown headings sort after numbered ones."""
    s = heading.strip()
    m = re.match(r'^(\d+(?:\.\d+)*)', s)
    if not m:
        m = re.match(r'^(?:Chapter|Section|Part)\s+(\d+(?:\.\d+)*)', s, re.IGNORECASE)
    if m:
        parts = tuple(int(x) for x in m.group(1).split('.'))
        # "Section 1.1 Exercises" sorts right after "1.1" by appending a 0.
        if re.match(r'^(?:Chapter|Section|Part)\b', s, re.IGNORECASE):
            return parts + (0,)
        return parts
    return (float('inf'),)


def _clean_heading(line):
    """Strip trailing page numbers and artifact characters from a heading.

    PDF-extracted TOC lines often append a page number ('4.10 Antiderivatives
    419').  We strip a trailing run of digits (with optional preceding
    whitespace) if what remains is still meaningful (has at least one word
    beyond the section number).  Also collapses repeated whitespace.
    """
    line = re.sub(r'\s+', ' ', line.strip())
    # Strip trailing page number: '... Title 419' -> '... Title'
    # Only if there's title text before the number (don't strip '4.10').
    m = re.match(r'^(\d+(?:\.\d+)*\s+\S.*?)\s+\d+$', line)
    if m:
        line = m.group(1)
    return line


def _is_learning_objective(heading):
    """True if a heading is a learning objective, not a section title.

    Learning objectives in textbooks worldwide share a universal pattern:
    imperative sentences instructing the student ('Calculate the slope',
    'Find the derivative', 'Explain the difference').  Real section titles
    are noun phrases or gerunds ('Defining the Derivative', 'Related
    Rates').  We check if the body starts with a common educational
    imperative verb.  This list is drawn from Bloom's taxonomy and standard
    textbook learning-objective conventions -- not specific to any one book.
    """
    body = re.sub(
        r'^(\d+(?:\.\d+)*\s*|Chapter\s+\d+\s*|Section\s+\d+(?:\.\d+)*\s*|Part\s+[IVXLCDM\d]+\s*)',
        '', heading, flags=re.IGNORECASE).strip()
    first_word = body.split()[0].lower().rstrip('.,;:') if body else ''
    _IMPERATIVES = frozenset(
        ['calculate', 'find', 'express', 'use', 'state', 'explain', 'recognize', 'describe', 'determine', 'identify', 'sketch', 'evaluate', 'compute', 'apply', 'verify', 'simplify', 'solve', 'graph', 'compare', 'classify', 'construct', 'derive', 'formulate', 'illustrate', 'interpret', 'list', 'name', 'prove', 'show', 'translate', 'write', 'demonstrate', 'discuss', 'explore', 'predict', 'analyze', 'assess', 'define', 'estimate', 'approximate', 'convert', 'factor', 'integrate', 'differentiate', 'plot', 'draw', 'label', 'measure', 'test', 'check', 'compute'])
    return first_word in _IMPERATIVES


def _detect_headings(text):
    """Find structural heading lines in extracted text.

    Returns a list of (start_pos, heading_line) in document order.

    Two-tier strategy that balances coverage against exercise filtering:

    * Sub-numbered headings (1.1, 1.2, 2.3.1) are accepted if they are
      isolated (blank line before OR after).  They have very low false-
      positive risk because exercises use bare integers (1., 2., 3.),
      not dotted sub-numbers.  PDF text extraction often collapses blank
      lines, so we accept blank on either side rather than requiring both.

    * Bare-integer headings (1., 2., Chapter 1) are ambiguous: they
      can be chapter titles ("1. Functions and Graphs") or exercise
      items ("1. Consider the graph...").  For these we require a blank
      line before (the strongest layout signal), and then drop any run
      of 3+ consecutive integers (exercise lists).
    """
    def is_subnumbered(h):
        return bool(re.match(r'^\d+\.\d', h.strip()))

    found = []
    for m in _HEADING_PATTERN.finditer(text):
        line = _full_line(text, m.start())
        if not line:
            continue
        if not _looks_like_heading(text, m.start(), line, require_blank=False):
            continue
        if is_subnumbered(line) or re.match(r'^(Chapter|Section|Part)\b',
                                            line, re.IGNORECASE):
            # Sub-numbered or named structural headings: accept based on
            # content heuristics alone (uppercase, title-like).  PDF text
            # extraction collapses blank lines, so layout signals are
            # unreliable -- content filters do the heavy lifting instead.
            found.append((m.start(), _clean_heading(line)))
        else:
            # Bare integer heading: require blank line before to distinguish
            # chapter titles from exercise-list items.
            if _blank_before(text, m.start()):
                found.append((m.start(), _clean_heading(line)))

    # Reject lines containing PDF running-header artifacts (bullets, page
    # markers).  These appear in repeated page headers like "4.10 •
    # Antiderivatives     419" and are never real section titles.
    found = [(s, l) for s, l in found if '•' not in l]

    # Drop learning objectives (level-3+ imperative sentences like
    # "1.2.1 Calculate the slope of a line").  These are sub-items within a
    # section, not sections themselves.  Only apply to deep sub-numberings
    # (3+ dotted parts) to avoid accidentally dropping real level-2 sections
    # that happen to start with an imperative ('Calculate ...' as a section
    # title is rare but possible -- we only filter at depth 3+).
    found = [(s, l) for s, l in found
             if not (re.match(r'^\d+\.\d+\.\d', l.strip())
                     and _is_learning_objective(l))]

    # Deduplicate by position.
    dedup, last = [], -1
    for start, line in found:
        if start == last:
            continue
        dedup.append((start, line))
        last = start
    found = dedup

    # Drop exercise-list clusters: runs of consecutive integers (N, N+1,
    # N+2, ...) where every heading is a bare integer.  Real chapters are
    # sparse; exercise lists are dense.
    def _bare_int(h):
        m = re.match(r'^(\d+)\.', h.strip())
        if not m or re.match(r'^\d+\.\d', h.strip()):
            return None
        return int(m.group(1))

    drop = set()
    i = 0
    while i < len(found):
        nums = []
        j = i
        while j < len(found):
            n = _bare_int(found[j][1])
            if n is None:
                break
            if nums and n != nums[-1] + 1:
                break
            nums.append(n)
            j += 1
        if len(nums) >= 3:
            for k in range(i, j):
                drop.add(found[k][0])
        i = max(j, i + 1)
    found = [(s, l) for s, l in found if s not in drop]
    return found


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
                p2, t2, s2, b2, y2 = spans[j]
                if s2 > body_size and b2 and p2 == page_num:
                    parts.append(t2)
                    j += 1
                else:
                    break
            heading_text = " ".join(parts).strip()
            if len(heading_text) >= 3 and not re.match(r'^\d+\s+\d+$', heading_text):
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
        h for h in headings
        if not (h[1] == h[1].upper() and len(h[1]) > 5)
        and not re.match(r'^.+\s+\d+$', h[1])  # "Limits 105" etc.
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
        end = heading_positions[i + 1][0] if i + 1 < len(heading_positions) else len(flat_text)
        body = flat_text[pos:end].strip()
        if len(body) > 200:
            sections.append({
                "heading": heading_text,
                "level": size_to_level.get(size, 2),
                "content": body,
            })

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
            sections.append({
                "heading": line.strip(),
                "level": _assign_level(line),
                "content": body,
            })
    else:
        pages = re.split(r"\f", full_text)
        current_text = ""
        section_num = 1
        for page_text in pages:
            page_text = page_text.strip()
            if not page_text:
                continue
            if len(current_text) + len(page_text) > 3000 and current_text:
                sections.append({
                    "heading": "Section %d" % section_num,
                    "level": 2,
                    "content": current_text,
                })
                section_num += 1
                current_text = page_text
            else:
                current_text += "\n\n" + page_text if current_text else page_text
        if current_text.strip():
            sections.append({
                "heading": "Section %d" % section_num,
                "level": 2,
                "content": current_text,
            })

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


def parse_plain_text(content_text):
    """Parse plain text into sections."""
    # Detect structural headings with the same source-agnostic logic as the
    # PDF parser: full-line capture + exercise filtering + natural sort.
    candidates = _detect_headings(content_text)

    sections = []
    if len(candidates) >= 3:
        for i, (start, line) in enumerate(candidates):
            end = candidates[i + 1][0] if i + 1 < len(candidates) else len(content_text)
            nl = content_text.find('\n', start)
            if nl == -1 or nl > end:
                nl = end
            body = content_text[nl:end].strip()
            sections.append({
                'heading': line.strip(),
                'level': _assign_level(line),
                'content': body,
            })
    else:
        # Split by double newlines, group into ~3000 char sections
        chunks = re.split(r'\n\s*\n', content_text)
        current_text = ""
        section_num = 1
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            if len(current_text) + len(chunk) > 3000 and current_text:
                sections.append({
                    'heading': "Section %d" % section_num,
                    'level': 2,
                    'content': current_text
                })
                section_num += 1
                current_text = chunk
            else:
                current_text += "\n\n" + chunk if current_text else chunk
        if current_text.strip():
            sections.append({
                'heading': "Section %d" % section_num,
                'level': 2,
                'content': current_text
            })

    sections = [s for s in sections if len(s['content']) > 200]
    sections = [s for s in sections if not is_toc_entry(s['content'])]
    # Natural-order sort (1, 1.1, 1.2, 2, ...) so navigation follows the book.
    sections = sorted(sections, key=lambda s: _section_sort_key(s['heading']))
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
    lines = content.split('\n')
    md_heading = re.compile(r'^#{1,4}\s+\S')
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
        body = '\n'.join(lines[start:end]).strip()
        if not body:
            continue
        # Derive a sub-heading: first line if it's a heading, else "Introduction".
        first_line = body.split('\n', 1)[0]
        if md_heading.match(first_line):
            sub_h = re.sub(r'^#{1,4}\s+', '', first_line).strip()
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
    if re.search(r'\n\s*\n', content):
        paragraphs = re.split(r'\n\s*\n', content)
        joiner = '\n\n'
    else:
        paragraphs = content.split('\n')
        joiner = '\n'
    chunks = []
    current = []
    current_lines = 0
    part = 1
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        plines = para.count('\n') + 1
        if current and current_lines + plines > target_lines:
            chunks.append(("Part %d" % part, joiner.join(current)))
            part += 1
            current = [para]
            current_lines = plines
        else:
            current.append(para)
            current_lines += plines
    if current:
        chunks.append(("Part %d" % part, joiner.join(current)))
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
    content = section.get('content', '')
    line_count = content.count('\n') + 1
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
        'heading': section['heading'],
        'level': section.get('level', 2),
        'content': '',  # replaced at write time with the child link list
        'is_fragment_parent': True,
        'fragment_children': [],  # filled in after slug generation
    }
    children = []
    for sub_h, body in pieces:
        child = {
            'heading': "%s — %s" % (section['heading'], sub_h),
            'level': (section.get('level', 2) + 1),
            'content': body,
            'is_fragment_child': True,
        }
        children.append(child)
    parent['fragment_children'] = children
    return [parent] + children


def fragment_sections(sections):
    """Apply fragment_section across the section list, preserving order.

    A parent that fragments expands in place to [parent, child_1, ...], so
    the TOC and navigation still flow in reading order.
    """
    out = []
    for s in sections:
        out.extend(fragment_section(s))
    return out


# ---------------------------------------------------------------------------
# Note writing
# ---------------------------------------------------------------------------

def create_section_note(section, subject, source_url, index, total,
                         prev_slug, next_slug, toc_slug, base_title):
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
    heading = section['heading']
    section.get('level', 2)

    # Build navigation (wikilinks without .md)
    nav_parts = []
    if prev_slug:
        nav_parts.append("[[%s|Previous]]" % prev_slug)
    nav_parts.append("[[%s|Table of Contents]]" % toc_slug)
    if next_slug:
        nav_parts.append("[[%s|Next]]" % next_slug)
    # Fragment children link back up to their parent index page.
    parent_slug = section.get('parent_slug')
    if parent_slug:
        nav_parts.append("[[%s|Up]]" % parent_slug)
    nav = " | ".join(nav_parts)

    # Build tags
    tags = ["#textbook", "#ingested"]
    if subject:
        tags.append("#%s" % slugify(subject))

    # Source attribution
    source_line = "> **Source:** %s" % source_url if source_url else "> **Source:** local file"

    # --- Fragment parent: thin index page linking to its children ----------
    if section.get('is_fragment_parent'):
        child_slugs = section.get('fragment_child_slugs', [])
        child_headings = [c['heading'] for c in section.get('fragment_children', [])]
        list_lines = []
        for cslug, chead in zip(child_slugs, child_headings):
            list_lines.append("- [[%s|%s]]" % (cslug, chead))
        body = ("This section was long enough that it was split into smaller "
                "linked pages so each stays a clean file-sized unit:\n\n%s\n"
                % '\n'.join(list_lines))
        note = "# %s\n\n%s\n> **Part of:** [[%s]]\n\n%s\n\n---\n**Navigation:** %s\n\n%s\n" % (
            heading, source_line, toc_slug, body, nav, ' '.join(tags))
        return note

    # --- Normal note or fragment child: the actual content ----------------
    content = truncate_content(section['content'])
    note = "# %s\n\n%s\n> **Part of:** [[%s]]\n\n%s\n\n---\n**Navigation:** %s\n\n%s\n" % (
        heading,
        source_line,
        toc_slug,
        content,
        nav,
        ' '.join(tags)
    )
    return note


def create_toc_note(title, subject, source_url, sections, section_slugs,
                    skey="", max_sections=0):
    """Create the table of contents note.
    
    Slugs should NOT include .md extension -- they are wikilink references.
    A hidden ``source_key`` marker is embedded so a later re-ingest of the
    same source can find this TOC and delete orphaned section notes (making
    ingestion idempotent).
    """
    tags = ["#textbook", "#ingested", "#table-of-contents"]
    if subject:
        tags.append("#%s" % slugify(subject))

    source_line = "> **Source:** %s" % source_url if source_url else "> **Source:** local file"

    # Build TOC entries with indentation based on heading level
    toc_lines = []
    for i, (section, slug) in enumerate(zip(sections, section_slugs)):
        level = section.get('level', 2)
        indent = "  " * (level - 1) if level > 1 else ""
        toc_lines.append("%s- [[%s|%s]]" % (indent, slug, section['heading']))

    marker = ("\n" + _source_key_line(skey)) if skey else ""
    max_sections_marker = ("\n" + _max_sections_line(max_sections)) if max_sections else ""
    toc_content = "# %s - Table of Contents\n\n%s\n> **Ingested:** %s\n> **Sections:** %d\n\n## Contents\n\n%s\n\n%s%s%s\n" % (
        title,
        source_line,
        time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime()),
        len(sections),
        '\n'.join(toc_lines),
        ' '.join(tags),
        marker,
        max_sections_marker,
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

        if source_type == 'html_url':
            content_text = fetch_url(source)
            source_url = source
            title, sections = parse_html(content_text, source)

        elif source_type == 'text_url':
            content_text = fetch_url(source)
            source_url = source
            title, sections = parse_plain_text(content_text)

        elif source_type == 'markdown_url':
            content_text = fetch_url(source)
            source_url = source
            title, sections = parse_markdown(content_text)

        elif source_type == 'pdf_url':
            temp_pdf_path = fetch_pdf_url(source)
            source_url = source
            # PyMuPDF font-metadata extraction builds the TOC index in
            # seconds. Equations/figures on individual pages are read
            # later by textbook_read_page (vision model), not here.
            title, sections = parse_pdf(temp_pdf_path)
            result["parser"] = "pdf_text_layer"

        elif source_type in ('pdf_file', 'text_file', 'markdown_file', 'html_file', 'auto_file'):
            file_path = Path(source)
            if not file_path.exists():
                return {"status": "error", "error": "File not found: %s" % source}

            if source_type == 'pdf_file':
                # PyMuPDF font-metadata extraction builds the TOC index
                # in seconds. Equations/figures on individual pages are
                # read later by textbook_read_page (vision model).
                title, sections = parse_pdf(str(file_path))
                result["parser"] = "pdf_text_layer"
            elif source_type == 'text_file':
                content_text = file_path.read_text(encoding='utf-8', errors='replace')
                title, sections = parse_plain_text(content_text)
            elif source_type == 'markdown_file':
                content_text = file_path.read_text(encoding='utf-8', errors='replace')
                title, sections = parse_markdown(content_text)
            elif source_type == 'html_file':
                content_text = file_path.read_text(encoding='utf-8', errors='replace')
                title, sections = parse_html(content_text)
            else:
                # auto_file: try HTML first, then markdown, then plain text
                try:
                    content_text = file_path.read_text(encoding='utf-8', errors='replace')
                    title, sections = parse_html(content_text)
                    if not sections:
                        title, sections = parse_markdown(content_text)
                    if not sections:
                        title, sections = parse_plain_text(content_text)
                except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                    content_text = file_path.read_text(encoding='utf-8', errors='replace')
                    title, sections = parse_plain_text(content_text)

        else:
            return {"status": "error", "error": "Unknown source type: %s" % source_type}

        # Clean up temp PDF
        if temp_pdf_path:
            try:
                os.unlink(temp_pdf_path)
            except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                pass

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
                source, source_type, title=title, sections=sections)
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
            result["errors"].append("Limited to %d sections (found %d)" % (max_sections, result["sections_found"]))

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
        toc_slug = "%s-toc" % subject_slug

        section_slugs = []
        for i, s in enumerate(sections):
            slug = "%s-%s" % (subject_slug, slugify(s['heading']))
            # Ensure uniqueness
            base_slug = slug
            counter = 2
            while slug in section_slugs:
                slug = "%s-%d" % (base_slug, counter)
                counter += 1
            section_slugs.append(slug)

        # Backfill parent index pages with their children's slugs so the
        # parent note body can link to them. Each fragment parent's
        # 'fragment_children' list parallels the children that immediately
        # follow it in the sections list.
        i = 0
        while i < len(sections):
            s = sections[i]
            if s.get('is_fragment_parent') and s.get('fragment_children'):
                child_slugs = []
                for j, _child in enumerate(s['fragment_children']):
                    # The children immediately follow the parent in the list.
                    ci = i + 1 + j
                    if ci < len(sections):
                        child_slugs.append(section_slugs[ci])
                s['fragment_child_slugs'] = child_slugs
                # Also tag each child with its parent's slug for the "Up" link.
                parent_slug = section_slugs[i]
                for j, _child in enumerate(s['fragment_children']):
                    ci = i + 1 + j
                    if ci < len(sections):
                        sections[ci]['parent_slug'] = parent_slug
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
        for i, (section, slug) in enumerate(zip(sections, section_slugs)):
            prev_slug = section_slugs[i-1] if i > 0 else None
            next_slug = section_slugs[i+1] if i+1 < len(section_slugs) else None

            note_content = create_section_note(
                section, subject, source_url, i, len(sections),
                prev_slug, next_slug, toc_slug, title
            )

            filename = safe_filename(slug)
            note_path = TEXTBOOKS_DIR / filename
            existed = note_path.exists()
            note_path.write_text(note_content, encoding='utf-8')
            rel = str(note_path.relative_to(VAULT_DIR))
            if existed:
                result["notes_updated"].append(rel)
            else:
                result["notes_created"].append(rel)

        # Write TOC note (with the source-key marker so the next ingest can
        # find it and reconcile again).
        toc_content = create_toc_note(
            title, subject, source_url, sections, section_slugs,
            skey=skey, max_sections=max_sections)
        toc_filename = safe_filename(toc_slug)
        toc_path = TEXTBOOKS_DIR / toc_filename
        toc_path.write_text(toc_content, encoding='utf-8')
        result["toc_note"] = str(toc_path.relative_to(VAULT_DIR))
        result["sections_ingested"] = len(sections)

    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        result["status"] = "error"
        result["errors"].append(str(e))
        import traceback
        result["errors"].append(traceback.format_exc())

    return result
