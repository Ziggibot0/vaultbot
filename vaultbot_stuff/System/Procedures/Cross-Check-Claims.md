---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-02
description: Check if a vault note's claims are consistent with the web sources it cites. Reads the note, extracts claims with source URLs, fetches each source, and verifies each claim against the source text. Returns claims that the source doesn't support. Use when fact-checking a research note against its sources.
when_to_use: when fact-checking a research note against its cited web sources, when verifying research quality, or when a note makes claims that need source verification
falsifiable_if: the procedure flags a claim as unsupported when the source does support it, or misses unsupported claims
applies_to:
  - claim-verification
  - research-quality
  - fact-checking
  - source-verification
allowed_tools:
  - web_read_source
  - code_read
  - llm_generate
summary: "# Cross-Check-Claims"
tags:
  - procedure
  - procedures
---

# Cross-Check-Claims

## When to Run This

When a research note cites web sources, run this to verify each claim
against the actual source text. Catches claims that the source doesn't
actually support.

## Steps

### Step 1: Read the note and extract claims with source URLs

1. ```python
import re, json

note_path = args.get("note_path", "")
if not note_path:
    result = json.dumps({"error": "note_path argument required"})
else:
    p = Path(vault_path) / note_path
    if not p.exists():
        p = Path(note_path)
    if not p.exists():
        result = json.dumps({"error": f"note not found: {note_path}"})
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
        # Extract source URLs
        urls = re.findall(r'https?://[^\s\)]+', text)
        # Extract claims (sentences with citations)
        claims = []
        for line in text.split('\n'):
            if re.search(r'https?://|sources?:|\[\[', line, re.IGNORECASE) and len(line) > 20:
                claims.append(line.strip()[:200])
        result = json.dumps({"note": str(p), "urls": urls[:5],
                             "claim_lines": claims[:10], "note_text": text[:2000]})
```

### Step 2: Fetch each source and check claims against it

2. ```python
import json as _json

data = _json.loads(output)
if "error" in data:
    result = output
else:
    urls = data.get("urls", [])
    claims = data.get("claim_lines", [])
    if not urls or not claims:
        result = _json.dumps({"results": [], "note": "no URLs or claims to check"})
    else:
        results = []
        for url in urls[:3]:
            source_text = web_read_source(url=url)
            if not source_text:
                results.append({"url": url, "status": "source not saved",
                                "claims_checked": 0})
                continue
            # Small model checks each claim against source
            prompt = f"""For each claim, check if the source text supports it.
Source URL: {url}
Source text (first 1500 chars):
{source_text[:1500]}

Claims to check:
{_json.dumps(claims[:5], indent=2)}

Return JSON: [{{"claim": "...", "verdict": "supported|unsupported|contradicted", "reason": "one sentence"}}]
Return ONLY the JSON array."""
            verdicts = llm_generate(prompt)
            try:
                start = verdicts.find("[")
                end = verdicts.rfind("]")
                parsed = _json.loads(verdicts[start:end+1]) if start != -1 else []
            except Exception:
                parsed = []
            results.append({"url": url, "status": "checked",
                            "claims_checked": len(parsed), "verdicts": parsed})
        result = _json.dumps({"results": results})
```

### Step 3: Return the verification report

3. ```python
import json as _json

data = _json.loads(output)
results = data.get("results", [])
unsupported = []
for r in results:
    for v in r.get("verdicts", []):
        if v.get("verdict") in ("unsupported", "contradicted"):
            unsupported.append({"claim": v.get("claim"), "url": r.get("url"),
                                "verdict": v.get("verdict"), "reason": v.get("reason")})

result = _json.dumps({
    "sources_checked": len(results),
    "total_unsupported": len(unsupported),
    "unsupported_claims": unsupported,
    "all_results": results,
})
```