---
type: procedure
status: active
baseline: true
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
  - vault_gaps
  - llm_generate
provides:
  - Pattern-Scan
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

## Why This Exists

Dangling wikilinks (links to notes that don't exist) need triage before the
researcher can act on them. This procedure classifies each into one of four
categories so the autonomous researcher knows exactly what to do. The
tradeoff: it is pure classification over a bounded set of categories, so the
small model handles it without big-model reasoning.

## Steps

### Step 1: Get dangling wikilinks from vault_gaps

1. ```python
import json

gaps_data = vault_gaps()
dangling_links = []
for gap in gaps_data.get("gaps", []):
    # vault_gaps returns gaps with kind="dangling_link" and a "topic" field
    # (NOT "name"). Other gap kinds (thin_notes, etc.) have different shapes
    # — we only want the dangling wikilinks.
    if gap.get("kind") == "dangling_link" and gap.get("topic"):
        dangling_links.append({
            "target": gap["topic"],
            "source_notes": gap.get("referenced_by", []),
            "reference_count": gap.get("reference_count", 0),
        })

result = {"dangling_links": dangling_links, "count": len(dangling_links)}
```

### Step 2: Get the list of all existing note titles for typo/alias detection

2. ```python
import json
from pathlib import Path

# prior_results[0] is the JSON string from step 1
step1 = json.loads(prior_results[0])
dangling_links = step1["dangling_links"]

# vault_list() returns {"count": N, "files": [...]} — extract the list
all_notes_data = vault_list()
all_notes = all_notes_data.get("files", []) if isinstance(all_notes_data, dict) else all_notes_data
all_titles = [Path(n).stem for n in all_notes]

# Build a lookup of lowercased, hyphen-normalized titles for fuzzy matching
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

result = {
    "dangling_links": dangling_links,
    "typo_candidates": len(typo_candidates),
    "unresolved": len(dangling_links) - len(typo_candidates),
}
```

### Step 3: Small model classifies each dangling link

3. [llm: You are a triage classifier. The prior step output contains a list of dangling wikilinks (each with "target", "source_notes", "fuzzy_matches"). For each dangling wikilink, classify it into exactly one category:

- **research_needed**: The link refers to a real concept, topic, or entity that should have its own note in the vault. It's not a typo and no existing note covers it.
- **typo_alias**: The link is a misspelling or alternate name for an existing note (check fuzzy_matches).
- **chat_log**: The link looks like it refers to a chat conversation (contains "chat-" or a date pattern).
- **ignore**: The link is a passing reference to something that doesn't need its own note.

Output format: one line per link, `TARGET | CATEGORY | BRIEF_REASON`]

### Step 4: Format the triage report

4. ```python
import json, re

# prior_results[1] is step 2's JSON (dangling_links with fuzzy matches)
# prior_results[2] is step 3's raw LLM text output
step2 = json.loads(prior_results[1])
dangling_links = step2["dangling_links"]
llm_output = prior_results[2]

categories = {"research_needed": [], "typo_alias": [], "chat_log": [], "ignore": []}

for line in llm_output.strip().split("\n"):
    parts = line.split(" | ")
    if len(parts) >= 3:
        target = parts[0].strip()
        category = parts[1].strip().strip("*")
        reason = parts[2].strip()
        if category in categories:
            entry = {"target": target, "reason": reason}
            # Enrich with source info
            for dl in dangling_links:
                if dl["target"] == target:
                    entry["source_notes"] = dl.get("source_notes", [])
                    if dl.get("fuzzy_matches"):
                        entry["fuzzy_matches"] = dl["fuzzy_matches"]
                    break
            categories[category].append(entry)

result = {
    "summary": {
        "total_dangling": len(dangling_links),
        "research_needed": len(categories["research_needed"]),
        "typo_alias": len(categories["typo_alias"]),
        "chat_log": len(categories["chat_log"]),
        "ignore": len(categories["ignore"]),
    },
    "triage": categories,
}
```

### Step 5: Output the prioritized work queue

5. [llm: Format the triage report into a concise actionable summary for the operator or autonomous researcher. Lead with counts, then list the top 5 research_needed items with their source notes and reasons. Keep it under 200 words.]


## Related

- [[Find-Broken-Links]] — finds dangling wikilinks but doesn't triage them; Gap-Fill adds the classification layer
- [[Vault-Gaps]] — checks for dangling wikilinks and thin notes; Gap-Fill provides actionable triage on the dangling links it finds
- [[Procedure-Expansion-Proposal]] — Gap-Fill was proposed as Tier 1 because triage is pure classification, which means the small model handles it without big-model reasoning
- [[Tiny-LLM-Use-Cases-Mapping-to-VaultBot-Procedure-Cartridge]] — classification over a bounded set of categories is the highest-confidence small-model use case, therefore Gap-Fill's LLM step is safely small-cartridge
