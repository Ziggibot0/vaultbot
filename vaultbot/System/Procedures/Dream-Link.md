---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-02
description: Connect orphaned notes into the vault graph by finding semantically related notes and adding wikilinks. Takes graph analyzer output (with isolated_nodes) as input via args.
when_to_use: As part of a Dream-Pass cycle after Dream-Analyze, or independently to connect orphan notes.
applies_to:
  - vault
  - graph
  - linking
  - orphans
allowed_tools:
  - vault_search
  - code_read
  - llm_generate
falsifiable_if: it adds broken wikilinks, links to non-existent notes, or crashes on large orphan lists
success_count: 0
failure_count: 0
success_rate: 0.0
summary: 1. This note describes how the Dream-Link tool identifies orphaned notes from graph analyzer output and adds wikilinks by finding semantically related vault stems, stopping after processing up to 50 n
tags:
  - procedure
  - procedures
---

# Dream-Link

Connects orphaned notes (from graph analyzer output) into the vault graph by finding semantically related notes and adding wikilinks.

## Step 1: Find and add links for orphaned notes

1. ```python
import json, os, re

vault_path = os.environ.get("VAULT_PATH", ".")
_IGNORED_DIRS = {'.obsidian', '.git', 'vaultbot_backend', 'node_modules', '__pycache__', '.venv', 'trash'}

# Get isolated nodes from args (passed by orchestrator) or from prior_results
graph_data = args.get("graph_data", "")
if not graph_data and len(prior_results) > 0:
    graph_data = prior_results[0]
try:
    _step1_data = json.loads(graph_data) if isinstance(graph_data, str) else graph_data
except:
    _step1_data = {}
isolated = _step1_data.get("isolated_nodes", [])

# --- Build a set of all vault note stems for link validation ---
all_vault_stems = set()
for root, dirs, files in os.walk(vault_path):
    dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
    for f in files:
        if f.endswith(".md"):
            all_vault_stems.add(os.path.splitext(f)[0])

# --- For each isolated node, find semantic matches ---
links_added = 0
links_skipped = 0
link_log = []

for node in isolated[:50]:  # Cap at 50 to avoid timeout
    stem = os.path.splitext(os.path.basename(node))[0]
    if stem.startswith("chat-") or stem.startswith("Chat-"):
        links_skipped += 1
        continue
    if stem in ["SECURITY", "README", "LICENSE"]:
        links_skipped += 1
        continue

    full_path = node if os.path.isabs(node) else os.path.join(vault_path, node)
    if not os.path.exists(full_path):
        links_skipped += 1
        continue

    try:
        with open(full_path, encoding='utf-8') as f:
            content = f.read()
    except:
        links_skipped += 1
        continue

    # Build query from title and first 200 chars
    query = stem.replace("-", " ")
    if content:
        # Extract text after frontmatter
        body = content
        if body.startswith("---"):
            end = body.find("---", 3)
            if end != -1:
                body = body[end+3:]
        query = query + " " + body[:200].replace("\n", " ").strip()

    try:
        results = vault_search(query, k=5)
    except:
        links_skipped += 1
        continue

    # Filter results: must exist, not be the note itself, not already linked
    candidates = []
    existing_links = set(re.findall(r'\[\[([^\]]+)\]\]', content))
    for r in results:
        r_stem = os.path.splitext(os.path.basename(r.get("file_path", "")))[0]
        if r_stem and r_stem != stem and r_stem in all_vault_stems and r_stem not in existing_links:
            candidates.append(r_stem)

    if candidates[:3]:
        # Add a Related section with wikilinks
        related_section = "\n\n## Related\n"
        for c in candidates[:3]:
            related_section += f"- [[{c}]]\n"
        with open(full_path, 'a', encoding='utf-8') as f:
            f.write(related_section)
        links_added += len(candidates[:3])
        link_log.append({"note": stem, "linked_to": candidates[:3]})
    else:
        links_skipped += 1

result = json.dumps({
    "links_added": links_added,
    "links_skipped": links_skipped,
    "link_log": link_log[:20],
})
```