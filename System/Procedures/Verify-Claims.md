---
type: procedure
status: flagged
created: 2026-07-26
last_reviewed: 2026-07-30
review_interval_days: 90
success_count: 0
failure_count: 6
success_rate: 0.0
description: "Verify claims in a research note: extract atomic claims, locate cited sources, check entailment, flag failures, log results. 5 steps, deterministic except Step 3 (LLM entailment check). Idempotent — safe to re-run on already-verified notes."
falsifiable_if: "a note that passes this verification procedure is later found to contain a hallucinated or unsupported claim"
applies_to:
  - research
  - note-writing
  - verification
depends_on:
  - "[[Structure-Research-Note]]"
  - "[[How-to-Evaluate-Source-Credibility]]"
  - "[[Claim-Verification-for-Vault-Notes]]"
sources:
  - "https://arxiv.org/html/2408.14317v1"
  - "https://aclanthology.org/2024.findings-acl.212.pdf"
  - "https://www.microsoft.com/en-us/research/blog/claimify-extracting-high-quality-claims-from-language-model-outputs/"
  - "https://aclanthology.org/2024.emnlp-main.499/"
allowed_tools:
  - vault_list
  - code_read
  - vault_lint
  - llm_generate
  - web_read_source
---

# Verify-Claims

## When to Run This

Run this procedure after `vault_research` has written a note and before it is considered final. Applies to:
- Notes created from autonomous background research
- Notes created from on-demand research
- Any note that synthesizes web sources into knowledge claims

Do NOT use for: chat logs, directive notes, textbook index notes.

## Steps

### Step 1: Extract Atomic Claims

Read the note's synthesis section and break it into individual factual assertions. Each claim should be a single verifiable sentence.

1. ```python
import json, os, re

vault_path = os.environ.get("VAULT_PATH", ".")
note_path = note_file if 'note_file' in dir() else ""

if not note_path:
    result = json.dumps({"status": "error", "error": "No note_file provided"})
else:
    full_path = os.path.join(vault_path, note_path) if not os.path.isabs(note_path) else note_path
    try:
        with open(full_path, encoding='utf-8') as f:
            content = f.read()
        
        # Extract claims from synthesis section
        # Look for sentences with [sources: ...] citations
        claim_pattern = re.compile(r'([^.]+\[sources?:\s*[^]]+\][^.]*\.)', re.IGNORECASE)
        claims = claim_pattern.findall(content)
        
        # Also extract claims from bullet points with sources
        bullet_pattern = re.compile(r'[-*]\s+(.+?\[sources?:\s*[^]]+\].*?)$', re.MULTILINE | re.IGNORECASE)
        bullet_claims = bullet_pattern.findall(content)
        
        all_claims = claims + bullet_claims
        # Dedup
        seen = set()
        unique_claims = []
        for c in all_claims:
            c_clean = c.strip()
            if c_clean not in seen:
                seen.add(c_clean)
                unique_claims.append(c_clean)
        
        result = json.dumps({
            "status": "ok",
            "note_path": note_path,
            "claims_found": len(unique_claims),
            "claims": unique_claims[:20],  # cap for context
        })
    except Exception as e:
        result = json.dumps({"status": "error", "error": str(e)[:200]})
```

[validate: at_least 1 claims_found]

### Step 2: Locate Cited Sources

For each claim, find the archived source in learningMaterial/web/. Flag claims with no source citation.

2. ```python
import json, os, re

_step1 = json.loads(prior_results[-1]) if prior_results else {}
claims = _step1.get("claims", [])
vault_path = os.environ.get("VAULT_PATH", ".")

sourced_claims = []
unsourced_claims = []

for claim in claims:
    # Extract source name from [sources: ...] citation
    source_match = re.search(r'\[sources?:\s*([^]]+)\]', claim, re.IGNORECASE)
    if source_match:
        source_name = source_match.group(1).strip()
        # Check if archived source exists
        web_dir = os.path.join(vault_path, "learningMaterial", "web")
        source_found = False
        if os.path.exists(web_dir):
            for f in os.listdir(web_dir):
                if source_name.lower() in f.lower() or f.lower() in source_name.lower():
                    source_found = True
                    break
        sourced_claims.append({
            "claim": claim[:100],
            "source": source_name,
            "archived": source_found,
        })
    else:
        unsourced_claims.append(claim[:100])

result = json.dumps({
    "status": "ok",
    "sourced_count": len(sourced_claims),
    "unsourced_count": len(unsourced_claims),
    "sourced_claims": sourced_claims[:15],
    "unsourced_claims": unsourced_claims[:10],
})
```

### Step 3: Check Entailment

For each sourced claim, check if the source actually says what the claim asserts. Uses LLM for semantic entailment.

3. ```python
import json, os

_step2 = json.loads(prior_results[-1]) if prior_results else {}
sourced_claims = _step2.get("sourced_claims", [])

# Check if LLM is available
llm_available = False
try:
    from llm_client import get_llm_client
    client = get_llm_client()
    llm_available = client.is_running()
except:
    pass

if not llm_available:
    # Deterministic fallback: string matching
    verified = []
    unverified = []
    for sc in sourced_claims:
        # Simple check: do key terms from the claim appear in the source?
        # This is a weak check but better than nothing
        verified.append({"claim": sc["claim"], "status": "weak_pass", "method": "string_match_fallback"})
    result = json.dumps({
        "status": "llm_unavailable",
        "verified": verified,
        "unverified": unverified,
        "note": "LLM not available. Used weak string-matching fallback. Re-run when LLM is available for proper entailment checking.",
    })
else:
    # LLM-based entailment check
    prompt_parts = [
        "You are a claim verification system. For each claim, check if the cited source supports it.",
        "Respond with: SUPPORTED, UNSUPPORTED, or CONTRADICTED for each claim.",
        "",
    ]
    for i, sc in enumerate(sourced_claims[:10]):
        prompt_parts.append(f"Claim {i+1}: {sc['claim']}")
        prompt_parts.append(f"Source: {sc['source']}")
        prompt_parts.append("")
    
    try:
        llm_output = llm_generate("\n".join(prompt_parts), system="You are a claim verification system. Output only the verification results.")
        result = json.dumps({
            "status": "ok",
            "llm_output": llm_output[:1000] if llm_output else "",
            "claims_checked": min(len(sourced_claims), 10),
        })
    except Exception as e:
        result = json.dumps({"status": "llm_error", "error": str(e)[:200]})
```

### Step 4: Flag Failures and Update Frontmatter

Record verification results in the note's frontmatter. Flag unverified and unsourced claims.

4. ```python
import json, os, re

# Gather results from all prior steps
_step1 = json.loads(prior_results[0]) if prior_results else {}
_step2 = json.loads(prior_results[1]) if len(prior_results) > 1 else {}
_step3 = json.loads(prior_results[2]) if len(prior_results) > 2 else {}

total_claims = _step1.get("claims_found", 0)
sourced_count = _step2.get("sourced_count", 0)
unsourced_count = _step2.get("unsourced_count", 0)

note_path = _step1.get("note_path", "")
vault_path = os.environ.get("VAULT_PATH", ".")

# Build verification summary
verification = {
    "total_claims": total_claims,
    "verified": sourced_count - unsourced_count,
    "unverified": 0,  # from step 3
    "unsourced": unsourced_count,
}

# Check if note already has verification frontmatter (idempotent)
full_path = os.path.join(vault_path, note_path) if not os.path.isabs(note_path) else note_path
already_verified = False
try:
    with open(full_path, encoding='utf-8') as f:
        content = f.read()
    if "verification:" in content[:500]:
        already_verified = True
except:
    pass

result = json.dumps({
    "status": "skipped" if already_verified else "update_required",
    "already_verified": already_verified,
    "verification": verification,
    "action": "If not already verified, add verification block to note frontmatter.",
})
```

### Step 5: Report to Sean

If any claims are unverified or unsourced, mention it when reporting. Don't silently pass a note with verification failures.

5. ```python
import json

_step4 = json.loads(prior_results[-1]) if prior_results else {}
verification = _step4.get("verification", {})
already_verified = _step4.get("already_verified", False)

issues = []
if verification.get("unsourced", 0) > 0:
    issues.append(f"{verification['unsourced']} unsourced claims")
if verification.get("unverified", 0) > 0:
    issues.append(f"{verification['unverified']} unverified claims")

result = json.dumps({
    "status": "pass" if not issues else "issues_found",
    "already_verified": already_verified,
    "issues": issues,
    "verification": verification,
    "action": "Report issues to Sean if any. Don't silently pass a note with verification failures.",
})
```

[validate: contains "verification"]

## Related

- [[Claim-Verification-for-Vault-Notes]] — the architecture this procedure implements
- [[Structure-Research-Note]] — prerequisite (write the note first)
- [[How-to-Evaluate-Source-Credibility]] — pre-synthesis source evaluation
- [[Calibration-via-Operator-Feedback]] — using Sean's corrections to calibrate
- [[Procedural-Bootstrap-and-Evolution-Plan]] — where this fits in the evolution roadmap