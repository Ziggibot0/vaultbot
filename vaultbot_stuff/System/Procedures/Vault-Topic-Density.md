---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-02
description: Find notes that mention a topic but don't have it in their title, frontmatter, or tags — the topic is buried in the content. Given a topic, finds all notes that discuss it in their body without any metadata indication. Use when looking for notes that are secretly about a topic but wouldn't be found by title or tag search.
when_to_use: when looking for notes that are secretly about a topic, when title and tag search miss relevant notes, when a topic is buried in content, or when doing thorough topic coverage analysis
falsifiable_if: the procedure returns notes that don't actually discuss the topic, or misses notes that do
applies_to:
  - vault-search
  - topic-coverage
  - hidden-content
  - knowledge-digging
allowed_tools:
  - vault_list
  - llm_generate
summary: Vault-Topic-Density
tags:
  - procedure
  - procedures
---

# Vault-Topic-Density

## When to Run This

When title and tag search aren't enough — a note might discuss a topic
deeply without mentioning it in the title or tags. This finds those
hidden-topic notes.

## Steps

### Step 1: Find notes that mention the topic in body but not in title/tags

1. ```python
import re, json

topic = args.get("topic", "")
if not topic:
    result = json.dumps({"error": "topic argument required"})
else:
    all_files = vault_list()
    hidden = []
    topic_lower = topic.lower()
    for fp in all_files:
        p = Path(fp)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        # Check if topic is in the body
        if topic_lower not in text.lower():
            continue
        # Check if it's in the title or frontmatter
        in_title = topic_lower in p.stem.lower()
        in_fm = False
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                fm = text[3:end].lower()
                if topic_lower in fm:
                    in_fm = True
        if not in_title and not in_fm:
            rel = str(p.relative_to(vault_path)).replace("\\", "/")
            # Count topic mentions in body
            body = text
            if text.startswith("---"):
                end = text.find("---", 3)
                if end != -1:
                    body = text[end+3:]
            mentions = len(re.findall(r'\b' + re.escape(topic) + r'\b', body, re.IGNORECASE))
            # Get the paragraphs that mention it
            paragraphs = [para.strip()[:200] for para in body.split('\n\n')
                          if topic_lower in para.lower()][:2]
            hidden.append({"path": rel, "stem": p.stem,
                           "mention_count": mentions,
                           "context": paragraphs})

    hidden.sort(key=lambda h: -h["mention_count"])
    result = json.dumps({"topic": topic, "hidden_notes": hidden[:20],
                         "total_hidden": len(hidden)})
```

### Step 2: Small model assesses how relevant each hidden note is

2. ```python
import json as _json

data = _json.loads(output)
if "error" in data:
    result = output
else:
    hidden = data.get("hidden_notes", [])
    if not hidden:
        result = _json.dumps({"assessments": [], "note": "no hidden-topic notes found"})
    else:
        prompt = f"""For each note, assess how central this topic is to the
note's purpose. Is the topic the main subject, a supporting reference, or
just a passing mention?

Topic: {data['topic']}

Notes:
{json.dumps(hidden[:10], indent=2)}

Return JSON: [{{"path": "...", "centrality": "main|supporting|passing", "should_retag": true/false, "should_retitle": true/false}}]
Return ONLY the JSON array."""
        assessments = llm_generate(prompt)
        result = assessments
```

### Step 3: Return the hidden topic analysis

3. ```python
import json as _json
try:
    start = output.find("[")
    end = output.rfind("]")
    parsed = _json.loads(output[start:end+1]) if start != -1 else []
except Exception:
    parsed = []
retag = [p for p in parsed if p.get("should_retag")]
result = _json.dumps({
    "hidden_topic_notes": parsed,
    "retag_candidates": retag,
    "total_hidden": data.get("total_hidden", 0),
})
```