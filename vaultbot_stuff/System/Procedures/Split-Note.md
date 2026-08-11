---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-03
description: Split verbose notes with multiple distinct claims into separate one-idea-per-note files. Auto-scans the vault for split candidates, then writes each part as its own note with proper frontmatter. The original note becomes a hub note with wikilinks to the parts.
when_to_use: when notes are too verbose, cover multiple distinct claims, or during Dream-Pass maintenance to make notes more granular and discoverable
applies_to:
  - vault-maintenance
  - note-quality
  - one-idea-per-note
  - dream-pass
allowed_tools:
  - vault_list
  - code_read
  - vault_safe_write
falsifiable_if: it splits a note that should stay together (one coherent argument), or fails to split a note with genuinely distinct claims
summary: Summary|split_note_metadata_generator,note_schema_spliters,split_files_extraction,directories_filtering,python_backends_detection;1. The Note Generator Analyzes Metadata and Generates Split Notes with
tags:
  - procedure
  - procedures
---

# Split-Note

Splits verbose notes into multiple smaller notes — one idea per note.
This makes each idea equally discoverable by retrieval, and lets each
claim carry its own frontmatter (supports, contradicts, confidence).

## Steps

1. ```python
   import json, os, re, sys
   from pathlib import Path

# Import the schema module
backend = str(Path(vault_path) / "vaultbot_stuff" / "vaultbot_backend")
if backend not in sys.path:
    sys.path.insert(0, backend)
from note_schema import split_note_if_needed, parse_frontmatter

# Auto-scan: walk all .md files and find split candidates
vault = Path(vault_path)
candidates = []

# Skip directories that shouldn't be split
skip_dirs = {"vaultbot_stuff/Memory/Chat", "vaultbot_stuff/vaultbot_backend", ".obsidian"}

for md_file in vault.rglob("*.md"):
    rel = md_file.relative_to(vault)
    rel_str = str(rel).replace("\\", "/")

    # Skip non-content directories
    if any(rel_str.startswith(s) for s in skip_dirs):
        continue
    # Skip procedures themselves
    if "System/Procedures" in rel_str:
        continue
    # Skip date-only filenames (sacred journals)
    stem = md_file.stem
    if re.match(r"^\d{4}-\d{2}-\d{2}$", stem):
        continue

    try:
        content = md_file.read_text(encoding="utf-8")
    except Exception:
        continue

    # Heuristic: notes over 3000 chars with multiple ## sections are candidates
    if len(content) < 3000:
        continue

    # Count top-level sections (## headings)
    sections = re.findall(r"^##\s+.+", content, re.MULTILINE)
    if len(sections) < 3:
        continue

    # Try the schema split function
    try:
        result = split_note_if_needed(content, str(rel))
        if result and len(result) > 1:
            candidates.append({
                "file_path": rel_str,
                "char_count": len(content),
                "section_count": len(sections),
                "split_parts": len(result),
            })
    except Exception:
        pass

print(json.dumps({"candidates": candidates, "count": len(candidates)}, indent=2))
```

2. ```python
   import json, os, re, sys
   from pathlib import Path

   backend = str(Path(vault_path) / "vaultbot_stuff" / "vaultbot_backend")
if backend not in sys.path:
    sys.path.insert(0, backend)
from note_schema import split_note_if_needed, inject_schema, parse_frontmatter

vault = Path(vault_path)

# Get candidates from step 1
data = json.loads(prior_results[-1])
candidates = data.get("candidates", [])

results = []

for cand in candidates:
    rel_path = cand["file_path"]
    md_file = vault / rel_path
    content = md_file.read_text(encoding="utf-8")

    try:
        parts = split_note_if_needed(content, rel_path)
    except Exception as e:
        results.append({"file": rel_path, "error": str(e)})
        continue

    if not parts or len(parts) < 2:
        results.append({"file": rel_path, "skipped": "no split needed"})
        continue

    # Create split notes
    stem = md_file.stem
    parent = md_file.parent
    created_links = []

    for i, part in enumerate(parts):
        # Build new note title
        # Extract a title from the part's first heading or use numbered suffix
        part_text = part if isinstance(part, str) else part.get("content", "")
        heading_match = re.search(r"^##?\s+(.+)", part_text, re.MULTILINE)
        if heading_match:
            part_title = heading_match.group(1).strip()
            # Clean for filename
            part_title = re.sub(r'[^\w\s-]', '', part_title).strip()[:60]
        else:
            part_title = f"{stem}-Part-{i+1}"

        new_filename = f"{part_title}.md"
        new_path = parent / new_filename

        # Ensure unique filename
        counter = 1
        while new_path.exists():
            new_path = parent / f"{part_title}-{counter}.md"
            counter += 1

        # Inject schema if needed
        fm = parse_frontmatter(part_text)
        if not fm:
            part_text = inject_schema(part_text)

        # Write the new note
        new_rel = str(new_path.relative_to(vault)).replace("\\", "/")
        try:
            new_path.write_text(part_text, encoding="utf-8")
            created_links.append(new_rel)
            results.append({"split_from": rel_path, "created": new_rel, "status": "ok"})
        except Exception as e:
            results.append({"split_from": rel_path, "error writing": str(e)})

    # Convert original to hub note if we created splits
    if created_links:
        fm = parse_frontmatter(content)
        hub_content = f"""---
type: hub
status: {fm.get('status', 'active')}
created: {fm.get('created', '2026-08-04')}
summary: "{fm.get('summary', stem)} — split into sub-notes"
tags:
  - hub
  - split
---

# {stem}

This note was split into separate notes for better discoverability.

"""
        for link in created_links:
            link_stem = Path(link).stem
            hub_content += f"- [[{link_stem}]]\n"

        try:
            md_file.write_text(hub_content, encoding="utf-8")
            results.append({"hub": rel_path, "links": created_links, "status": "hub_created"})
        except Exception as e:
            results.append({"hub": rel_path, "error": str(e)})

print(json.dumps({"results": results, "total_splits": len([r for r in results if r.get("status") == "ok"])}, indent=2))
```