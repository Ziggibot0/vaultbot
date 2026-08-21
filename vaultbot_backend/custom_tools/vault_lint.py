"""
Agent-authored tool: vault_lint
"""

SCHEMA = {
    "name": "vault_lint",
    "description": (
        "Check a note for broken wikilinks, missing frontmatter, argument "
        "quality, and other quality issues. Returns a report of all wikilinks, "
        "which ones are broken (target note doesn't exist), whether the note "
        "has YAML frontmatter, what tags it has, and whether it passes "
        "argument-quality checks (minimum length, has wikilinks, contains "
        "reasoning language). Ignores wikilinks inside code spans/blocks. "
        "Checks against all vault files (not just .md). Use this after "
        "writing notes to verify quality."
    ),
    "parameters": {
        "properties": {
            "file_path": {
                "description": (
                    "Path to the note to lint, relative to vault root (e.g. "
                    "'vaultbot-stuff/System/Identity/Autonomy-Directive.md')"
                ),
                "type": "string",
            }
        },
        "required": ["file_path"],
        "type": "object",
    },
}

import os  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402

from paths import VAULT_ROOT  # noqa: E402

# The vault root is resolved centrally in paths.py (vault is a sibling of
# vaultbot_backend/, not an ancestor).
EXCLUDE_DIRS = {
    ".git",
    "node_modules",
    ".obsidian",
    "vaultbot_venv",
    "__pycache__",
    "checkpoints",
    ".venv",
}


def _build_file_index():
    """Build sets of all file stems and all relative paths in the vault."""
    stems = set()
    paths = set()
    for root, dirs, files in os.walk(VAULT_ROOT):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            full = Path(root) / f
            rel = str(full.relative_to(VAULT_ROOT)).replace("\\", "/")
            stems.add(Path(f).stem)
            paths.add(rel)
    return stems, paths


def _strip_code(content: str) -> str:
    """Remove code blocks and inline code spans so wikilinks inside them
    aren't matched."""
    # Strip fenced code blocks (```...```)
    content = re.sub(r"```.*?```", "", content, flags=re.DOTALL)
    # Strip inline code spans (`...`)
    content = re.sub(r"`[^`]+`", "", content)
    return content


def _check_argument_quality(content: str) -> list:
    """Check if a note is a self-contained argument, not just bare facts.

    Enforces the NOTE QUALITY rule from the system prompt: notes must be
    self-contained arguments with claim, reasoning, and connections in prose.
    """
    issues = []

    # Strip frontmatter for body-level checks
    body = re.sub(r"^---\n.*?\n---\n", "", content, flags=re.DOTALL)
    body = body.strip()

    # Check 1: Minimum length (too short = probably just a fact dump)
    if len(body) < 200:
        issues.append(
            {
                "type": "too_short",
                "message": (
                    f"Note body is only {len(body)} chars — likely a bare "
                    f"fact, not a self-contained argument. Aim for claim + "
                    f"reasoning + connections in prose."
                ),
            }
        )

    # Check 2: Contains at least one wikilink (connections to related notes)
    clean = _strip_code(content)
    wikilinks = re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", clean)
    if len(wikilinks) == 0:
        issues.append(
            {
                "type": "no_wikilinks",
                "message": (
                    "Note has no wikilinks — it should connect to related "
                    "notes. Use [[Note-Title]] to cite related concepts."
                ),
            }
        )

    # Check 3: Contains reasoning language
    reasoning_markers = [
        "because",
        "therefore",
        "which means",
        "this means",
        "this implies",
        "contradicts",
        "supports",
        "caused by",
        "as a result",
        "consequently",
        "the reason",
        "due to",
        "in order to",
        "so that",
        "which is why",
        "evidence",
        "implies",
        "results in",
        "leads to",
        "stems from",
        "however",
        "but",
        "although",
        "despite",
        "while",
        "whereas",
    ]
    body_lower = body.lower()
    reasoning_hits = [m for m in reasoning_markers if m in body_lower]
    if len(reasoning_hits) == 0:
        issues.append(
            {
                "type": "no_reasoning_language",
                "message": (
                    "Note contains no reasoning language (because, therefore, "
                    "which means, contradicts, however, etc.) — it should "
                    "explain WHY, not just state facts."
                ),
            }
        )

    return issues


def run(args: dict) -> dict:
    file_path = args.get("file_path", "")

    if not file_path:
        return {"error": "file_path is required"}

    full = (VAULT_ROOT / file_path).resolve()

    try:
        full.relative_to(VAULT_ROOT.resolve())
    except ValueError:
        return {"error": "path must be inside vault root"}

    if not full.exists():
        return {"error": f"file not found: {file_path}"}

    content = full.read_text(encoding="utf-8")
    issues = []

    # Build index of all files for link checking
    all_stems, all_paths = _build_file_index()

    # Strip code before matching wikilinks
    clean_content = _strip_code(content)

    # Find all wikilinks: [[Target]] or [[Target|alias]]
    wikilinks = re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", clean_content)
    wikilinks = [ln.strip() for ln in wikilinks if ln.strip()]

    broken_links = []
    for link in wikilinks:
        # Valid if it matches a file stem OR a relative file path
        if link not in all_stems and link not in all_paths:
            broken_links.append(link)

    if broken_links:
        issues.append(
            {
                "type": "broken_wikilinks",
                "count": len(broken_links),
                "links": broken_links,
            }
        )

    # Check for frontmatter
    has_frontmatter = content.startswith("---")
    if not has_frontmatter:
        issues.append(
            {"type": "missing_frontmatter", "message": "Note has no YAML frontmatter"}
        )

    # Check for empty sections
    empty_sections = re.findall(r"^#+\s+.+\n\s*\n(?=^#|\Z)", content, re.MULTILINE)
    if empty_sections:
        issues.append({"type": "empty_sections", "count": len(empty_sections)})

    # Check argument quality (NOTE QUALITY rule enforcement)
    quality_issues = _check_argument_quality(content)
    issues.extend(quality_issues)

    # Universal schema validation
    try:
        import os
        import sys

        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        from note_schema import validate_schema

        _schema_ok, schema_errors, schema_warnings = validate_schema(content)
        for err in schema_errors:
            issues.append({"type": "schema_error", "message": err})
        for warn in schema_warnings:
            issues.append({"type": "schema_warning", "message": warn})
    except ImportError:
        pass  # note_schema unavailable — skip schema checks

    # --- Procedure-specific frontmatter checks ---
    # If this note is a procedure (type: procedure in frontmatter), run the
    # authoritative procedure_validator so the full required-field set is
    # enforced (type, description, when_to_use, allowed_tools,
    # falsifiable_if, status, model_cartridge, created, summary, tags) —
    # not just the three fields the old check looked at. This keeps
    # vault_lint in lockstep with Procedure-Creator's pre-publication gate.
    if has_frontmatter:
        try:
            import os
            import sys

            backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if backend_dir not in sys.path:
                sys.path.insert(0, backend_dir)
            from procedure_validator import validate_procedure_text

            result = validate_procedure_text(content)
            # Only surface frontmatter errors here — step/syntax/tool issues
            # are the validator's job at publication time, not vault_lint's.
            for err in result.get("errors", []):
                if err.startswith("Frontmatter"):
                    issues.append(
                        {"type": "procedure_frontmatter_error", "message": err}
                    )
        except Exception as e:  # noqa: BLE001 — fail loud, don't silently skip
            issues.append(
                {
                    "type": "procedure_check_failed",
                    "message": (
                        f"vault_lint could not validate procedure frontmatter: {e}"
                    ),
                }
            )

    # Extract tags (avoid matching hex colors like #FF0000)
    tags = list(set(re.findall(r"(?<!\w)#([a-zA-Z][a-zA-Z0-9_-]*)", content)))

    return {
        "file_path": str(full),
        "total_wikilinks": len(wikilinks),
        "broken_wikilinks": broken_links,
        "has_frontmatter": has_frontmatter,
        "tag_count": len(tags),
        "tags": tags,
        "issues": issues,
        "issue_count": len(issues),
    }
