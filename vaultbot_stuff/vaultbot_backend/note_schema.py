"""Universal note schema for VaultBot.

Every ``.md`` note written to the vault must have YAML frontmatter with a
minimal set of required fields.  Notes that make **claims** can also carry
optional claim-schema fields (``supports``, ``contradicts``, ``confidence``,
``falsifiable_if``, …) that let the vault do deterministic reasoning without
LLM calls.

This module is pure-stdlib — no LLM, no I/O.  It is imported by every write
path (``vault_safe_write``, ``vault_append``, ``research_engine``,
``vault_maintenance``, ``consolidation_pipeline``, ``graph_ops``) so that no
note lands on disk without the schema.  Missing required fields are
**auto-injected** (the LLM does not have to know the schema); only
*invalid* values are rejected.

Design decisions (see /memories/session/plan.md):
- Auto-inject, don't reject:  the system self-heals missing fields.
- Optional claim fields are opt-in:  not auto-injected.
- Procedures keep their own schema:  this module adds required fields but
  never removes or overrides procedure-specific fields.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

# ── Constants ────────────────────────────────────────────────────────

REQUIRED_FIELDS: tuple[str, ...] = (
    "type",
    "status",
    "created",
    "summary",
    "tags",
)

# Optional claim-schema fields.  Present only when the note makes a claim.
CLAIM_FIELDS: tuple[str, ...] = (
    "supports",
    "contradicts",
    "derived_from",
    "depends_on",
    "confidence",
    "falsifiable_if",
    "evidence_count",
    "evidence_sources",
)

VALID_TYPES: frozenset[str] = frozenset({
    "research",
    "research-note",
    "semantic",
    "architecture",
    "architecture-plan",
    "system-design",
    "claim",
    "pattern",
    "pattern-highway",
    "concept",
    "procedure",
    "exemplar",
    "chat",
    "bridge",
    "audit",
    "diagnostic",
    "synthesis",
    "roadmap",
    "plan",
})

VALID_STATUSES: frozenset[str] = frozenset({
    "raw",
    "draft",
    "active",
    "experimental",
    "verified",
    "complete",
    "tentative",
    "design-spec",
    "superseded",
    "deprecated",
    "flagged",
    "rejected",
    "alias",
})

# Path-prefix → type inference.  First match wins.
_TYPE_INFERENCE: tuple[tuple[str, str], ...] = (
    ("System/Procedures/", "procedure"),
    ("System/Architecture/", "architecture"),
    ("System/Exemplars/", "exemplar"),
    ("System/Patterns/", "pattern"),
    ("System/Identity/", "semantic"),
    ("System/Playbooks/", "semantic"),
    ("System/Quality-Gates/", "semantic"),
    ("Knowledge/Research/", "research"),
    ("Knowledge/Concepts/", "concept"),
    ("Knowledge/Biology/", "research"),
    ("Knowledge/Simulations/", "research"),
    ("Knowledge/Textbooks/", "research"),
    ("Memory/Chat/", "chat"),
    ("Memory/Build-Log/", "semantic"),
)

_DEFAULT_TYPE = "claim"

# Fields whose values are lists of wikilink strings.
_LIST_FIELDS: frozenset[str] = frozenset({
    "supports",
    "contradicts",
    "derived_from",
    "depends_on",
    "evidence_sources",
    "tags",
    "scope",
    "applies_to",
    "evidence_sources",
})

# Fields that must be numeric.
_FLOAT_FIELDS: frozenset[str] = frozenset({"confidence", "success_rate"})
_INT_FIELDS: frozenset[str] = frozenset({
    "evidence_count", "source_count", "fact_count", "review_interval_days",
    "success_count", "failure_count",
})

_FM_START = re.compile(r"\A---\s*\n")
_FM_END = re.compile(r"\n---\s*(?:\n|$)")

# ── Frontmatter parser (same logic as procedure_validator) ───────────


def _strip_quotes(value: str) -> str:
    """Strip surrounding quotes from a YAML value string."""
    value = value.strip()
    if len(value) >= 2:
        if (value[0] == '"' and value[-1] == '"') or (
            value[0] == "'" and value[-1] == "'"
        ):
            return value[1:-1]
    return value


def _parse_inline_list(value: str) -> list[str] | None:
    """Parse an inline YAML list like ``[item1, item2, "[[Note]]"]``.

    Returns ``None`` if the value is not an inline list.
    """
    value = value.strip()
    if not (value.startswith("[") and value.endswith("]")):
        return None
    inner = value[1:-1].strip()
    if not inner:
        return []
    items = []
    # Split on commas, respecting quoted strings with commas inside
    in_quote = False
    quote_char = ""
    buf = ""
    for ch in inner:
        if not in_quote and ch in ('"', "'"):
            in_quote = True
            quote_char = ch
            buf += ch
        elif in_quote and ch == quote_char:
            in_quote = False
            quote_char = ""
            buf += ch
        elif ch == "," and not in_quote:
            items.append(_strip_quotes(buf))
            buf = ""
        else:
            buf += ch
    if buf.strip():
        items.append(_strip_quotes(buf))
    return items


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Parse YAML frontmatter into a flat dict (keys + list values).

    Handles simple key: value pairs, ``  - item`` list syntax, and
    inline lists like ``tags: [a, b, c]``.
    Returns ``{}`` if the text has no frontmatter.
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm_str = text[3:end].strip()
    fm: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list | None = None
    for line in fm_str.split("\n"):
        line = line.rstrip()
        if not line:
            continue
        # Only treat lines with NO leading whitespace as top-level keys.
        # Indented lines (e.g. "  - item" or "    type: string" inside a
        # nested list) are list items or nested keys — they must NOT
        # overwrite top-level fields.  This prevents a nested "type: string"
        # inside an "inputs:" list from clobbering the real "type: procedure".
        if line.startswith(" ") or line.startswith("\t"):
            if line.lstrip().startswith("- ") and current_key:
                value = _strip_quotes(line.lstrip()[2:].strip())
                if current_list is None:
                    current_list = []
                    fm[current_key] = current_list
                current_list.append(value)
            continue
        if ":" in line:
            current_list = None
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            # Try inline list first: [item, item]
            inline = _parse_inline_list(value)
            if inline is not None:
                fm[key] = inline
                current_key = key
                continue
            # Strip quotes from scalar values
            value = _strip_quotes(value)
            if value:
                fm[key] = value
                current_key = key
            else:
                current_key = key
                current_list = None
    return fm


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Split note text into (frontmatter_str, body_str).

    Returns ("", text) if no frontmatter is present.
    """
    m_start = _FM_START.match(text)
    if not m_start:
        return "", text
    m_end = _FM_END.search(text, m_start.end())
    if not m_end:
        return "", text
    fm_str = text[m_start.end():m_end.start()]
    body = text[m_end.end():]
    return fm_str, body


def _infer_type(file_path: str) -> str:
    """Infer note type from the file path relative to vault root."""
    normalized = file_path.replace("\\", "/")
    for prefix, note_type in _TYPE_INFERENCE:
        if prefix in normalized:
            return note_type
    return _DEFAULT_TYPE


def _infer_summary(body: str) -> str:
    """Derive a one-line summary from the body's first heading or paragraph."""
    # First H1
    h1 = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    if h1:
        return h1.group(1).strip()[:200]
    # First H2
    h2 = re.search(r"^##\s+(.+)$", body, re.MULTILINE)
    if h2:
        return h2.group(1).strip()[:200]
    # First non-empty paragraph
    for line in body.strip().split("\n"):
        line = line.strip()
        if line and not line.startswith("<!--") and not line.startswith("```"):
            return line[:200]
    return "Untitled note"


def _infer_tags(file_path: str, note_type: str) -> list[str]:
    """Derive tags from the note type and parent directory name."""
    normalized = file_path.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p and not p.endswith(".md")]
    tags = [note_type]
    # Add the immediate parent directory as a tag (e.g. "Research", "Chat")
    if parts:
        parent = parts[-1]
        if parent.lower() not in ("vaultbot_stuff", "system", "knowledge",
                                  "memory", "user"):
            tags.append(parent.lower())
    return tags


def _format_frontmatter(fm: dict[str, Any]) -> str:
    """Render a frontmatter dict back to YAML text (without --- wrappers)."""
    lines: list[str] = []
    for key, value in fm.items():
        if isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                for item in value:
                    item_str = str(item)
                    # Wrap in quotes if it contains special chars
                    if "[[" in item_str or ":" in item_str or "#" in item_str:
                        lines.append(f'  - "[{item_str}]"' if False
                                     else f'  - "{item_str}"')
                    else:
                        lines.append(f"  - {item_str}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key}: {value}")
        else:
            val_str = str(value)
            if ":" in val_str or "[" in val_str or "#" in val_str:
                lines.append(f'{key}: "{val_str}"')
            else:
                lines.append(f"{key}: {val_str}")
    return "\n".join(lines)


# ── Public API ───────────────────────────────────────────────────────


def inject_schema(
    content: str,
    file_path: str,
    existing_content: str | None = None,
    *,
    force_type: str | None = None,
) -> str:
    """Ensure *content* has valid frontmatter with all required fields.

    Missing required fields are auto-injected using inference rules.
    Existing fields are **always preserved** — this function never removes
    or overwrites a field the caller provided.

    Parameters
    ----------
    content : str
        The note content to check (may or may not have frontmatter).
    file_path : str
        Path relative to vault root, used for type/tag inference.
    existing_content : str, optional
        If overwriting an existing note, the current file content.  The
        existing ``status`` and ``created`` values are preserved unless
        the new content explicitly provides them.
    force_type : str, optional
        Override the type inference.  Used by callers that know the type
        (e.g. ``research_engine`` knows it's writing a ``research`` note).

    Returns
    -------
    str
        The note content with valid frontmatter.
    """
    fm_str, body = _split_frontmatter(content)
    fm = parse_frontmatter(content) if fm_str else {}

    # Merge existing note's preserved fields
    existing_fm: dict[str, Any] = {}
    if existing_content:
        existing_fm = parse_frontmatter(existing_content)

    today = date.today().isoformat()

    # --- Check if any required field is actually missing ---
    # If all required fields are present, return the original content
    # unchanged — don't reformat existing frontmatter (avoids spurious
    # rewrites on boot-time healing of already-valid notes).
    missing: list[str] = []
    for field in REQUIRED_FIELDS:
        if field not in fm:
            missing.append(field)

    if not missing and fm_str:
        # All required fields present — no injection needed.
        return content

    # --- Required fields (auto-inject only the missing ones) ---
    if "type" not in fm:
        inferred = force_type or _infer_type(file_path)
        fm["type"] = inferred
    if "status" not in fm:
        # Preserve existing status, default to 'raw' for new notes
        fm["status"] = existing_fm.get("status", "raw")
    if "created" not in fm:
        # Preserve existing created date, default to today
        fm["created"] = existing_fm.get("created", today)
    if "summary" not in fm:
        fm["summary"] = _infer_summary(body)
    if "tags" not in fm:
        fm["tags"] = _infer_tags(file_path, fm.get("type", _DEFAULT_TYPE))

    # Ensure tags is a list
    if isinstance(fm.get("tags"), str):
        fm["tags"] = [t.strip() for t in fm["tags"].split(",")]

    # Rebuild the note with the injected frontmatter
    new_fm_str = _format_frontmatter(fm)
    if body.startswith("\n"):
        return f"---\n{new_fm_str}\n---{body}"
    return f"---\n{new_fm_str}\n---\n\n{body.lstrip()}"


def validate_schema(content: str) -> tuple[bool, list[str], list[str]]:
    """Validate a note's frontmatter against the universal schema.

    Returns
    -------
    ok : bool
        True if no errors (warnings are OK).
    errors : list[str]
        Must-fix issues (invalid values, not just missing — missing is
        auto-injected by :func:`inject_schema`).
    warnings : list[str]
        Soft issues (missing optional claim fields, unusual values).
    """
    fm = parse_frontmatter(content)
    errors: list[str] = []
    warnings: list[str] = []

    if not fm:
        errors.append("No frontmatter found — call inject_schema() first")
        return False, errors, warnings

    # Check required fields exist
    for field in REQUIRED_FIELDS:
        if field not in fm:
            errors.append(f"Missing required field: {field}")

    # Validate type
    note_type = fm.get("type", "")
    if note_type and note_type not in VALID_TYPES:
        errors.append(
            f"Invalid type '{note_type}'. Valid types: "
            f"{', '.join(sorted(VALID_TYPES))}"
        )

    # Validate status
    note_status = fm.get("status", "")
    if note_status and note_status not in VALID_STATUSES:
        errors.append(
            f"Invalid status '{note_status}'. Valid statuses: "
            f"{', '.join(sorted(VALID_STATUSES))}"
        )

    # Validate numeric fields
    for field in _FLOAT_FIELDS:
        if field in fm:
            try:
                float(fm[field])
            except (ValueError, TypeError):
                errors.append(f"{field} must be a number, got: {fm[field]}")
    for field in _INT_FIELDS:
        if field in fm:
            try:
                int(fm[field])
            except (ValueError, TypeError):
                errors.append(f"{field} must be an integer, got: {fm[field]}")

    # Validate list fields format
    for field in _LIST_FIELDS:
        if field in fm:
            val = fm[field]
            if not isinstance(val, list) and not isinstance(val, str):
                errors.append(f"{field} must be a list, got: {type(val).__name__}")
            # Warn if list fields contain non-wikilink-looking values
            if isinstance(val, list):
                for item in val:
                    if not isinstance(item, str):
                        errors.append(
                            f"{field} list item must be a string, got: {type(item).__name__}"
                        )

    # Warnings for missing optional claim fields on claim-like types
    claim_types = {"claim", "architecture", "architecture-plan", "semantic",
                   "diagnostic", "pattern", "pattern-highway"}
    if note_type in claim_types:
        if "falsifiable_if" not in fm:
            warnings.append(
                f"Claim-like note (type:{note_type}) has no falsifiable_if — "
                f"consider adding one for testability"
            )

    ok = len(errors) == 0
    return ok, errors, warnings


def split_note_if_needed(
    content: str,
    file_path: str,
    *,
    max_sections: int = 2,
    min_section_chars: int = 200,
) -> list[dict[str, str]] | None:
    """Identify whether a note should be split into multiple smaller notes.

    A note is a split candidate if it has more than *max_sections* H2
    sections, each with at least *min_section_chars* of content, AND the
    sections cover distinct topics (not just sub-headings of one argument).

    Returns
    -------
    list[dict] or None
        If the note should be split, returns a list of split proposals:
        ``[{"title": ..., "content": ..., "file_path": ...}, ...]``.
        The original note becomes a hub note with wikilinks to the parts.
        Returns ``None`` if the note should NOT be split.
    """
    fm_str, body = _split_frontmatter(content)
    fm = parse_frontmatter(content) if fm_str else {}

    # Never split procedures, roadmaps, plans, or chat notes
    note_type = fm.get("type", "")
    if note_type in ("procedure", "roadmap", "plan", "chat", "exemplar"):
        return None

    # Find H2 sections
    h2_pattern = re.compile(r"^##\s+(.+)$", re.MULTILINE)
    h2_matches = list(h2_pattern.finditer(body))

    if len(h2_matches) <= max_sections:
        return None

    # Extract sections
    sections: list[tuple[str, str]] = []
    for i, m in enumerate(h2_matches):
        title = m.group(1).strip()
        start = m.start()
        end = h2_matches[i + 1].start() if i + 1 < len(h2_matches) else len(body)
        section_body = body[start:end].strip()
        if len(section_body) >= min_section_chars:
            sections.append((title, section_body))

    # Need at least 2 qualifying sections to bother splitting
    if len(sections) < 2:
        return None

    # Check that sections have distinct topics by comparing their content
    # word sets.  Structural sections (Introduction, Evidence, Conclusion)
    # of a single argument share many content words; distinct claims about
    # different topics share very few.
    section_word_sets: list[set[str]] = []
    for _, section_body in sections:
        words = set(
            w.lower() for w in re.findall(r"\w+", section_body)
            if len(w) > 3 and w.lower() not in {
                "that", "this", "with", "from", "have", "they", "their",
                "which", "would", "could", "should", "there", "where",
                "when", "what", "each", "also", "than", "then", "into",
                "been", "more", "such", "these", "those", "will", "does",
                "note", "section",
            }
        )
        section_word_sets.append(words)

    # Compute pairwise Jaccard similarity.  If the average similarity is
    # high (> 0.25), the sections are about the same topic and should
    # stay together.  Low similarity means genuinely distinct topics.
    total_sim = 0.0
    pair_count = 0
    for i in range(len(section_word_sets)):
        for j in range(i + 1, len(section_word_sets)):
            union = section_word_sets[i] | section_word_sets[j]
            if union:
                sim = len(section_word_sets[i] & section_word_sets[j]) / len(union)
                total_sim += sim
                pair_count += 1
    avg_sim = total_sim / pair_count if pair_count > 0 else 0.0

    if avg_sim > 0.25:
        return None  # sections are about the same topic — don't split

    # Build split proposals
    stem = file_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].replace(".md", "")
    proposals: list[dict[str, str]] = []
    for title, section_body in sections:
        slug = re.sub(r"[^a-zA-Z0-9_-]", "-", title)[:60].strip("-")
        part_path = f"{stem}--{slug}.md"
        part_content = inject_schema(
            f"# {title}\n\n{section_body}",
            part_path,
            force_type=fm.get("type", _DEFAULT_TYPE),
        )
        # Copy relevant claim fields from parent
        parent_fm = parse_frontmatter(part_content)
        for claim_field in ("falsifiable_if", "evidence_count",
                            "evidence_sources", "confidence"):
            if claim_field in fm:
                parent_fm[claim_field] = fm[claim_field]
        part_content = inject_schema(
            f"---\n{_format_frontmatter(parent_fm)}\n---\n\n# {title}\n\n{section_body}",
            part_path,
            force_type=fm.get("type", _DEFAULT_TYPE),
        )
        proposals.append({
            "title": title,
            "file_path": part_path,
            "content": part_content,
        })

    return proposals if proposals else None


def strip_frontmatter(content: str) -> str:
    """Return the body of a note without its frontmatter."""
    _, body = _split_frontmatter(content)
    return body


# ── Boot-time healing ────────────────────────────────────────────────

# Directories the healer should skip (mirrors vault_graph._IGNORED_DIRS).
_HEAL_SKIP_DIRS = {
    ".venv", "vaultbot_venv", "vaultbot_index", "sessions", "partials",
    ".git", ".obsidian", "node_modules", "__pycache__", "vaultbot_backend",
    ".vscode", "trash", ".github", "learningMaterial",
}

# Root-level .md files that are repo meta-files, not vault knowledge.
# The healer skips these so it doesn't inject frontmatter into README.md,
# CONTRIBUTING.md, etc.
_HEAL_SKIP_ROOT_FILES = {
    "README.md", "CONTRIBUTING.md", "SECURITY.md", "LICENSE",
    "CHANGELOG.md", "CODE_OF_CONDUCT.md",
}

# Only heal files under these top-level directories (vault knowledge zones).
_HEAL_ALLOWED_PREFIXES = ("vaultbot_stuff/", "User/")


def heal_note_on_disk(file_path: str | Path, vault_root: str | Path) -> dict:
    """Read a note, inject missing schema fields, write back if changed.

    Returns a dict with keys:
        ``healed`` (bool), ``file_path`` (str), ``changes`` (list[str]),
        ``error`` (str | None).
    """
    p = Path(file_path)
    try:
        content = p.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return {"healed": False, "file_path": str(p), "changes": [],
                "error": str(e)}

    rel = str(p.relative_to(vault_root)).replace("\\", "/")
    original = content

    try:
        healed = inject_schema(content, rel, existing_content=content)
    except Exception as e:  # noqa: BLE001
        return {"healed": False, "file_path": str(p), "changes": [],
                "error": str(e)}

    if healed == original:
        return {"healed": False, "file_path": str(p), "changes": []}

    # Compute what changed for the report
    old_fm = parse_frontmatter(original)
    new_fm = parse_frontmatter(healed)
    changes = []
    for field in REQUIRED_FIELDS:
        if field not in old_fm and field in new_fm:
            changes.append(f"added {field}")

    try:
        p.write_text(healed, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return {"healed": False, "file_path": str(p), "changes": [],
                "error": str(e)}

    return {"healed": True, "file_path": str(p), "changes": changes,
            "error": None}


def heal_vault_schema(vault_root: str | Path,
                      logger=None) -> dict:
    """Scan every ``.md`` in the vault and heal missing schema fields.

    This is the boot-time self-heal.  It reads each note, calls
    :func:`inject_schema` to fill missing required frontmatter fields, and
    writes back only if the content changed.  Optional claim fields are
    never auto-injected — only required fields (type, status, created,
    summary, tags).

    Parameters
    ----------
    vault_root : str | Path
        The vault root directory.
    logger : callable, optional
        If provided, called with ``log(message)`` for progress reporting.

    Returns
    -------
    dict
        ``{"scanned": int, "healed": int, "skipped": int, "errors": int,
        "details": list[dict]}``
    """
    vault = Path(vault_root).resolve()
    scanned = 0
    healed_count = 0
    skipped = 0
    errors = 0
    details: list[dict] = []

    for root, dirs, files in __import__("os").walk(vault):
        dirs[:] = [d for d in dirs if d not in _HEAL_SKIP_DIRS]
        for fname in files:
            if not fname.endswith(".md"):
                continue
            fpath = Path(root) / fname
            # Only heal files inside vault knowledge zones
            rel = str(fpath.relative_to(vault)).replace("\\", "/")
            if not any(rel.startswith(p) for p in _HEAL_ALLOWED_PREFIXES):
                continue
            # Skip root-level repo meta-files (double safety)
            if fname in _HEAL_SKIP_ROOT_FILES and "/" not in rel:
                continue
            scanned += 1
            result = heal_note_on_disk(fpath, vault)
            if result.get("error"):
                errors += 1
                details.append(result)
            elif result["healed"]:
                healed_count += 1
                details.append(result)
            else:
                skipped += 1

            # Progress logging every 200 files
            if logger and scanned % 200 == 0:
                logger(f"Schema heal: scanned {scanned}, healed {healed_count}")

    summary = {
        "scanned": scanned,
        "healed": healed_count,
        "skipped": skipped,
        "errors": errors,
        "details": details[:50],  # cap to avoid huge logs
    }
    if logger:
        logger(
            f"Schema heal complete: scanned {scanned}, healed "
            f"{healed_count}, skipped {skipped}, errors {errors}"
        )
    return summary