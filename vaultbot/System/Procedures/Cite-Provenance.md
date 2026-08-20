---
type: procedure
status: experimental
baseline: true
model_cartridge: big
created: 2026-08-09
description: "Cite-Provenance takes a draft answer, splits it into claims, deterministically searches the vault for supporting notes, then has the LLM format the answer with inline wikilink citations. Every claim must cite a vault note."
when_to_use: "After generating an answer, before presenting it. Run this to attach citations to every factual claim."
falsifiable_if: "A claim is cited to a note that doesn't actually support it."
applies_to:
  - provenance
  - citation
  - grounding
  - quality-control
allowed_tools:
  - vault_search
  - vault_read_note
summary: |
  Cite-Provenance: forces every claim to cite a supporting vault note.
  1. Code step splits draft into sentences and searches vault for each.
  2. LLM formats the answer with inline wikilinks.
tags:
  - procedure
  - provenance
  - citation
  - grounding
---

# Cite-Provenance

## Purpose

Forces every factual claim in an answer to cite a supporting vault note. The core search is deterministic (code step calls vault_search for each sentence) — the LLM only formats the output.

## Why This Exists

Answers were presented with claims that had no grounding in the vault, so there was no way to trace a claim back to its source. This procedure forces every claim to cite a supporting vault note. The key tradeoff is that the search is deterministic (code step), while the LLM only formats the output — keeping grounding cheap and reliable.

## Inputs

- `draft_answer` (string, required): The draft answer containing claims to cite.

## Output Contract

Returns the cited answer with inline wikilinks and a Sources section.

---

## Steps

### Step 1: Split the draft into claims and search the vault for each

1. ```python
import json, re

draft = args.get("draft_answer", "")
if not draft:
    raise RuntimeError("draft_answer argument required")

# Split into sentences (simple heuristic)
sentences = re.split(r'(?<=[.!?])\s+', draft)
sentences = [s.strip() for s in sentences if len(s.strip()) > 20]

# Search vault for each sentence
citations = []
for sent in sentences:
    try:
        results = vault_search(sent, k=3)
        if results and len(results) > 0:
            top = results[0]
            # vault_search returns {"file_path": ..., "name": ..., "score": ...}
            note_name = top.get("name", top.get("filename", "Unknown"))
            citations.append({
                "claim": sent,
                "note": note_name,
                "excerpt": str(top.get("file_path", ""))[:200],
                "grounded": True,
            })
        else:
            citations.append({
                "claim": sent,
                "note": None,
                "excerpt": None,
                "grounded": False,
            })
    except Exception as e:
        citations.append({
            "claim": sent,
            "note": None,
            "excerpt": str(e)[:200],
            "grounded": False,
        })

grounded = sum(1 for c in citations if c["grounded"])
print(f"Sentences: {len(sentences)}, Grounded: {grounded}, Ungrounded: {len(sentences) - grounded}")
for c in citations:
    if c["grounded"]:
        print(f"  [[{c['note']}]] <- {c['claim'][:80]}")
    else:
        print(f"  UNGROUNDED <- {c['claim'][:80]}")

result = json.dumps({"citations": citations, "draft_answer": draft})
```

### Step 2: Format the answer with inline citations

2. [llm: Format the final answer with inline citations. Use the original draft and the citation data below.

ORIGINAL DRAFT: {step_1.draft_answer}
CITATIONS: {step_1.citations}

For each grounded claim, insert the wikilink after the claim: [[Note-Title]].
For ungrounded claims, either remove them or flag with [speculative].

Output the final cited answer, followed by a "Sources" section listing all citations.

FINAL OUTPUT:]

[validate: contains "[[" and contains "Sources"]

## Related

- [[Record-Provenance-Trace]] — records the provenance trace this produces
- [[Verify-Claims]] — verifies claims against sources
- [[Cross-Check-Claims]] — checks claims against cited web sources
