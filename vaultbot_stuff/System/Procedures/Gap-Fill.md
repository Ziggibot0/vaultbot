---
type: procedure
status: active
model_cartridge: small
created: 2026-08-03
description: "Triage dangling wikilinks into actionable categories: 'research needed' (concept has no note and should be created), 'typo/alias' (note exists under a slightly different title), 'chat log' (link points at a chat that wasn't saved), or 'ignore' (intentional reference to a concept that doesn't need its own note). Returns a prioritized work queue for the autonomous researcher or the big model. Small cartridge — pure classification over a bounded set of categories."
when_to_use: when the vault has dangling wikilinks that need triage, before running autonomous research, or when asked 'what gaps should we fill?'
falsifiable_if: it classifies a link as 'typo' when the target note genuinely doesn't exist, or marks 'research needed' for a link that already resolves to an existing note
applies_to:
  - gap-detection
  - vault-maintenance
  - research-routing
  - autonomous-research
allowed_tools:
  - run_procedure
  - vault_list
  - llm_generate
summary: Gap-Fill
tags:
  - procedure
  - procedures
---

# Gap-Fill

## When to Run This

Run when the vault has dangling wikilinks (links pointing at notes that
don't exist). This procedure triages each one into one of four categories so
the autonomous researcher (or big model) knows exactly what to do:

1. **research_needed** — a real concept that should have a note. Route to
   the autonomous researcher.
2. **typo_alias** — the note exists under a slightly different title.
   Fix the wikilink, don't create a new note.
3. **chat_log** — link points at a chat conversation that wasn't saved
   as a note. Either save it or remove the link.
4. **ignore** — intentional reference to a concept that doesn't need
   its own note (e.g., a passing mention in prose).

The small model does the classification — it sees the link text, the
context sentence, and the list of existing note titles, then picks a
category. No research, no synthesis, just routing.

## Steps

### Step 1: Run Pattern-Scan to get dangling wikilinks

1. ```python
import json

run_procedure("Pattern-Scan")
out_file = str(Path(vault_path) / "vaultbot_stuff" / "Memory" / "Build-Log" / "pattern-scan-output.json")
with open(out_file, "r", encoding="utf-8") as f:
    scan = json.load(f)

# Extract notes with unresolved outgoing links
notes_with_dangling = []
for note in scan.get("notes", []):
    unresolved = note.get("unresolved_out", [])
    if unresolved:
        notes_with_dangling.append({
            "source": note.get("path"),
            "dangling_targets": unresolved,
            "title": note.get("title", note.get("path"))
        })

# Flatten into individual dangling links with context
dangling_links = []
for n in notes_with_dangling:
    for target in n["dangling_targets"]:
        dangling_links.append({
            "target": target,
            "source_note": n["source"],
            "source_title": n["title"]
        })

print(f"Found {len(dangling_links)} dangling wikilinks across {len(notes_with_dangling)} notes.")
```

### Step 2: Get the list of all existing note titles for typo/alias detection

2. ```python
all_notes = vault_list()
all_titles = [Path(n).stem for n in all_notes]

# Build a lookup of lowercased, hyphen-normalized titles for fuzzy matching
import unicodedata
def normalize_title(t):
    t = t.lower().replace("-", " ").replace("_", " ").strip()
    return t

normalized_titles = {normalize_title(t): t for t in all_titles}

# For each dangling link, check if a close match exists
typo_candidates = []
for dl in dangling_links:
    target_norm = normalize_title(dl["target"])
    if target_norm in normalized_titles:
        dl["fuzzy_match"] = normalized_titles[target_norm]
        typo_candidates.append(dl)
    else:
        # Check for partial matches (target is substring or vice versa)
        partials = [t for t in all_titles if target_norm in normalize_title(t) or normalize_title(t) in target_norm]
        if partials:
            dl["fuzzy_matches"] = partials[:3]
        else:
            dl["fuzzy_matches"] = []

print(f"Typo/alias candidates: {len(typo_candidates)}")
print(f"Unresolved (need LLM classification): {len(dangling_links) - len(typo_candidates)}")
```

### Step 3: Small model classifies each dangling link

3. [llm: You are a triage classifier. For each dangling wikilink below, classify it into exactly one category:

- **research_needed**: The link refers to a real concept, topic, or entity that should have its own note in the vault. It's not a typo and no existing note covers it.
- **typo_alias**: The link is a misspelling or alternate name for an existing note (fuzzy matches provided).
- **chat_log**: The link looks like it refers to a chat conversation (contains "chat-" or a date pattern).
- **ignore**: The link is a passing reference to something that doesn't need its own note (e.g., a person mentioned once, a generic concept).

Output format: one line per link, `TARGET | CATEGORY | BRIEF_REASON`

Dangling links to classify:
{json.dumps(dangling_links, indent=2)}]

### Step 4: Format the triage report

4. ```python
# Parse the LLM classification output
import re

categories = {"research_needed": [], "typo_alias": [], "chat_log": [], "ignore": []}

for line in llm_output.strip().split("\n"):
    parts = line.split(" | ")
    if len(parts) >= 3:
        target, category, reason = parts[0].strip(), parts[1].strip().strip("*"), parts[2].strip()
        if category in categories:
            entry = {"target": target, "reason": reason}
            # Enrich with source info
            for dl in dangling_links:
                if dl["target"] == target:
                    entry["source_note"] = dl["source_note"]
                    if dl.get("fuzzy_matches"):
                        entry["fuzzy_matches"] = dl["fuzzy_matches"]
                    break
            categories[category].append(entry)

report = {
    "summary": {
        "total_dangling": len(dangling_links),
        "research_needed": len(categories["research_needed"]),
        "typo_alias": len(categories["typo_alias"]),
        "chat_log": len(categories["chat_log"]),
        "ignore": len(categories["ignore"])
    },
    "triage": categories
}

print(json.dumps(report, indent=2))
```

### Step 5: Output the prioritized work queue

5. [llm: Format the triage report into a concise actionable summary for the operator or autonomous researcher. Lead with counts, then list the top 5 research_needed items with their source notes and reasons. Keep it under 200 words.]


## Related

- [[Find-Broken-Links]] — finds dangling wikilinks but doesn't triage them; Gap-Fill adds the classification layer
- [[Vault-Gaps]] — checks for dangling wikilinks and thin notes; Gap-Fill provides actionable triage on the dangling links it finds
- [[Procedure-Expansion-Proposal]] — Gap-Fill was proposed as Tier 1 because triage is pure classification, which means the small model handles it without big-model reasoning
- [[Tiny-LLM-Use-Cases-Mapping-to-VaultBot-Procedure-Cartridge]] — classification over a bounded set of categories is the highest-confidence small-model use case, therefore Gap-Fill's LLM step is safely small-cartridge
