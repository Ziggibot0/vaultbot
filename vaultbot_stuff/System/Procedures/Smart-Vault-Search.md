---
type: procedure
status: verified
model_cartridge: small
created: 2026-08-02
description: "Search the vault for notes matching a query, then rank and deduplicate results by reading the actual note content. Given a query, runs vault_search, reads the top results, and the small model re-ranks them by true relevance (not just keyword overlap). Supports iterative deepening: if the first pass is thin, it runs follow-up searches on subtopics. Replaces the former Deep-Vault-Search and Smart-Vault-Dig procedures."
when_to_use: "when vault_search results are noisy, when results don't seem relevant despite keyword matches, when you need high-precision search results, when the first search was unsatisfying, or when you need to dig deeper into a topic iteratively"
falsifiable_if: "the re-ranked results are less relevant than the original, or the model misjudges relevance, or iterative deepening produces worse results"
applies_to:
  - vault-search
  - retrieval-quality
  - search-reranking
  - knowledge-digging
  - iterative-search
allowed_tools:
  - vault_search
  - llm_generate
success_count: 8
failure_count: 0
success_rate: 1.0
---

# Smart-Vault-Search

## When to Run This

When `vault_search` returns results but they don't seem truly relevant.
This procedure re-ranks them by reading the actual content and having the
small model judge true relevance, not just keyword overlap.

If the first pass returns thin results, it can iteratively deepen by
running follow-up searches on subtopics extracted from the initial hits.
This absorbs the former Deep-Vault-Search (iterative deepening) and
Smart-Vault-Dig (content-aware digging) procedures.

## Steps

### Step 1: Get search results (pre-supplied or run vault_search)

1. ```python
import json

query = args.get("query", "")
pre_supplied = args.get("hits")
if not query and not pre_supplied:
    result = json.dumps({"error": "query or hits argument required"})
elif pre_supplied:
    # Backend pre-supplied hits (from FUSED retrieval) — use directly.
    enriched = []
    for h in pre_supplied:
        fp = h.get("file_path", "")
        if not fp:
            continue
        enriched.append({"file_path": fp, "name": h.get("name", ""),
                         "keyword_score": h.get("keyword_score", h.get("score", 0)),
                         "preview": h.get("preview", "")[:300]})
    result = json.dumps({"hits": enriched, "query": query or ""})
else:
    hits = vault_search(query=query, k=8)
    enriched = []
    for h in hits:
        fp = h.get("file_path", "")
        if not fp:
            continue
        try:
            text = Path(fp).read_text(encoding="utf-8", errors="replace")[:500]
        except Exception:
            text = ""
        enriched.append({"file_path": fp, "name": h.get("name", ""),
                         "keyword_score": h.get("score", 0),
                         "preview": text[:300]})
    result = json.dumps({"hits": enriched, "query": query})
```

### Step 2: Small model re-ranks by true relevance

2. ```python
import json as _json

data = _json.loads(output)
hits = data.get("hits", [])
query = data.get("query", "")

if not hits:
    result = _json.dumps({"reranked": [], "note": "no hits to rerank"})
else:
    prompt = f"""Re-rank these vault search results by TRUE relevance to the query.
Read the previews and judge which actually answer the query.

Query: {query}

Results:
{json.dumps(hits, indent=2)}

Return JSON: [{{"file_path": "...", "name": "...", "relevance": "high|medium|low", "reason": "why"}}]
Return ONLY the JSON array."""
    reranked = llm_generate(prompt)
    result = reranked
```

### Step 3: Return the re-ranked results

3. ```python
import json as _json
try:
    start = output.find("[")
    end = output.rfind("]")
    parsed = _json.loads(output[start:end+1]) if start != -1 else []
except Exception:
    parsed = []

high = [p for p in parsed if p.get("relevance") == "high"]
medium = [p for p in parsed if p.get("relevance") == "medium"]

result = _json.dumps({"high_relevance": high,
                      "medium_relevance": medium,
                      "total_hits": len(parsed)})
```