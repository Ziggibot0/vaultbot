---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-17
description: "Verify a chat answer's claims against their cited vault notes. Splits the answer into sentences, maps each sentence to the [[wikilink]] it cites, reads the cited note, and runs entailment checking (supported/unsupported/contradicted) per claim. Returns a per-claim verdict list. This is the idle-time verification layer behind the chat trust badge."
when_to_use: after a chat answer is delivered, to verify that each cited claim is actually supported by its source note (the 'I don't worry about hallucinations' guarantee)
falsifiable_if: a claim is marked 'supported' but the source note does not actually entail it, or a supported claim is flagged unsupported
applies_to:
  - claim-verification
  - fact-checking
  - provenance
  - chat-answer-verification
allowed_tools:
  - vault_search
  - code_read
  - llm_generate
summary: Verify-Answer-Entailment
tags:
  - procedure
  - procedures
  - provenance
---

# Verify-Answer-Entailment

## When to Run This

Run this after a chat answer is delivered, to verify that each cited claim is actually supported by its source note. This is the idle-time verification layer behind the chat trust badge — it runs in the background (not on the chat critical path) and upgrades the badge from "grounded" to "verified" once the per-claim checks complete.

**Applies to:** chat answers that cite vault notes via inline `[[wikilinks]]`.

**Does NOT apply to:** greetings, "I don't know" answers, or answers with no citations (nothing to verify).

## Design Principle

Sentence-to-source mapping is **structural** (deterministic code splits sentences and extracts their wikilinks). Entailment checking is **semantic** (the small model decides supported/unsupported/contradicted). This mirrors the [[Provenance-Audit]] split: code finds the structure, the model judges the meaning.

## Inputs

- `answer` (string, required): The chat answer text, with inline `[[wikilinks]]` citing vault notes.

## Outputs

- JSON list of per-claim verdicts: `[{"claim": "...", "note": "...", "verdict": "supported|unsupported|contradicted", "reasoning": "..."}]`
- A summary count: `{"supported": N, "unsupported": N, "contradicted": N, "total": N}`

---

## Steps

### Step 1: Split the answer into sentences and map each to its cited note

1. ```python
import re, json

answer = args.get("answer", "")
if not answer:
    result = json.dumps({"error": "answer argument required"})
else:
    # Split into sentences (same heuristic as citation_gate._split_sentences)
    sentences = re.split(r"(?<=[.!?])\s+", answer.strip())
    sentences = [s.strip() for s in sentences if s.strip()]

    # For each sentence, extract the [[wikilinks]] it cites
    pairs = []
    for s in sentences:
        links = re.findall(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]", s)
        if links:
            # A sentence may cite multiple notes; keep the first for entailment
            pairs.append({"claim": s, "note": links[0].strip()})

    result = json.dumps({"pairs": pairs, "total": len(pairs)})
```

### Step 2: Resolve each cited note to its file and read its text

2. ```python
import json

data = json.loads(output)
pairs = data.get("pairs", [])
enriched = []
for p in pairs:
    note = p.get("note", "")
    source_text = ""
    if note:
        try:
            # Resolve the wikilink stem to a file path via vault_search,
            # then read the full note text via code_read.
            hits = vault_search(note, k=1)
            if hits and len(hits) > 0:
                fp = hits[0].get("file_path", "")
                if fp:
                    content = code_read(fp)
                    if isinstance(content, dict):
                        source_text = content.get("content", "") or content.get("text", "")
                    else:
                        source_text = str(content)
        except Exception:
            source_text = ""
    enriched.append({
        "claim": p.get("claim", ""),
        "note": note,
        "source_text": (source_text or "")[:2000],
    })

result = json.dumps({"pairs": enriched, "total": len(enriched)})
```

### Step 3: Check entailment for each claim against its source

3. ```python
import json

data = json.loads(output)
pairs = data.get("pairs", [])
verdicts = []

for p in pairs:
    claim = p.get("claim", "")
    note = p.get("note", "")
    source_text = p.get("source_text", "")
    if not source_text:
        verdicts.append({
            "claim": claim,
            "note": note,
            "verdict": "unsupported",
            "reasoning": "no source text available",
        })
        continue
    prompt = (
        "Determine whether the source text supports the claim.\n\n"
        "Source text:\n" + source_text[:1500] + "\n\n"
        "Claim:\n" + claim + "\n\n"
        'Return ONLY JSON: {"verdict": "supported|unsupported|contradicted", "reasoning": "one sentence"}'
    )
    try:
        raw = llm_generate(prompt).strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        v = json.loads(raw)
        verdict = str(v.get("verdict", "unsupported")).lower()
        if verdict not in ("supported", "unsupported", "contradicted"):
            verdict = "unsupported"
        verdicts.append({
            "claim": claim,
            "note": note,
            "verdict": verdict,
            "reasoning": v.get("reasoning", ""),
        })
    except Exception as e:
        verdicts.append({
            "claim": claim,
            "note": note,
            "verdict": "unsupported",
            "reasoning": "entailment check failed: " + str(e)[:100],
        })

result = json.dumps(verdicts)
```

## Related

- [[Check-Entailment]] — the single-pair entailment primitive this procedure composes
- [[Extract-Claims]] — extracts claims from text before entailment checking
- [[Verify-Claims]] — the note-level verification orchestrator (this is the answer-level analog)
- [[Record-Provenance-Trace]] — stores the per-claim verdicts as a permanent provenance manifest
