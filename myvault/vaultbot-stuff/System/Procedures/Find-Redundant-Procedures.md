---
type: procedure
status: experimental
baseline: true
created: 2026-08-02
description: Find procedures that overlap or duplicate each other using deterministic string similarity. Reads all procedure descriptions and when_to_use fields, computes pairwise similarity using difflib + keyword overlap, and returns ranked overlapping pairs. No LLM needed — pure deterministic. Use when the procedure library feels redundant.
when_to_use: when the procedure library has overlapping procedures, when two procedures seem to do the same thing, when cleaning up the procedure library, or when asked 'which procedures are duplicates'
falsifiable_if: the procedure flags pairs as overlapping when they're distinct, or misses real overlaps
applies_to:
  - procedure-audit
  - deduplication
  - procedure-library
  - vault-maintenance
allowed_tools:
  - vault_list
  - llm_generate
success_count: 0
failure_count: 0
success_rate: 0.0
last_reviewed: 2026-08-03
summary: Find-Redundant-Procedures
tags:
  - procedure
  - procedures
---

# Find-Redundant-Procedures

## When to Run This

Run this when the procedure library feels bloated with overlapping
procedures. Uses deterministic string similarity (difflib + keyword
overlap) to find candidate pairs — no LLM needed, so it works on any
number of procedures without token limits.

## Why This Exists

The procedure library grows organically and accumulates overlapping
procedures that do the same thing under different names. This procedure
detects those duplicates deterministically using difflib + keyword
overlap, so it scales to any number of procedures without token limits.
The tradeoff: pure string similarity trades semantic precision for zero
LLM cost, so it may flag near-misses as well as true duplicates.

## Steps

### Step 1: Read all procedure descriptions and compute pairwise similarity

1. ```python
import json, os, re, difflib

# vault_list() returns paths relative to vault root.
# Detect the vault root by finding the Procedures directory.
vault_root = None
for candidate in [os.getcwd(), "C:\\Users\\skell\\Desktop\\Vault2", os.path.expanduser("~")]:
    if os.path.isdir(os.path.join(candidate, "vaultbot", "System", "Procedures")):
        vault_root = candidate
        break
if vault_root is None:
    d = os.getcwd()
    for _ in range(10):
        if os.path.isdir(os.path.join(d, "vaultbot", "System", "Procedures")):
            vault_root = d
            break
        d = os.path.dirname(d)

all_files = vault_list()
procedures = []
debug_info = {"vault_root": vault_root, "cwd": os.getcwd(), "total_files": len(all_files), "proc_files_seen": 0, "read_errors": 0}

for fp in all_files:
    fp_norm = fp.replace("\\", "/")
    if "System/Procedures/" not in fp_norm:
        continue
    debug_info["proc_files_seen"] += 1
    text = None
    for full_path in [os.path.join(vault_root, fp) if vault_root else None, fp, os.path.join(os.getcwd(), fp)]:
        if full_path is None:
            continue
        try:
            with open(full_path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            break
        except Exception:
            continue
    if text is None:
        debug_info["read_errors"] += 1
        continue
    if not text.startswith("---"):
        continue
    end = text.find("---", 3)
    if end == -1:
        continue
    fm = text[3:end]
    if "type: procedure" not in fm:
        continue
    desc = ""
    when = ""
    for line in fm.split("\n"):
        if line.strip().startswith("description:"):
            desc = line.split(":", 1)[1].strip().strip('"').strip("'")
        if line.strip().startswith("when_to_use:") or line.strip().startswith("when:"):
            when = line.split(":", 1)[1].strip().strip('"').strip("'")
    name = os.path.splitext(os.path.basename(fp))[0]
    procedures.append({"name": name, "description": desc, "when_to_use": when})

# Compute pairwise similarity using difflib + keyword overlap
pairs = []
for i, a in enumerate(procedures):
    for b in procedures[i+1:]:
        text_a = a["description"] + " " + a["when_to_use"]
        text_b = b["description"] + " " + b["when_to_use"]
        # Sequence similarity
        ratio = difflib.SequenceMatcher(None, text_a.lower(), text_b.lower()).ratio()
        # Name similarity
        name_ratio = difflib.SequenceMatcher(None, a["name"].lower(), b["name"].lower()).ratio()
        # Keyword overlap: shared significant words
        words_a = set(w for w in text_a.split() if len(w) > 3)
        words_b = set(w for w in text_b.split() if len(w) > 3)
        if words_a and words_b:
            jaccard = len(words_a & words_b) / len(words_a | words_b)
        else:
            jaccard = 0
        # Combined score: weighted average
        score = 0.4 * ratio + 0.3 * name_ratio + 0.3 * jaccard
        if score > 0.35:
            pairs.append({
                "proc_a": a["name"],
                "proc_b": b["name"],
                "score": round(score, 3),
                "text_sim": round(ratio, 3),
                "name_sim": round(name_ratio, 3),
                "keyword_overlap": round(jaccard, 3),
                "desc_a": a["description"][:120],
                "desc_b": b["description"][:120],
            })

pairs.sort(key=lambda p: p["score"], reverse=True)
result = json.dumps({"procedures_found": len(procedures), "overlapping_pairs": pairs, "pair_count": len(pairs)})
```

### Step 2: Format and return results

2. ```python
import json as _json

data = _json.loads(output)
pairs = data.get("overlapping_pairs", [])
proc_count = data.get("procedures_found", 0)

high = [p for p in pairs if p["score"] >= 0.55]
medium = [p for p in pairs if 0.35 <= p["score"] < 0.55]

summary = "Scanned {} procedures. Found {} overlapping pairs: {} high, {} medium.\n".format(proc_count, len(pairs), len(high), len(medium))

if high:
    summary += "\n=== HIGH OVERLAP (likely duplicates) ===\n"
    for p in high:
        summary += "\n  {} <-> {} (score: {})\n".format(p["proc_a"], p["proc_b"], p["score"])
        summary += "    A: {}\n".format(p["desc_a"])
        summary += "    B: {}\n".format(p["desc_b"])

if medium:
    summary += "\n=== MEDIUM OVERLAP (possibly related) ===\n"
    for p in medium:
        summary += "\n  {} <-> {} (score: {})\n".format(p["proc_a"], p["proc_b"], p["score"])
        summary += "    A: {}\n".format(p["desc_a"])
        summary += "    B: {}\n".format(p["desc_b"])

if not pairs:
    summary += "\nNo overlapping pairs found."

result = _json.dumps({"summary": summary, "high_count": len(high), "medium_count": len(medium), "pairs": pairs})
```

## Related

- [[Find-Duplicates]] — catches exact title duplicates; this catches semantic overlap
- [[Find-Underused-Procedures]] — sibling procedure-library audit probe
- [[Procedure-Library-Health]] — broader library health assessment