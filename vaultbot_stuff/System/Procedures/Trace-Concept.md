---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-02
description: "Trace a concept through the vault: find the note that defines it, all notes that link to it, all notes that mention it by name but don't link it, and identify the chain of notes that build on the concept. Returns a concept dependency graph. Use when you need to understand how a concept evolved or what depends on it."
when_to_use: "when you need to understand the full footprint of a concept in the vault, before deleting or renaming a concept note, or when tracking how an idea propagated"
falsifiable_if: "the trace misses notes that mention the concept, or includes notes that don't actually reference it"
applies_to:
  - concept-tracing
  - dependency-mapping
  - vault-search
  - graph-organization
allowed_tools:
  - vault_list
  - vault_search
  - llm_generate
---

# Trace-Concept

## When to Run This

Run this when you need the full footprint of a concept: where it's defined,
what links to it, what mentions it without linking, and what builds on it.
Essential before renaming or deleting a concept note.

## Steps

### Step 1: Find the defining note and all mentions

1. ```python
import re, json

concept = args.get("concept", "")
if not concept:
    result = json.dumps({"error": "concept argument required"})
else:
    all_files = vault_list()
    definers = []
    linkers = []
    mentioners = []
    concept_lower = concept.lower()
    link_pattern = re.compile(r'\[\[' + re.escape(concept) + r'(\|[^\]]+)?\]\]', re.IGNORECASE)
    mention_pattern = re.compile(r'\b' + re.escape(concept) + r'\b', re.IGNORECASE)

    for fp in all_files:
        p = Path(fp)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel = str(p.relative_to(vault_path)).replace("\\", "/")
        has_link = bool(link_pattern.search(text))
        has_mention = bool(mention_pattern.search(text))
        # Definer: note whose stem matches the concept
        if p.stem.lower() == concept_lower:
            definers.append({"path": rel, "chars": len(text)})
        if has_link:
            linkers.append({"path": rel})
        elif has_mention:
            mentioners.append({"path": rel})

    result = json.dumps({
        "concept": concept,
        "definers": definers,
        "linkers": linkers,
        "mentioners_no_link": mentioners,
        "total_references": len(linkers) + len(mentioners),
    })
```

### Step 2: Small model identifies the dependency chain

2. ```python
import json as _json

trace_data = _json.loads(output)
if "error" in trace_data:
    result = output
else:
    prompt = f"""Given this concept trace, identify the dependency chain:
which notes define the concept, which build on it, and which are downstream.
{json.dumps(trace_data, indent=2)}

Return JSON: {{"chain": [{{"note": "path", "role": "definer|builds_on|downstream|mentions"}}], "summary": "one sentence"}}
Return ONLY the JSON."""
    chain = llm_generate(prompt)
    result = chain
```

### Step 3: Return the full trace

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {}
result = _json.dumps({"trace": trace_data, "chain": parsed.get("chain", []),
                      "summary": parsed.get("summary", "")})
```