---
type: procedure
status: active
baseline: true
created: 2026-08-06
description: Create a new vault note with automatic YAML frontmatter, path validation, and directory routing. Use this INSTEAD of vault_safe_write for note creation.
when_to_use: when creating a new vault note, when the user asks to "write a note", "create a note", or "save this as a note"
falsifiable_if: the procedure writes to the wrong directory, skips locked-note protection, or produces invalid YAML frontmatter
applies_to:
  - note-creation
  - vault-writes
  - knowledge-management
allowed_tools:
  - vault_safe_write
  - vault_list
trigger:
  - create a note
  - write a note
  - save this as a note
  - new note
  - store this
inhibitor:
  - edit an existing note
  - modify a note
  - append to a note
  - delete a note
summary: "Create a new vault note with automatic YAML frontmatter, path validation, and directory routing."
tags:
  - procedure
  - note-creation
model_cartridge: small
---

# Write-Note

## When to Run This

When you need to create a new vault note. This procedure handles:
1. **Directory routing** — decides where the note goes based on content type
2. **YAML frontmatter** — auto-injects missing fields (type, status, created, summary, tags)
3. **Locked-note protection** — blocks writes to LOCKED or sacred journal files
4. **Name deduplication** — checks if a note with the same name already exists (anywhere in the vault)

## Why This Exists

Creating notes directly with vault_safe_write skipped directory routing, frontmatter injection, and duplicate-name protection. This procedure exists to wrap note creation with those safeguards. The key tradeoff: it blocks duplicate names by default (unless allow_suffix is set) and protects locked/sacred files, so a bad write can't silently clobber an existing note.

## Inputs

| Key | Required | Description |
|-----|----------|-------------|
| `title` | Yes | The note title (e.g. `My-Concept`) |
| `content` | Yes | The markdown body (without frontmatter) |
| `type` | No | Note type (default: `concept`) |
| `status` | No | Note status (default: `active`) |
| `tags` | No | List of tags (default: `[concept]`) |
| `summary` | No | One-line summary (default: derived from first paragraph) |
| `directory` | No | Override the auto-routed directory |
| `allow_suffix` | No | If a duplicate name exists, auto-suffix with `-2`, `-3`, etc. (default: false) |

## Steps

### Step 1: Validate inputs and route directory

```python
import json, re

title = args.get("title", "").strip()
content = args.get("content", "").strip()
note_type = args.get("type", "concept")
status = args.get("status", "active")
tags = args.get("tags", ["concept"])
summary = args.get("summary", "")
directory = args.get("directory", "")
allow_suffix = args.get("allow_suffix", False)

# Validate required fields
if not title:
    result = json.dumps({"error": "title is required"})
elif not content:
    result = json.dumps({"error": "content is required"})
else:
    # Route directory if not specified
    if not directory:
        if note_type == "procedure":
            directory = "vaultbot/System/Procedures"
        elif note_type in ("concept", "research", "reference"):
            directory = "vaultbot/Knowledge/Concepts"
        elif note_type in ("memory", "chat"):
            directory = "vaultbot/Memory/Chat"
        else:
            directory = "vaultbot/Knowledge/Concepts"

    # Get ALL existing notes across the entire vault
    all_files = vault_list()
    existing_names = [f.split("/")[-1].replace(".md", "") for f in all_files]

    # Check for duplicate name (anywhere in the vault)
    if title in existing_names:
        if allow_suffix:
            # Generate unique suffixed name
            candidate = f"{title}-2"
            counter = 3
            while candidate in existing_names:
                candidate = f"{title}-{counter}"
                counter += 1
            final_title = candidate
        else:
            result = json.dumps({
                "error": f"Duplicate note name '{title}' exists in the vault",
                "existing_in": [f for f in all_files if f.split("/")[-1].replace(".md", "") == title],
                "action": "use allow_suffix=true to auto-suffix, or choose a different title"
            })
    else:
        # Derive summary if not provided
        if not summary:
            first_para = content.split("\n\n")[0][:200]
            summary = first_para.replace("#", "").strip()[:150]

        result = json.dumps({
            "directory": directory,
            "title": title,
            "note_type": note_type,
            "status": status,
            "tags": tags,
            "summary": summary,
            "content": content,
        })
```

### Step 2: Build YAML frontmatter and write the note

```python
import json
from datetime import date

data = json.loads(output)

if "error" in data:
    result = json.dumps(data)
else:
    # Use suffixed title if generated
    final_title = data.get("title")

    # Build YAML frontmatter
    tags_yaml = "\n".join(f"  - {t}" for t in data["tags"])
    frontmatter = f"""---
type: {data["note_type"]}
status: {data["status"]}
created: {date.today().isoformat()}
tags:
{tags_yaml}
summary: {data["summary"]}
---"""

    full_content = frontmatter + "\n\n# " + final_title + "\n\n" + data["content"]

    # Write the note
    file_path = f"{data['directory']}/{final_title}.md"
    write_result = vault_safe_write(file_path=file_path, content=full_content)

    result = json.dumps({
        "status": "written",
        "file_path": file_path,
        "note": f"Created {final_title} in {data['directory']}",
        "write_result": write_result,
    })
```

## Example Usage

```python
execute_procedure("Write-Note", args={
    "title": "My-New-Concept",
    "content": "This is a new concept about...",
    "type": "concept",
    "tags": ["concept", "research"],
    "summary": "A new concept about..."
})
```

## Stacking

This procedure is designed to be called by higher-level procedures:
- `Link-Notes` can call `Write-Note` to create placeholder notes
- `Profile-User` can call `Write-Note` to create profile notes
- `Connect-Concepts` can call `Write-Note` to create new concept notes

## Related

- [[Vault-Lint]] — lints a note after writing
- [[Write-Procedure-Draft]] — drafts a procedure note for review
- [[Safe-Write]] — the safe-write primitive this procedure builds on
