---
type: procedure
status: experimental
baseline: true
model_cartridge: big
created: 2026-08-14
description: Scan all procedure notes for missing or thin when_to_use fields, generate better trigger language via LLM, and patch them in place. Self-improving retrieval feedback loop for Dream Pass.
when_to_use: During Dream Pass (after Dream-Evaluate). When procedure retrieval fails to surface a procedure that should have been used. When procedures have missing or under-specified when_to_use frontmatter. When RAG retrieval quality needs improvement. When auditing procedure discoverability. When when_to_use fields are too thin for RAG to surface them. When you notice a procedure wasn't surfaced that should have been. When doing a vault-wide when_to_use audit. When enriching procedure trigger language. When improving procedure retrieval accuracy.
falsifiable_if: After running, any procedure note still has a missing or sub-100-char when_to_use field, or the generated trigger language doesn't improve RAG retrieval for the target procedures
allowed_tools:
  - vault_list
  - vault_read_note
  - llm_generate
  - md_safe_replace
  - vault_lint
tags:
  - procedure
  - dream-pass
  - when-to-use
  - retrieval
  - rag
  - self-improvement
  - frontmatter
summary: Dream-When-To-Use-Update
---

# Dream-When-To-Use-Update

## When to Run This

Run during Dream Pass (after Dream-Evaluate) or standalone when:
- A procedure wasn't surfaced by RAG when it should have been
- Procedures have missing `when_to_use` frontmatter fields
- `when_to_use` fields are too thin (under 100 chars) for RAG to match
- You want to audit and improve procedure discoverability vault-wide

## Why This Exists

The `when_to_use` field is what RAG uses to surface procedures. If it's missing or too thin, the procedure is invisible to retrieval — no matter how good the procedure itself is. This was discovered when all 6 lens procedures had NO `when_to_use` field at all, making them impossible to surface independently via RAG.

This procedure is the **self-improving retrieval feedback loop**: it finds procedures with poor discoverability and fixes them, so next time RAG can find them.

## Inputs

- `min_length`: Minimum character threshold for when_to_use (default: 100)
- `target_procedure`: Optional — only update a specific procedure by name

## Steps

### Step 1: List all procedure notes and identify candidates

```python
import re

min_length = int(args.get("min_length", 100))
target = args.get("target_procedure", "")

# Get all .md files
all_files = vault_list()

# Filter to procedure notes (check frontmatter for type: procedure)
candidates = []
for file_path in all_files:
    # Skip non-md files
    if not file_path.endswith(".md"):
        continue
    # Skip trash/backups
    if "trash" in file_path or "_backup" in file_path:
        continue
    
    try:
        note_result = vault_read_note(file_path, max_lines=0)
        if isinstance(note_result, dict):
            content = note_result.get("content", "")
        else:
            content = str(note_result)
    except Exception:
        continue
    
    # Check if it's a procedure
    if not content.startswith("---"):
        continue
    
    # Extract frontmatter
    fm_match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
    if not fm_match:
        continue
    
    fm = fm_match.group(1)
    if "type: procedure" not in fm:
        continue
    
    # Skip baseline procedures — their when_to_use is curated and shared,
    # not learned per-instance. Rewriting it here would make every user's
    # copy diverge from the canonical baseline.
    if "baseline: true" in fm:
        continue
    
    # If targeting a specific procedure, skip others
    proc_name = file_path.split("/")[-1].replace(".md", "")
    if target and target.lower() not in proc_name.lower():
        continue
    
    # Extract when_to_use
    wtu_match = re.search(r'^when_to_use:\s*["\']?(.*?)["\']?\s*$', fm, re.MULTILINE)
    when_to_use = wtu_match.group(1) if wtu_match else ""
    
    # Extract description for context
    desc_match = re.search(r'^description:\s*["\']?(.*?)["\']?\s*$', fm, re.MULTILINE)
    description = desc_match.group(1) if desc_match else ""
    
    # Check if missing or too thin
    if not when_to_use or len(when_to_use.strip()) < min_length:
        candidates.append({
            "path": file_path,
            "name": proc_name,
            "current_when_to_use": when_to_use,
            "description": description,
            "content_preview": content[:500]
        })

result = f"CANDIDATES: {len(candidates)}\nMIN_LENGTH: {min_length}\n"
for c in candidates:
    result += f"CANDIDATE: {c['path']}::: {c['current_when_to_use'] or '(missing)'}::: {c['description'][:100]}\n"
print(result)
```

[validate: contains "CANDIDATES:"]

---

### Step 2: Generate improved when_to_use for each candidate

For each candidate, use the LLM to generate rich trigger language based on the procedure's description and content.

```python
# Parse Step 1
lines = output.strip().split("\n")
candidates = []
for line in lines:
    if line.startswith("CANDIDATE: "):
        rest = line.replace("CANDIDATE: ", "").strip()
        parts = rest.split("::: ")
        if len(parts) >= 3:
            candidates.append({
                "path": parts[0].strip(),
                "current": parts[1].strip(),
                "description": parts[2].strip()
            })

if not candidates:
    print("NO_CANDIDATES: All procedures have adequate when_to_use fields.")
else:
    updates = []
    for c in candidates:
        # Read the full procedure for context
        try:
            note_result = vault_read_note(c["path"], max_lines=0)
            if isinstance(note_result, dict):
                content = note_result.get("content", "")
            else:
                content = str(note_result)
        except Exception:
            content = ""
        
        prompt = f"""Generate a rich when_to_use field for this VaultBot procedure. The when_to_use field tells RAG when to surface this procedure. It should describe SITUATIONS and TRIGGERS, not topics.

Current when_to_use: {c['current'] or '(missing)'}
Description: {c['description']}
Procedure content (first 800 chars): {content[:800]}

Write a when_to_use string that:
1. Describes specific SITUATIONS when this procedure should be used
2. Includes common phrasings a user might say that trigger this procedure
3. Is at least 100 characters
4. Is specific enough to distinguish from other procedures

Output ONLY the when_to_use text, no quotes, no explanation."""

        new_wtu = llm_generate(prompt).strip()
        
        # Clean up — remove wrapping quotes if the LLM added them
        if new_wtu.startswith('"') and new_wtu.endswith('"'):
            new_wtu = new_wtu[1:-1]
        if new_wtu.startswith("'") and new_wtu.endswith("'"):
            new_wtu = new_wtu[1:-1]
        
        if len(new_wtu) < 50:
            new_wtu = f"When you need to {c['description'][:80]}. When the task involves this procedure's specific workflow."
        
        updates.append({
            "path": c["path"],
            "old": c["current"],
            "new": new_wtu
        })
    
    result = f"UPDATES: {len(updates)}\n"
    for u in updates:
        result += f"UPDATE: {u['path']}::: {u['new']}\n"
    print(result)
```

[validate: contains "UPDATES:"]

---

### Step 3: Apply updates to each procedure note

```python
import re

# Parse Step 2
lines = output.strip().split("\n")
updates = []
for line in lines:
    if line.startswith("UPDATE: "):
        rest = line.replace("UPDATE: ", "").strip()
        parts = rest.split("::: ", 1)
        if len(parts) == 2:
            updates.append({
                "path": parts[0].strip(),
                "new_wtu": parts[1].strip()
            })

applied = 0
errors = 0

for u in updates:
    path = u["path"]
    new_wtu = u["new_wtu"]
    
    try:
        # Read the current content
        note_result = vault_read_note(path, max_lines=0)
        if isinstance(note_result, dict):
            content = note_result.get("content", "")
        else:
            content = str(note_result)
        
        # Check if when_to_use already exists
        wtu_match = re.search(r'^(when_to_use:\s*)(.*?)(\s*)$', content, re.MULTILINE)
        
        if wtu_match:
            # Replace existing when_to_use
            old_line = wtu_match.group(0)
            new_line = f'when_to_use: "{new_wtu}"'
            md_safe_replace(path, old_line, new_line)
        else:
            # Insert when_to_use after the description line (or after type line)
            # Find a good insertion point in the frontmatter
            if "description:" in content:
                old_anchor = re.search(r'^(description:.*?)(\s*)$', content, re.MULTILINE)
                if old_anchor:
                    old_line = old_anchor.group(0)
                    new_line = old_line + f'\nwhen_to_use: "{new_wtu}"'
                    md_safe_replace(path, old_line, new_line)
            elif "type: procedure" in content:
                old_anchor = re.search(r'^(type: procedure.*?)(\s*)$', content, re.MULTILINE)
                if old_anchor:
                    old_line = old_anchor.group(0)
                    new_line = old_line + f'\nwhen_to_use: "{new_wtu}"'
                    md_safe_replace(path, old_line, new_line)
        
        applied += 1
    except Exception as e:
        errors += 1
        print(f"ERROR updating {path}: {e}")

result = f"APPLIED: {applied}\nERRORS: {errors}\nTOTAL_CANDIDATES: {len(updates)}"
print(result)
```

[validate: contains "APPLIED:"]

---

### Step 4: Lint all updated procedures and produce report

```python
# Parse Step 3
lines = output.strip().split("\n")
applied = 0
for line in lines:
    if line.startswith("APPLIED: "):
        applied = int(line.replace("APPLIED: ", "").strip())
        break

# Re-parse Step 2 for the update list
lines2 = output.strip().split("\n")
updated_paths = []
for line in lines2:
    if line.startswith("UPDATE: "):
        rest = line.replace("UPDATE: ", "").strip()
        parts = rest.split("::: ", 1)
        if len(parts) == 2:
            updated_paths.append(parts[0].strip())

# Lint each
lint_results = []
for path in updated_paths:
    try:
        lint_result = vault_lint(path)
        if isinstance(lint_result, dict):
            broken = len(lint_result.get("broken_wikilinks", []))
            issues = len(lint_result.get("issues", []))
            lint_results.append({"path": path, "broken": broken, "issues": issues})
        else:
            lint_results.append({"path": path, "broken": 0, "issues": 0})
    except Exception as e:
        lint_results.append({"path": path, "broken": -1, "issues": -1, "error": str(e)})

# Report
report_lines = []
report_lines.append("## Dream-When-To-Use-Update Report")
report_lines.append("")
report_lines.append(f"**Procedures updated:** {applied}")
report_lines.append(f"**Errors:** {len([l for l in lint_results if l.get('broken', 0) < 0])}")
report_lines.append("")
report_lines.append("### Updated Procedures")
report_lines.append("")
for lr in lint_results:
    status = "✅" if lr.get("broken", 0) == 0 and lr.get("issues", 0) == 0 else "⚠️"
    report_lines.append(f"- {status} {lr['path']}")
report_lines.append("")
report_lines.append("### Next Steps")
report_lines.append("")
report_lines.append("- Re-test RAG retrieval for procedures that were updated")
report_lines.append("- If any procedures still don't surface, manually review their description field")

print("\n".join(report_lines))
```

[validate: contains "Dream-When-To-Use-Update Report"]

---

## Research Justification

1. **RAG retrieval depends on when_to_use**: The `when_to_use` frontmatter field is what RAG uses to match user queries to procedures. Missing or thin fields mean procedures are invisible to retrieval, no matter how good the procedure itself is.

2. **Self-improving feedback loop**: This procedure closes the gap between "a procedure exists" and "RAG can find it." When a procedure isn't surfaced, this procedure fixes the trigger language so next time it is.

3. **LLM for semantics, code for structure**: The LLM generates trigger language (semantic task), while code handles file I/O, frontmatter parsing, and linting (structural tasks). This follows [[Deterministic-Scaffolding-for-Small-Models]].

## Related

- [[Dream-Pass]] — parent orchestrator that calls this as a sub-procedure
- [[Dream-Evaluate]] — the step that identifies which procedures need when_to_use updates
- [[vault_lint]] — the linter that now checks for missing when_to_use fields on procedures
- [[Deterministic-Scaffolding-for-Small-Models]] — the design pattern this follows