---
type: procedure
status: experimental
baseline: true
model_cartridge: big
created: 2026-08-02
description: Consolidate vault patterns and gaps into semantic knowledge notes. Reads journal themes from Dream-Scan, graph gaps from Dream-Analyze, and existing semantic notes for dedup. Uses LLM to synthesize new semantic notes. The most complex dream sub-procedure.
when_to_use: as part of Dream-Pass, or standalone when consolidating memories
applies_to:
  - vault
  - memory
  - consolidation
allowed_tools:
  - vault_search
  - vault_list
  - vault_lint
  - code_read
  - llm_generate
  - run_procedure
falsifiable_if: it produces duplicate semantic notes, fabricates content not grounded in journal themes, or crashes on empty theme input
summary: Dream-Consolidate
tags:
  - procedure
  - procedures
---

# Dream-Consolidate

Consolidates patterns from journal themes, graph gaps, and quality modules into permanent semantic notes. This is the core memory-writing step of the dream pass — the LLM synthesizes, everything else is deterministic.

## Step 1: Gather Inputs

Collect themes from Dream-Scan's output file, graph gaps from Dream-Analyze, existing semantic notes for dedup, and an exemplar note for formatting guidance.

1. ```python
import json, os, re

vault_path = os.environ.get("VAULT_PATH", ".")
_IGNORED_DIRS = {'.obsidian', '.git', 'vaultbot_backend', 'node_modules', '__pycache__', '.venv', 'trash'}

# --- Load journal themes from Dream-Scan ---
themes_path = os.path.join(vault_path, "vaultbot_backend", "_dream_pass_themes.json")
journal_themes = []
if os.path.exists(themes_path):
    with open(themes_path, encoding='utf-8') as f:
        journal_themes = json.load(f)

# --- Load graph analysis from Dream-Analyze (via prior_results) ---
graph_data = {}
try:
    graph_data = json.loads(prior_results.get("Dream-Analyze", "{}"))
except:
    pass

# --- Collect existing semantic notes for dedup ---
semantic_notes = []
for root, dirs, files in os.walk(vault_path):
    dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
    for fname in files:
        if not fname.endswith('.md'):
            continue
        fpath = os.path.join(root, fname)
        try:
            with open(fpath, encoding='utf-8') as f:
                content = f.read()
            if content.startswith('---'):
                # Extract frontmatter
                fm_end = content.find('---', 3)
                if fm_end > 0:
                    fm = content[3:fm_end]
                    if 'type: research' in fm or 'type: semantic' in fm:
                        title = fname.replace('.md', '')
                        # Get first 200 chars of body for dedup comparison
                        body_preview = content[fm_end+3:fm_end+203].strip()
                        semantic_notes.append({
                            "title": title,
                            "path": fpath,
                            "preview": body_preview
                        })
        except:
            continue

# --- Load exemplar note for formatting guidance ---
exemplar_path = os.path.join(vault_path, "vaultbot", "Knowledge", "Research", "python-108dates-and-times-L1.md")
exemplar_content = ""
if os.path.exists(exemplar_path):
    with open(exemplar_path, encoding='utf-8') as f:
        exemplar_content = f.read()[:500]

print(f"Loaded {len(journal_themes)} journal themes, {len(semantic_notes)} existing semantic notes")
print(f"Graph data keys: {list(graph_data.keys()) if graph_data else 'none'}")
print(f"Exemplar loaded: {bool(exemplar_content)}")
```

2. If no journal themes and no graph gaps, skip consolidation: `print("No new material to consolidate. Skipping.")` and set `final_output = {"status": "skipped", "reason": "no input data"}`.

## Step 2: Identify Consolidation Targets

Analyze the gathered data to find patterns worth consolidating into semantic notes.

1. ```python
# --- Extract dangling links from graph data ---
dangling_links = graph_data.get("dangling_links", [])
isolated_nodes = graph_data.get("isolated_nodes", [])

# --- Extract themes that appear across multiple journals ---
theme_groups = {}
for entry in journal_themes:
    for theme in entry.get("themes", []):
        t = theme.lower().strip()
        if t not in theme_groups:
            theme_groups[t] = []
        theme_groups[t].append(entry.get("date", "unknown"))

# --- Filter to themes appearing in 2+ journals (consolidation candidates) ---
consolidation_candidates = [
    {"theme": t, "dates": dates}
    for t, dates in theme_groups.items()
    if len(dates) >= 2
]

# --- Also include dangling links as consolidation targets ---
for dl in dangling_links[:10]:
    consolidation_candidates.append({
        "theme": f"Dangling link: {dl}",
        "dates": ["graph"]
    })

# --- Also include isolated nodes ---
for node in isolated_nodes[:5]:
    consolidation_candidates.append({
        "theme": f"Isolated node: {node}",
        "dates": ["graph"]
    })

print(f"Found {len(consolidation_candidates)} consolidation candidates")
for c in consolidation_candidates[:10]:
    print(f"  - {c['theme']} (sources: {c['dates']})")
```

## Step 3: Generate Semantic Notes (LLM)

For each consolidation candidate, use the LLM to synthesize a new semantic note — but only if no existing note covers the same topic.

1. ```python
# --- Check each candidate against existing semantic notes ---
new_notes_needed = []
for candidate in consolidation_candidates:
    theme = candidate["theme"]
    # Simple dedup: check if any existing note title contains the theme keywords
    keywords = [w for w in re.split(r'[\s:]+', theme) if len(w) > 3]
    is_duplicate = False
    for existing in semantic_notes:
        existing_lower = existing["title"].lower()
        if any(kw.lower() in existing_lower for kw in keywords):
            is_duplicate = True
            break
    if not is_duplicate:
        new_notes_needed.append(candidate)

print(f"After dedup: {len(new_notes_needed)} new notes needed")
```

2. For each new note needed, use `[llm:]` to generate the note content:

   ```llm
You are VaultBot, a research agent in an Obsidian vault. Create a semantic knowledge note for the following theme:

Theme: {{theme}}
Source dates: {{dates}}
Exemplar format (follow this structure):
{{exemplar_content}}

Instructions:
- Write a self-contained argument: claim + reasoning + connections
- Use wikilinks [[like-this]] to connect to related vault concepts
- Include frontmatter with type: research, status: raw, created: today's date
- Keep it concise (200-400 words)
- Ground every claim in the journal themes — do not fabricate
   ```

3. ```python
# --- Save each generated note ---
import datetime

saved_notes = []
for i, note_data in enumerate(llm_results):
    # Parse LLM output and save
    title = note_data.get("title", f"Consolidated-{i}")
    content = note_data.get("content", "")
    
    # Sanitize title for filename
    safe_title = re.sub(r'[^\w\-]', '-', title)[:80]
    note_path = os.path.join(vault_path, "vaultbot", "Knowledge", "Research", f"{safe_title}.md")
    
    # Write the note
    with open(note_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    saved_notes.append({"title": safe_title, "path": note_path})
    print(f"Saved: {safe_title}")

print(f"Total notes saved: {len(saved_notes)}")
```

## Step 4: Validate and Report

Run vault_lint on each new note and report the results.

1. ```python
# --- Validate each saved note ---
validation_results = []
for note in saved_notes:
    # vault_lint will be called by the procedure runner
    validation_results.append({
        "title": note["title"],
        "path": note["path"],
        "status": "pending_validation"
    })

final_output = {
    "status": "completed",
    "notes_created": len(saved_notes),
    "notes_validated": len(validation_results),
    "candidates_total": len(consolidation_candidates),
    "candidates_after_dedup": len(new_notes_needed),
    "details": saved_notes
}
print(json.dumps(final_output, indent=2))
```

2. Call `vault_lint` on each newly created note to verify quality (broken wikilinks, missing frontmatter, argument quality).

## Notes

- This is the only sub-procedure that uses the **big** model cartridge — all reasoning lives here.
- The dedup check is intentionally simple (keyword overlap). If it misses duplicates, [[Dream-Evaluate]] will catch them in the next cycle and flag them for review.
- If the LLM fabricates content not grounded in journal themes, the `falsifiable_if` condition is triggered and the note should be flagged for review.