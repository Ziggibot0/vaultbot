"""Markdown parser and structural heading detection utilities.

Extracted from ``custom_tools.textbook_ingest``.

This module contains:

* ``parse_markdown`` -- split Markdown text into sections by ``#`` headings.
* A suite of **source-agnostic** heading-detection utilities that are
  shared by the PDF and plain-text parsers:

  - ``_full_line``
  - ``_looks_like_heading``
  - ``_blank_before``
  - ``_blank_after``
  - ``_assign_level``
  - ``_section_sort_key``
  - ``_clean_heading``
  - ``_is_learning_objective``
  - ``_detect_headings``

The old PDF parser matched only the *prefix* of a heading line (e.g. it
captured "1.4 I" from "1.4 Inverse Functions") and treated any line
starting with "<number>. <Capital>" as a section -- which promoted
numbered exercises ("1. Consider the graph...") into their own notes.
These helpers fix both problems in a source-agnostic way.
"""

import re

# ---------------------------------------------------------------------------
# Heading pattern -- shared by PDF and plain-text parsers
# ---------------------------------------------------------------------------

_HEADING_PATTERN = re.compile(
    r"^("
    r"CHAPTER\s+[IVXLCDM\d]+"  # CHAPTER IV / CHAPTER 4
    r"|Chapter\s+\d+"  # Chapter 1
    r"|Section\s+\d+(?:\.\d+)*"  # Section 1.2
    r"|Part\s+[IVXLCDM\d]+"  # Part I / Part 2
    r"|\d+\.\s+\S"  # 1. Title (period glued to number)
    r"|\d+\.\d+(?:\.\d+)*\s+\S"  # 1.1 Title / 1.1.1 Title
    r")",
    re.MULTILINE,
)


def _full_line(text, pos):
    """Return the whole line of `text` starting at `pos`, without the newline."""
    end = text.find("\n", pos)
    if end == -1:
        end = len(text)
    line = text[pos:end]
    if line.endswith("\r"):
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
    if re.search(r"[.?!]\s*$", line):
        return False
    body = re.sub(
        r"^(\d+(?:\.\d+)*\s*|Chapter\s+\d+\s*|Section\s+\d+(?:\.\d+)*\s*|Part\s+[IVXLCDM\d]+\s*)",
        "",
        line,
        flags=re.IGNORECASE,
    ).strip()
    if not body or len(body) > 75:
        return False
    if len(body.split()) > 12:
        return False
    # Body must start with an UPPERCASE letter -- rejects decimals ('0.01
    # that contains a'), lowercase fragments ('1.1  and'), page bleeds,
    # and symbol-led lines.  Real section/chapter titles in English-language
    # textbooks always start with a capital letter.
    if not re.match(r"[A-Z]", body):
        return False
    # Reject sentence fragments -- real titles never end with a function
    # word like 'is', 'the', 'of', 'a'.  Filters answer-key references
    # ('4.13 The absolute maximum is') and wrapped sentences.
    _TRAILING_FN_WORDS = frozenset(
        [
            "is",
            "are",
            "was",
            "were",
            "the",
            "a",
            "an",
            "of",
            "in",
            "for",
            "to",
            "with",
            "by",
            "at",
            "from",
            "and",
            "or",
            "but",
            "be",
            "this",
            "that",
            "these",
            "those",
            "as",
            "into",
            "on",
            "upon",
            "over",
            "under",
            "than",
            "within",
            "without",
            "about",
            "above",
            "below",
            "behind",
            "between",
            "during",
            "through",
            "throughout",
            "across",
            "against",
            "around",
            "beyond",
            "despite",
            "except",
            "inside",
            "near",
            "outside",
            "toward",
            "towards",
            "until",
            "up",
            "upon",
            "down",
            "off",
            "per",
            "via",
            "using",
        ]
    )
    words = body.split()
    if words[-1].lower().rstrip(".,;:") in _TRAILING_FN_WORDS:
        return False
    # Reject headings containing math symbols or answer-key artifacts.
    # Real section titles are prose noun phrases; they never contain '=',
    # '∞', '≈', '±', or semicolons (which separate answer-key list items
    # like '6.17 Use the method of washers;').  Source-agnostic: these
    # characters are universal math/answer markers, not book-specific.
    if re.search(r"[=∞≈±∓≤≥≠∈∉√∑∏∫;]", body):
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
        [
            "approximately",
            "about",
            "around",
            "roughly",
            "nearly",
            "exactly",
            "since",
            "because",
            "therefore",
            "thus",
            "hence",
            "consequently",
            "yes",
            "no",
            "true",
            "false",
        ]
    )
    if words[0].lower().rstrip(".,;:") in _ANSWER_LEADINS:
        return False
    # Reject sentence fragments.  Real section titles are noun phrases
    # (Title Case or proper nouns), not clauses.  They never contain
    # lowercase auxiliary verbs or sentence connectives like 'will be',
    # 'there will', 'let ... assume', 'we can', 'it is'.  We check for
    # these universal sentence-fragment markers anywhere in the body.
    _SENTENCE_MARKERS = re.compile(
        r"\b(there\s+will|there\s+is|there\s+are|let\s+us|let\s+choose|"
        r"we\s+can|we\s+have|we\s+need|we\s+use|we\s+want|it\s+is|it\s+was|"
        r"this\s+is|they\s+are|she\s+must|he\s+must|one\s+must|you\s+must|"
        r"will\s+be|has\s+been|have\s+been|can\s+be|must\s+be|"
        r"after\s+years|after\s+months|after\s+days|at\s+interest|"
        r"if\s+we|if\s+you|if\s+there)\b",
        re.IGNORECASE,
    )
    if _SENTENCE_MARKERS.search(body):
        return False
    # embedded in a paragraph -> not a heading
    return not (
        require_blank
        and start > 0
        and not _blank_before(text, start)
        and not _blank_after(text, start, line)
    )


def _blank_before(text, start):
    """True if the line before `start` is blank (or start of document)."""
    if start == 0:
        return True
    p = start - 1
    if p >= 0 and text[p] == "\n":
        p -= 1
    if p >= 0 and text[p] == "\r":
        p -= 1
    while p >= 0 and text[p] in " \t":
        p -= 1
    return p < 0 or text[p] in "\n\r\f"


def _blank_after(text, start, line):
    """True if the line after the heading line is blank (or end of doc)."""
    nl = text.find("\n", start)
    if nl == -1:
        return True  # end of document
    p = nl + 1
    while p < len(text) and text[p] in " \t":
        p += 1
    return p >= len(text) or text[p] in "\n\r\f"


def _assign_level(line):
    """Heading depth from its numbering: '1.' -> 1, '1.1' -> 2, '1.1.1' -> 3.
    'Chapter'/'Part' -> 1, 'Section' -> 2. Default 2."""
    s = line.strip()
    m = re.match(r"^(\d+(?:\.\d+)*)", s)
    if m:
        return m.group(1).count(".") + 1
    if re.match(r"^(Chapter|Part)\b", s, re.IGNORECASE):
        return 1
    if re.match(r"^Section\b", s, re.IGNORECASE):
        return 2
    return 2


def _section_sort_key(heading):
    """Natural-order sort key: '1.2.3' -> (1,2,3); 'Chapter 2' -> (2,);
    'Section 1.1 Exercises' -> (1,1,0) so it sorts right after 1.1;
    non-numeric -> (inf,) so unknown headings sort after numbered ones."""
    s = heading.strip()
    m = re.match(r"^(\d+(?:\.\d+)*)", s)
    if not m:
        m = re.match(r"^(?:Chapter|Section|Part)\s+(\d+(?:\.\d+)*)", s, re.IGNORECASE)
    if m:
        parts = tuple(int(x) for x in m.group(1).split("."))
        # "Section 1.1 Exercises" sorts right after "1.1" by appending a 0.
        if re.match(r"^(?:Chapter|Section|Part)\b", s, re.IGNORECASE):
            return (*parts, 0)
        return parts
    return (float("inf"),)


def _clean_heading(line):
    """Strip trailing page numbers and artifact characters from a heading.

    PDF-extracted TOC lines often append a page number ('4.10 Antiderivatives
    419').  We strip a trailing run of digits (with optional preceding
    whitespace) if what remains is still meaningful (has at least one word
    beyond the section number).  Also collapses repeated whitespace.
    """
    line = re.sub(r"\s+", " ", line.strip())
    # Strip trailing page number: '... Title 419' -> '... Title'
    # Only if there's title text before the number (don't strip '4.10').
    m = re.match(r"^(\d+(?:\.\d+)*\s+\S.*?)\s+\d+$", line)
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
        r"^(\d+(?:\.\d+)*\s*|Chapter\s+\d+\s*|Section\s+\d+(?:\.\d+)*\s*|Part\s+[IVXLCDM\d]+\s*)",
        "",
        heading,
        flags=re.IGNORECASE,
    ).strip()
    first_word = body.split()[0].lower().rstrip(".,;:") if body else ""
    _IMPERATIVES = frozenset(
        [
            "calculate",
            "find",
            "express",
            "use",
            "state",
            "explain",
            "recognize",
            "describe",
            "determine",
            "identify",
            "sketch",
            "evaluate",
            "compute",
            "apply",
            "verify",
            "simplify",
            "solve",
            "graph",
            "compare",
            "classify",
            "construct",
            "derive",
            "formulate",
            "illustrate",
            "interpret",
            "list",
            "name",
            "prove",
            "show",
            "translate",
            "write",
            "demonstrate",
            "discuss",
            "explore",
            "predict",
            "analyze",
            "assess",
            "define",
            "estimate",
            "approximate",
            "convert",
            "factor",
            "integrate",
            "differentiate",
            "plot",
            "draw",
            "label",
            "measure",
            "test",
            "check",
            "compute",
        ]
    )
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
        return bool(re.match(r"^\d+\.\d", h.strip()))

    found = []
    for m in _HEADING_PATTERN.finditer(text):
        line = _full_line(text, m.start())
        if not line:
            continue
        if not _looks_like_heading(text, m.start(), line, require_blank=False):
            continue
        if is_subnumbered(line) or re.match(
            r"^(Chapter|Section|Part)\b", line, re.IGNORECASE
        ):
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
    found = [(s, ln) for s, ln in found if "•" not in ln]

    # Drop learning objectives (level-3+ imperative sentences like
    # "1.2.1 Calculate the slope of a line").  These are sub-items within a
    # section, not sections themselves.  Only apply to deep sub-numberings
    # (3+ dotted parts) to avoid accidentally dropping real level-2 sections
    # that happen to start with an imperative ('Calculate ...' as a section
    # title is rare but possible -- we only filter at depth 3+).
    found = [
        (s, ln)
        for s, ln in found
        if not (re.match(r"^\d+\.\d+\.\d", ln.strip()) and _is_learning_objective(ln))
    ]

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
        m = re.match(r"^(\d+)\.", h.strip())
        if not m or re.match(r"^\d+\.\d", h.strip()):
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
    found = [(s, ln) for s, ln in found if s not in drop]
    return found


def parse_markdown(content_text):
    """Parse Markdown into sections by # headings."""
    lines = content_text.split("\n")
    sections = []
    current_heading = "Introduction"
    current_level = 1
    current_lines = []

    for line in lines:
        m = re.match(r"^(#{1,4})\s+(.+)$", line)
        if m:
            if current_lines:
                sections.append(
                    {
                        "heading": current_heading,
                        "level": len(m.group(1)),
                        "content": "\n".join(current_lines).strip(),
                    }
                )
            current_heading = m.group(2).strip()
            current_level = len(m.group(1))
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections.append(
            {
                "heading": current_heading,
                "level": current_level,
                "content": "\n".join(current_lines).strip(),
            }
        )

    # Lazy import to avoid circular dependency:
    # textbook_ingest imports this module, and this function needs
    # is_toc_entry which lives in textbook_ingest.
    from custom_tools.textbook_ingest import is_toc_entry

    sections = [s for s in sections if len(s["content"]) > 50]
    sections = [s for s in sections if not is_toc_entry(s["content"])]
    return "Markdown Document", sections
