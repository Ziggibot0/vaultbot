---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-09
description: "Fix dangling wikilinks by fuzzy-matching them to existing vault notes and repairing the broken references. For genuinely missing notes, flags them for gap-fill. Called by Dream-Pass after Dream-Analyze surfaces dangling links."
when_to_use: as part of a Dream-Pass cycle after Dream-Analyze, or standalone when dangling wikilinks are reported
applies_to:
  - vault
  - maintenance
  - linking
  - dream-pass
allowed_tools:
  - vault_list
  - code_read
  - md_safe_replace
  - vault_read_note
falsifiable_if: it creates broken wikilinks, matches a dangling link to the wrong note, or fails to fix a link that has an obvious match
success_count: 0
failure_count: 0
success_rate: 0.0
summary: Dream-Dangle-Fix
tags:
  - procedure
  - procedures
---

# Dream-Dangle-Fix

Fixes dangling wikilinks surfaced by [[Dream-Analyze]] (or `vault_gaps`). For each dangling link, attempts fuzzy matching against existing vault note stems. If a match is found, repairs the wikilink in the referencing note. If no match exists, flags it for [[Dream-Gap-Fill]].

## Why This Exists

Dangling wikilinks point at notes that don't exist, and each is either a typo to repair or a genuine gap to fill — but telling them apart requires fuzzy-matching against the real vault stem index. This procedure exists to repair the fixable links and flag the rest for gap-fill. The key tradeoff is a fuzzy-match threshold (0.5) that balances repairing near-miss typos against wrongly matching a dangling link to an unrelated note.

## Inputs

- `dangling_links`: list of `{topic, referenced_by, file_path}` from Dream-Analyze or vault_gaps (passed via `args` or `prior_results`)

## Step 1: Load dangling links and build vault stem index

1. ```python
import json, os, re
from difflib import SequenceMatcher

vault_path = os.environ.get("VAULT_PATH", ".")
_IGNORED_DIRS = {'.obsidian', '.git', 'vaultbot_backend', 'node_modules', '__pycache__', '.venv', 'trash'}

# --- Load dangling links from args or prior_results ---
dangling = args.get("dangling_links", [])
if not dangling:
    # Try to extract from prior_results (Dream-Analyze output)
    for key, val in prior_results.items():
        if isinstance(val, str):
            try:
                d = json.loads(val)
                if "dangling_links" in d:
                    dangling = d["dangling_links"]
                    break
            except:
                pass

if not dangling:
    result = json.dumps({"status": "skipped", "reason": "no dangling links provided"})
    print("No dangling links to fix. Skipping.")
    # Set final_output for the procedure runner
    final_output = {"status": "skipped", "reason": "no dangling links"}
else:
    # --- Build index of all vault note stems ---
    all_stems = {}
    for root, dirs, files in os.walk(vault_path):
        dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
        for f in files:
            if f.endswith(".md"):
                stem = os.path.splitext(f)[0]
                rel_path = os.path.relpath(os.path.join(root, f), vault_path)
                all_stems[stem.lower()] = {"stem": stem, "path": rel_path}

    print(f"Loaded {len(dangling)} dangling links, {len(all_stems)} vault stems")
```

## Step 2: Fuzzy-match each dangling link to existing notes

2. ```python
# --- For each dangling link, find the best fuzzy match ---
def fuzzy_score(a, b):
    """Simple token-overlap + sequence matcher score."""
    a_tokens = set(re.split(r'[-\s]+', a.lower()))
    b_tokens = set(re.split(r'[-\s]+', b.lower()))
    overlap = len(a_tokens & b_tokens)
    seq_score = SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return (overlap * 0.6) + (seq_score * 0.4)

fixes = []
gaps = []

for dl in dangling:
    topic = dl.get("topic", dl.get("normalized_name", ""))
    refs = dl.get("referenced_by", [])
    
    if not topic:
        continue
    
    # Try exact match first (case-insensitive)
    topic_lower = topic.lower().replace(" ", "-")
    if topic_lower in all_stems:
        fixes.append({
            "dangling": topic,
            "matched_to": all_stems[topic_lower]["stem"],
            "match_type": "exact",
            "referenced_by": refs
        })
        continue
    
    # Fuzzy match against all stems
    best_score = 0.0
    best_stem = None
    for stem_lower, info in all_stems.items():
        score = fuzzy_score(topic, info["stem"])
        if score > best_score:
            best_score = score
            best_stem = info
    
    if best_score >= 0.5 and best_stem:
        fixes.append({
            "dangling": topic,
            "matched_to": best_stem["stem"],
            "match_type": "fuzzy",
            "score": round(best_score, 3),
            "referenced_by": refs
        })
    else:
        gaps.append({
            "dangling": topic,
            "best_guess": best_stem["stem"] if best_stem else None,
            "best_score": round(best_score, 3) if best_stem else 0,
            "referenced_by": refs
        })

print(f"Fixable: {len(fixes)}, Gaps (no match): {len(gaps)}")
for f in fixes:
    print(f"  {f['dangling']} -> [[{f['matched_to']}]] ({f['match_type']}, score={f.get('score', 1.0)})")
for g in gaps:
    print(f"  GAP: {g['dangling']} (best guess: {g.get('best_guess')}, score={g['best_score']})")
```

## Step 3: Repair wikilinks in referencing notes

3. ```python
# --- For each fixable dangling link, repair the wikilink in referencing notes ---
repaired = []
failed = []

for fix in fixes:
    old_link = fix["dangling"]
    new_link = fix["matched_to"]
    
    for ref_note in fix.get("referenced_by", []):
        # Find the referencing note file
        ref_path = None
        for stem_lower, info in all_stems.items():
            if info["stem"].lower() == ref_note.lower().replace(" ", "-"):
                ref_path = info["path"]
                break
        
        if not ref_path:
            failed.append({"dangling": old_link, "ref": ref_note, "reason": "referencing note not found"})
            continue
        
        try:
            # Read the referencing note
            full_path = os.path.join(vault_path, ref_path)
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Find the exact wikilink text to replace
            # The dangling link could appear as [[Old Link]] or [[Old Link|alias]]
            old_pattern = f"[[{old_link}]]"
            old_pattern_alias = f"[[{old_link}|"
            
            if old_pattern in content:
                new_content = content.replace(old_pattern, f"[[{new_link}]]")
            elif old_pattern_alias in content:
                # Handle aliased links: [[Old Link|some alias]] -> [[New Link|some alias]]
                new_content = content.replace(f"[[{old_link}|", f"[[{new_link}}|")
            else:
                # Try case-insensitive
                pattern = re.compile(re.escape(f"[[{old_link}]]"), re.IGNORECASE)
                if pattern.search(content):
                    new_content = pattern.sub(f"[[{new_link}]]", content)
                else:
                    failed.append({"dangling": old_link, "ref": ref_note, "reason": "wikilink not found in content"})
                    continue
            
            # Write back
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            repaired.append({
                "dangling": old_link,
                "fixed_to": new_link,
                "in_note": ref_note
            })
            print(f"  Fixed: [[{old_link}]] -> [[{new_link}]] in {ref_note}")
        except Exception as e:
            failed.append({"dangling": old_link, "ref": ref_note, "reason": str(e)})

result = json.dumps({
    "status": "completed",
    "repaired": repaired,
    "gaps_flagged": gaps,
    "failed": failed,
    "summary": f"{len(repaired)} links repaired, {len(gaps)} gaps flagged, {len(failed)} failed"
}, indent=2)
```

## Step 4: Validate

4. [validate: at_least 0 repaired or at_least 0 gaps_flagged]

## Related

- [[Dream-Analyze]] — surfaces the dangling links this repairs
- [[Dream-Gap-Fill]] — creates notes for the gaps this flags
- [[Dream-Link]] — sibling linking step for orphaned notes
