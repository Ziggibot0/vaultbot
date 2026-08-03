---
type: procedure
status: experimental
model_cartridge: big
created: 2026-08-02
description: "Consolidate vault patterns and gaps into semantic knowledge notes. Reads journal themes from Dream-Scan, graph gaps from Dream-Analyze, and existing semantic notes for dedup. Uses LLM to synthesize new semantic notes. The most complex dream sub-procedure."
when_to_use: "as part of Dream-Pass, or standalone when consolidating memories"
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

# --- Find existing semantic notes for dedup ---
existing_semantic = []
for root, dirs, files in os.walk(vault_path):
    dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
    for f in files:
        if not f.endswith(".md"):
            continue
        full_path = os.path.join(root, f)
        try:
            with open(full_path, encoding='utf-8') as fh:
                content = fh.read(500)
            if re.search(r'type:\s*semantic', content):
                existing_semantic.append(os.path.splitext(f)[0])
        except:
            pass

# --- Find exemplar semantic note for formatting ---
exemplar_content = ""
exemplar_candidates = [
    "VaultBot-Is-the-Vault.md",
    "Vault-Longevity-Architecture.md",
    "Semantic-Consolidation-Architecture.md",
]
for ec in exemplar_candidates:
    p = os.path.join(vault_path, ec)
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            exemplar_content = f.read()
        break

# --- Collect dangling wikilinks as consolidation targets ---
all_gaps = graph_data.get("dangling_links", [])
# Also search for thin notes
thin_notes = graph_data.get("thin_notes", [])

result = json.dumps({
    "journal_themes": journal_themes[:20],
    "existing_semantic": existing_semantic[:100],
    "dangling_links": all_gaps[:50],
    "thin_notes": thin_notes[:20],
    "exemplar_found": bool(exemplar_content),
    "exemplar_preview": exemplar_content[:3000] if exemplar_content else "",
})
```

## Step 2: Synthesize Semantic Notes via LLM

Send all gathered patterns to the LLM with the exemplar format and dedup list. The LLM writes semantic notes; the code writes them to disk and lints them.

2. ```python
import json, os, re

vault_path = os.environ.get("VAULT_PATH", ".")

# Parse prior step output
step1 = json.loads(prior_results.get("Dream-Consolidate-step1", prior_results.get("step1", "{}")))
if not step1:
    # Try to parse from the last result
    try:
        step1 = json.loads(result) if result else {}
    except:
        step1 = {}

journal_themes = step1.get("journal_themes", [])
existing_semantic = step1.get("existing_semantic", [])
dangling_links = step1.get("dangling_links", [])
exemplar_preview = step1.get("exemplar_preview", "")

# Build LLM prompt
prompt_parts = [
    "You are VaultBot's memory consolidation system. Your job is to synthesize",
    "semantic knowledge notes from the patterns and gaps below.",
    "",
    "## Exemplar Format (follow this structure exactly)",
    exemplar_preview if exemplar_preview else "(no exemplar found — use standard research note format with YAML frontmatter)",
    "",
    "## Existing Semantic Notes (for dedup — do NOT duplicate these)",
    json.dumps(existing_semantic, indent=2),
    "",
    "## Journal Themes (from recent journal entries)",
    json.dumps(journal_themes[:10], indent=2, default=str) if journal_themes else "[]",
    "",
    "## Dangling Wikilinks (topics that need notes written)",
    json.dumps(dangling_links[:30], indent=2, default=str) if dangling_links else "[]",
    "",
    "## Instructions",
    "1. Review ALL gaps below. Group related gaps together.",
    "2. For each group, write a semantic note that captures the pattern/lesson.",
    "3. Each note must have:",
    "   - YAML frontmatter with type: semantic, status: raw, created: today's date, tags",
    "   - A clear title as H1",
    "   - Summary section",
    "   - Key Findings section with evidence",
    "   - Related section with wikilinks to existing notes (use [[Note-Name]] format)",
    "4. CRITICAL: In the Related section, write a full sentence explaining each wikilink",
    "   relationship. Never dump bare links. Format: - [[Note-Name]] -- explains why related",
    "5. Do NOT include .md extensions in wikilinks. Use [[Note-Name]] not [[Note-Name.md]]",
    "6. Only link to notes that actually exist in the vault. If unsure, omit the link.",
    "",
    "Output each note as:",
    "### NOTE: [Note-Title]",
    "[full note content including YAML frontmatter]",
    "",
    "### NOTE: [Next-Title]",
    "[next note content]",
]

prompt = "\n".join(prompt_parts)

# Call LLM
llm_result = llm_generate(prompt)

# Parse LLM output for NOTE: blocks
notes_written = []
notes_skipped = []
lint_issues = []

note_pattern = re.compile(r'### NOTE:\s*(.+?)\n(.*?)(?=### NOTE:|$)', re.DOTALL)
for m in note_pattern.finditer(llm_result):
    title = m.group(1).strip()
    note_content = m.group(2).strip()

    # Clean up wikilinks — remove .md extensions
    def _clean_wikilinks(text):
        wl_pat = re.compile(r'\[\[([^\]]+)\.md\]\]')
        def _replacer(m):
            return f"[[{m.group(1)}]]"
        cleaned = wl_pat.sub(_replacer, text)
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        return cleaned

    note_content = _clean_wikilinks(note_content)

    note_path = os.path.join(vault_path, f"{title}.md")
    with open(note_path, 'w', encoding='utf-8') as f:
        f.write(note_content)
    notes_written.append(title)

    # Lint
    rel_path = f"{title}.md"
    try:
        lint_result = vault_lint(rel_path)
        if lint_result.get("broken_wikilinks"):
            lint_issues.append({
                "note": title,
                "broken": lint_result["broken_wikilinks"],
            })
    except:
        pass

result = json.dumps({
    "status": "consolidated",
    "notes_written": notes_written,
    "notes_skipped": notes_skipped,
    "lint_issues": lint_issues,
    "total_gaps_processed": len(dangling_links),
}, indent=2)
```

## Usage

This sub-procedure is called by [[Dream-Pass]] as step 4 of 7. It can also be run standalone when you want to consolidate memories without a full dream cycle. Requires [[Dream-Scan]] and [[Dream-Analyze]] to have run first (their outputs are consumed as inputs).