---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-02
description: Scan the vault for a specific phrase, sentence, or claim and find every note that contains it. Given a search string, does exact substring matching across all notes and returns every occurrence with note path, line number, and context. Use when you need to find every instance of a specific string, not a concept.
when_to_use: when looking for every instance of a specific phrase, when checking if a claim appears anywhere, when doing exact-string search, or when replacing a term across the vault
falsifiable_if: the procedure misses occurrences, or returns false matches
applies_to:
  - vault-search
  - exact-match
  - string-search
  - vault-maintenance
allowed_tools:
  - vault_list
  - llm_generate
summary: Vault-Inventory
tags:
  - procedure
  - procedures
---

# Vault-Inventory

## When to Run This

When you need to find every exact occurrence of a specific string in the
vault. Not semantic search — exact substring matching. Use when replacing
a term, checking if a phrase exists anywhere, or finding all instances of
a specific claim.

## Why This Exists

Semantic search finds by meaning, but sometimes you need every exact occurrence of a specific string — for term replacement or claim checking. This procedure exists to do exact substring matching across all notes with path, line number, and context. The key tradeoff: it's deliberately not semantic — exact matching only — so it returns precise occurrences rather than related-but-different content.

## Steps

### Step 1: Scan all notes for the exact string

1. ```python
import json

search_string = args.get("search_string", args.get("query", ""))
if not search_string:
    result = json.dumps({"error": "search_string or query argument required"})
else:
    all_files = vault_list()
    occurrences = []
    for fp in all_files:
        p = Path(fp)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if search_string.lower() in text.lower():
            rel = str(p.relative_to(vault_path)).replace("\\", "/")
            lines = text.split('\n')
            for i, line in enumerate(lines, 1):
                if search_string.lower() in line.lower():
                    # Get context
                    start = max(0, i - 2)
                    end = min(len(lines), i + 2)
                    context = '\n'.join(lines[start:end])
                    occurrences.append({"note": rel, "line": i,
                                        "context": context[:300],
                                        "line_text": line.strip()[:150]})
    result = json.dumps({"search_string": search_string,
                         "occurrences": occurrences[:30],
                         "total_occurrences": len(occurrences),
                         "notes_with_match": len(set(o["note"] for o in occurrences))})
```

### Step 2: Return the results (no LLM needed for exact search)

2. ```python
import json as _json

data = _json.loads(output)
if "error" in data:
    result = data
else:
    result = _json.dumps({
        "search_string": data["search_string"],
        "total_occurrences": data["total_occurrences"],
        "notes_affected": data["notes_with_match"],
        "occurrences": data["occurrences"],
    })
```

## Related

- [[Vault-List]] — lists notes by directory/tag (structural complement)
- [[Update-Vault-References]] — bulk-updates terms found by exact search
- [[Vault-Topic-Density]] — finds topics buried in content