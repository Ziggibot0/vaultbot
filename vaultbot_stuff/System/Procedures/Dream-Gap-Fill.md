---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-09
description: "Fill knowledge gaps by creating missing notes for dangling wikilinks that have no match in the vault. Takes gaps flagged by Dream-Dangle-Fix and generates stub notes grounded in related vault content."
when_to_use: as part of a Dream-Pass cycle after Dream-Dangle-Fix, or standalone when dangling links point to genuinely missing notes
applies_to:
  - vault
  - knowledge-gaps
  - dream-pass
allowed_tools:
  - vault_search
  - vault_read_note
  - vault_safe_write
  - llm_generate
falsifiable_if: it creates a note that fabricates content not grounded in vault knowledge, or creates a duplicate of an existing note
success_count: 0
failure_count: 0
success_rate: 0.0
summary: Dream-Gap-Fill
tags:
  - procedure
  - procedures
---

# Dream-Gap-Fill

Creates missing notes for dangling wikilinks that [[Dream-Dangle-Fix]] couldn't fuzzy-match to any existing vault note. These are genuine knowledge gaps — notes that are referenced but were never written.

## Inputs

- `gaps`: list of `{dangling, best_guess, best_score, referenced_by}` from Dream-Dangle-Fix (passed via `args` or `prior_results`)

## Step 1: Load gaps and gather context from referencing notes

1. ```python
import json, os, re

vault_path = os.environ.get("VAULT_PATH", ".")
_IGNORED_DIRS = {'.obsidian', '.git', 'vaultbot_backend', 'node_modules', '__pycache__', '.venv', 'trash'}

# --- Load gaps from args or prior_results ---
gaps = args.get("gaps", [])
if not gaps:
    for key, val in prior_results.items():
        if isinstance(val, str):
            try:
                d = json.loads(val)
                if "gaps_flagged" in d:
                    gaps = d["gaps_flagged"]
                    break
            except:
                pass

if not gaps:
    result = json.dumps({"status": "skipped", "reason": "no gaps provided"})
    print("No gaps to fill. Skipping.")
    final_output = {"status": "skipped", "reason": "no gaps"}
else:
    # --- Build a stem-to-path index ---
    stem_index = {}
    for root, dirs, files in os.walk(vault_path):
        dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
        for f in files:
            if f.endswith(".md"):
                stem = os.path.splitext(f)[0]
                stem_index[stem.lower()] = os.path.relpath(os.path.join(root, f), vault_path)
    
    # --- For each gap, read the referencing notes for context ---
    gap_contexts = []
    for gap in gaps[:10]:  # Cap at 10 per pass
        topic = gap.get("dangling", "")
        refs = gap.get("referenced_by", [])
        
        context_snippets = []
        for ref in refs:
            ref_stem = ref.lower().replace(" ", "-")
            if ref_stem in stem_index:
                ref_path = os.path.join(vault_path, stem_index[ref_stem])
                try:
                    with open(ref_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    # Extract body after frontmatter
                    if content.startswith("---"):
                        fm_end = content.find("---", 3)
                        if fm_end != -1:
                            content = content[fm_end+3:]
                    # Find the paragraph containing the dangling link
                    link_pattern = re.compile(rf'\[\[{re.escape(topic)}\]\]', re.IGNORECASE)
                    for match in link_pattern.finditer(content):
                        # Get surrounding paragraph
                        start = max(0, content.rfind('\n', 0, match.start()))
                        end = content.find('\n\n', match.end())
                        if end == -1:
                            end = len(content)
                        snippet = content[start:end].strip()[:500]
                        context_snippets.append({
                            "from_note": ref,
                            "snippet": snippet
                        })
                except:
                    pass
        
        gap_contexts.append({
            "topic": topic,
            "referenced_by": refs,
            "context": context_snippets
        })
    
    print(f"Loaded {len(gap_contexts)} gaps with context")
```

## Step 2: Generate stub notes for each gap using LLM

2. ```python
# --- For each gap, use LLM to generate a stub note ---
created_notes = []

for gc in gap_contexts[:5]:  # Cap at 5 LLM calls per pass
    topic = gc["topic"]
    refs = ", ".join(gc["referenced_by"])
    context_text = "\n".join(
        f"From {c['from_note']}: {c['snippet']}" 
        for c in gc["context"][:3]
    )
    
    prompt = f"""You are VaultBot filling a knowledge gap. A note titled "{topic}" is referenced by other vault notes but doesn't exist yet. Create a stub note for it.

Referenced by: {refs}

Context from referencing notes:
{context_text if context_text else "No context available — create a minimal stub based on the title alone."}

Instructions:
- Write a short note (150-300 words) that explains what this topic is
- Include YAML frontmatter with type: concept, status: stub, created: today
- Add a "## Related" section with wikilinks to the referencing notes
- If context is thin, mark clearly as a stub that needs expansion
- Do NOT fabricate facts — only write what can be inferred from the context above
- If you cannot infer anything, write: "This note is a placeholder. See [[{refs.split(',')[0].strip()}]] for context."

Return ONLY the markdown note content, starting with --- frontmatter."""
    
    note_content = llm_generate(prompt)
    created_notes.append({
        "topic": topic,
        "content": note_content
    })
    print(f"  Generated stub for: {topic}")

print(f"Generated {len(created_notes)} stub notes")
```

## Step 3: Write stub notes to the vault

3. ```python
# --- Write each generated note to the vault ---
written = []
for cn in created_notes:
    topic = cn["topic"]
    content = cn["content"]
    
    # Sanitize filename
    safe_name = re.sub(r'[<>:"/\\|?*]', '-', topic)[:80]
    note_path = os.path.join(vault_path, "vaultbot_stuff", "Knowledge", "Concepts", f"{safe_name}.md")
    
    # Check if note already exists (race condition guard)
    if os.path.exists(note_path):
        print(f"  Skipped (already exists): {safe_name}")
        continue
    
    try:
        with open(note_path, 'w', encoding='utf-8') as f:
            f.write(content)
        written.append({"topic": topic, "path": os.path.relpath(note_path, vault_path)})
        print(f"  Written: {safe_name}")
    except Exception as e:
        print(f"  Failed to write {safe_name}: {e}")

result = json.dumps({
    "status": "completed",
    "gaps_total": len(gaps),
    "stubs_generated": len(created_notes),
    "stubs_written": len(written),
    "written_notes": written,
}, indent=2)
```

## Step 4: Validate

4. [validate: contains "stubs_written"]
