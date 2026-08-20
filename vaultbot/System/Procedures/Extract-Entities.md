---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-07-31
description: Extract entities (proper nouns, named concepts) and 3-5 key facts from text. Returns JSON with entities and key_facts arrays. Uses the small model — entity extraction is simple structured parsing.
when_to_use: when you need to pull named entities and key facts out of raw text to link into the knowledge graph
falsifiable_if: extracted entities are not proper nouns or named concepts, or key facts are invented
applies_to:
  - knowledge-extraction
  - graph-building
  - note-enrichment
allowed_tools:
  - llm_generate
summary: Extract-Entities
tags:
  - procedure
  - procedures
last_reviewed: 2026-08-15
---

# Extract-Entities

## When to Run This

Run this procedure when you need to extract structured entities and key facts from a text passage. This is the enrichment step for the graph_ops extract operation — it pulls named entities and factual statements out of raw text so they can be linked into the vault's knowledge graph.

## Why This Exists

Raw text holds named entities and key facts that must be pulled out as structured JSON before they can be linked into the knowledge graph. This procedure exists as the enrichment step for the graph_ops extract operation. The key tradeoff is that it uses the small model — entity extraction is simple structured parsing, not reasoning.

## Steps

### Step 1: Extract entities and facts with the small model

1. [llm: Extract the most important entities (proper nouns, named concepts) and 3-5 key facts from the text below. Respond as JSON: {"entities": [...], "key_facts": [...]}. The text to extract from is provided as the prior step context. Return ONLY the JSON.]

### Step 2: Return the structured output

2. ```python
import json
try:
    # Find the JSON object in the output
    start = output.find("{")
    end = output.rfind("}")
    if start != -1 and end != -1 and end > start:
        parsed = json.loads(output[start:end+1])
        entities = parsed.get("entities", [])
        key_facts = parsed.get("key_facts", [])
    else:
        entities = []
        key_facts = []
except Exception:
    entities = []
    key_facts = []
result = json.dumps({"entities": entities, "key_facts": key_facts, "entity_count": len(entities), "fact_count": len(key_facts)})
```

## Related

- [[Extract-Claims]] — sibling extraction for factual claims
- [[Extract-Procedures-From-Note]] — sibling extraction for procedural content