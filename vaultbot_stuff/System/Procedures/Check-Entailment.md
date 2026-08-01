---
type: procedure
status: experimental
model_cartridge: small
created: 2026-07-31
description: "Check whether a source text supports, contradicts, or does not support a claim. Returns JSON with verdict and reasoning. Uses the small model — entailment checking is simple classification."
when_to_use: "when you need to verify whether a claim is supported by its cited source (claim verification / fact-checking)"
falsifiable_if: "the verdict contradicts what the source text actually says, or the reasoning is fabricated"
applies_to:
  - claim-verification
  - fact-checking
  - research-quality
allowed_tools:
  - llm_generate
---

# Check-Entailment

## When to Run This

Run this procedure when you need to verify whether a claim is supported by its cited source. Given a source text and a claim, the small model determines whether the source supports, contradicts, or does not support the claim. This is the core step of the Verify-Claims workflow.

## Steps

### Step 1: Ask the small model for a verdict

1. [llm: You are a fact-checking system. Given a source text and a claim, determine whether the source supports the claim. Verdict: 'supported', 'unsupported', or 'contradicted'. Return JSON: {"verdict": "...", "reasoning": "..."}. The source text and claim are provided as the prior step context. Return ONLY the JSON.]

### Step 2: Return the verdict

2. ```python
import json
try:
    raw = output.strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    parsed = json.loads(raw)
    verdict = parsed.get("verdict", "unsupported").lower()
    if verdict not in ("supported", "unsupported", "contradicted"):
        verdict = "unsupported"
    result = json.dumps({"verdict": verdict, "reasoning": parsed.get("reasoning", "")})
except Exception:
    result = json.dumps({"verdict": "error", "reasoning": "could not parse response"})
```