---
type: procedure
status: experimental
model_cartridge: small
created: 2026-07-31
description: "Extract entities (proper nouns, named concepts) and 3-5 key facts from text. Returns JSON with entities and key_facts arrays. Uses the small model — entity extraction is simple structured parsing."
when_to_use: "when you need to pull named entities and key facts out of raw text to link into the knowledge graph"
falsifiable_if: "extracted entities are not proper nouns or named concepts, or key facts are invented"
applies_to:
  - knowledge-extraction
  - graph-building
  - note-enrichment
allowed_tools:
  - llm_generate
---

# Extract-Entities

## When to Run This

Run this procedure when you need to extract structured entities and key facts from a text passage. This is the enrichment step for the graph_ops extract operation — it pulls named entities and factual statements out of raw text so they can be linked into the vault's knowledge graph.

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