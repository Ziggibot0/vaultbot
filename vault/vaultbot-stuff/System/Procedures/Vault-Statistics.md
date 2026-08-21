---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-02
description: "Calculate vault statistics: total notes, average note length, distribution by directory, frontmatter coverage, link density, orphan rate, and procedure coverage. Returns a compact statistical summary of the vault's current state. Use when assessing vault health or tracking growth over time."
when_to_use: when assessing vault health, when tracking vault growth, when generating a status report, or when asked 'how is the vault doing'
falsifiable_if: the statistics are incorrect or contradict what Pattern-Scan reports
applies_to:
  - vault-statistics
  - vault-health
  - status-report
  - vault-maintenance
allowed_tools:
  - vault_list
  - llm_generate
summary: Vault-Statistics
tags:
  - procedure
  - procedures
---

# Vault-Statistics

## When to Run This

Run this for a quick statistical snapshot of the vault. Useful for
tracking growth over time or assessing overall health.

## Why This Exists

Assessing vault health or tracking growth needed a compact statistical summary — note counts, lengths, link density, orphan rate, procedure coverage. This procedure exists to compute those statistics deterministically. The key tradeoff: it computes stats in code and uses the small model only to write a brief health summary, so the numbers are exact and reproducible.

## Steps

### Step 1: Compute vault statistics deterministically

1. ```python
import json, re
from collections import Counter

all_files = vault_list()
stats = {
    "total_notes": 0, "total_chars": 0, "total_words": 0,
    "avg_note_length": 0, "frontmatter_coverage": 0,
    "total_links": 0, "total_orphans": 0,
    "by_directory": Counter(), "by_type": Counter(),
    "procedure_count": 0, "notes_with_todos": 0,
}

stems = set()
out_links = {}
in_links = Counter()

for fp in all_files:
    p = Path(fp)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    stats["total_notes"] += 1
    stats["total_chars"] += len(text)
    stats["total_words"] += len(text.split())

    # Directory
    rel = str(p.relative_to(vault_path)).replace("\\", "/")
    top_dir = rel.split("/")[0] if "/" in rel else "root"
    stats["by_directory"][top_dir] += 1

    # Frontmatter
    has_fm = text.startswith("---")
    if has_fm:
        stats["frontmatter_coverage"] += 1
        end = text.find("---", 3)
        if end != -1:
            fm = text[3:end]
            type_match = re.search(r'type:\s*(\w+)', fm)
            if type_match:
                stats["by_type"][type_match.group(1)] += 1
            if "type: procedure" in fm:
                stats["procedure_count"] += 1

    # Links
    stems.add(p.stem)
    links = re.findall(r'\[\[([^\]|]+)', text)
    stats["total_links"] += len(links)
    out_links[p.stem] = set(links)
    for l in links:
        in_links[l] += 1

    # TODOs
    if re.search(r'-\s\[\s\]', text):
        stats["notes_with_todos"] += 1

# Orphans
for stem in stems:
    if in_links.get(stem, 0) == 0 and len(out_links.get(stem, set()) & stems) == 0:
        stats["total_orphans"] += 1

stats["avg_note_length"] = stats["total_chars"] // max(stats["total_notes"], 1)
stats["frontmatter_coverage_pct"] = round(
    100 * stats["frontmatter_coverage"] / max(stats["total_notes"], 1), 1)
stats["by_directory"] = dict(stats["by_directory"])
stats["by_type"] = dict(stats["by_type"])

result = json.dumps(stats)
```

### Step 2: Small model generates a health summary

2. ```python
import json as _json

data = _json.loads(output)
prompt = f"""Given these vault statistics, write a brief health summary.

Statistics:
{json.dumps(data, indent=2)}

Return JSON: {{"health": "good|fair|poor", "summary": "2-3 sentence assessment", "concerns": ["issues if any"], "strengths": ["good signs"]}}
Return ONLY the JSON."""
summary = llm_generate(prompt)
result = summary
```

### Step 3: Return the statistics

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"health": "unknown"}
result = _json.dumps({"statistics": data, "health_assessment": parsed})
```

## Related

- [[Vault-Health-Check]] — the fast health snapshot
- [[Vault-List]] — lists notes by directory/tag
- [[Pattern-Scan]] — the per-note signal engine this complements