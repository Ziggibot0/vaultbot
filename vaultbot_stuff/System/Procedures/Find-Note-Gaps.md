---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-02
description: Find vault notes that a concept or procedure depends on but that don't exist yet. Given a note path, reads its wikilinks and depends_on frontmatter, checks which targets are missing, and returns the list of missing notes with a one-sentence description of what each should contain. Use when checking if a note's dependencies are met before relying on it.
when_to_use: when a note references notes that might not exist, before relying on a note's dependencies, when checking if a concept's supporting notes are present, or when asked 'what's missing from this note's dependencies'
falsifiable_if: the procedure reports a note as missing when it exists, or misses a missing dependency
applies_to:
  - gap-detection
  - vault-maintenance
  - dependency-checking
  - vault-completeness
allowed_tools:
  - vault_list
  - code_read
  - llm_generate
summary: SUMMARY
tags:
  - procedure
  - procedures
---

# Find-Note-Gaps

## When to Run This

Run this when a note depends on other notes that might not exist. It checks
wikilinks and `depends_on` frontmatter, and reports which targets are missing.

## Steps

### Step 1: Read the note and extract all references

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
        # Extract wikilinks
        wikilinks = set(re.findall(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', text))
        # Extract depends_on from frontmatter
        depends = set()
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                fm = text[3:end]
                for line in fm.split("\n"):
                    if line.strip().startswith("depends_on:"):
                        # Could be inline or list
                        val = line.split(":", 1)[1].strip()
                        if val.startswith("["):
                            # Inline list
                            depends.update(re.findall(r'\[\[([^\]]+)\]\]|"([^"]+)"', val))
                            depends = {d[0] or d[1] for d in depends}
                        elif val:
                            depends.add(val.strip('"').strip("'"))
        all_refs = wikilinks | depends
        # Get all existing note stems
        all_stems = set()
        for fp in vault_list():
            all_stems.add(Path(fp).stem)
        missing = [ref for ref in all_refs if ref not in all_stems]
        result = json.dumps({"note": str(p), "total_refs": len(all_refs),
                             "missing": missing, "existing": len(all_refs) - len(missing)})
```

### Step 2: Small model describes what each missing note should contain

2. ```python
import json as _json

data = _json.loads(output)
if "error" in data:
    result = output
else:
    missing = data.get("missing", [])
    if not missing:
        result = _json.dumps({"missing": [], "note": "all dependencies exist"})
    else:
        # Read the note for context
        note_text = Path(data["note"]).read_text(encoding="utf-8", errors="replace")[:1500]
        prompt = f"""This note references these notes that don't exist yet:
{missing}

The referencing note's content:
{note_text}

For each missing note, describe what it should contain based on the context.
Return JSON: [{{"note": "Note-Name", "should_contain": "one sentence description", "priority": "high|medium|low"}}]
Return ONLY the JSON array."""
        desc = llm_generate(prompt)
        result = desc
```

### Step 3: Return the gap report

3. ```python
import json as _json
try:
    start = output.find("[")
    end = output.rfind("]")
    parsed = _json.loads(output[start:end+1]) if start != -1 else []
except Exception:
    parsed = []
result = _json.dumps({"missing_notes": parsed, "total_gaps": len(parsed)})
```


## Conditional Branches (Post-Detection Routing)

> **Research backing:** [[Execution-Loop-Dominance-Pattern]] demonstrates
> that routing detected gaps to specialized handlers produces better
> outcomes than monolithic gap-filling. [[Information-feedback-loops-for-iterative-self-improvement]]
> shows that feeding gap detection results into targeted creation
> processes creates compounding knowledge growth. The
> [[Procedure-Composition-Patterns]] approach: detect once, dispatch
> conditionally.

After Step 3 returns the gap report, the caller should dispatch each
missing note to the appropriate creation procedure based on its type and
priority. These are **conditional if-branches**, not sequential steps.

### IF missing note has "procedure" in its name OR is referenced by a procedure

→ Run `run_procedure("How-to-Create-a-Procedure")` to create the missing
procedure, using the referencing note's context to determine what the
procedure should do.

**Rationale:** Missing procedures are functional gaps — they mean
VaultBot is doing something manually that could be automated. Creating
them directly closes the gap. Backed by [[Deterministic-Scaffolding-for-Small-Models]]
which shows that proceduralizing workflows improves reliability and
reduces token costs.

### IF missing note is a concept (referenced by knowledge notes, not procedures)

→ Call `vault_research(topic=<note_name>)` to research and create the
note with web sources. Then run `run_procedure("Structure-Research-Note",
   note_path=<new_note>)` to ensure it meets quality standards.

**Rationale:** Missing concept notes are knowledge gaps. Researching them
with web sources ensures they're grounded in evidence, not
hallucination. [[Structure-Research-Note]] then ensures the note has
proper wikilinks, frontmatter, and source citations. Backed by the
[[Vault-Knowledge-Only-Directive]] and [[IDK-Fallback-Directive]].

### IF missing note has priority "high" (referenced by 3+ notes)

→ Auto-research immediately using `vault_research(topic=<note_name>)`.
Do not wait for user confirmation — the high reference count means
multiple notes are depending on this concept existing.

**Rationale:** High-priority gaps create the most broken wikilinks and
degrade retrieval quality the most. Auto-researching them is the
highest-leverage action. The priority threshold of 3+ references comes
from [[Information-feedback-loops-for-iterative-self-improvement]] which
shows that concepts referenced by multiple notes are hub candidates that
disproportionately improve graph connectivity when created.

### IF missing note has priority "low" (referenced by 1 note)

→ Log the gap but do not auto-create. Surface it in the next dream pass
for batch evaluation. The referencing note can function without it for
now.

**Rationale:** Low-priority gaps have minimal impact on vault
connectivity. Auto-creating them wastes tokens on notes that may never
be retrieved. The [[Ephemeral-Argument-Architecture]] concept shows
that not every link needs a target — some are aspirational references
that gain value only when the concept becomes relevant.

### IF the note itself is thin (exists but has < 100 words of content)

→ Run `run_procedure("Condense-Note", note_path=<note_path>)` if the
note has enough content to condense, OR call `vault_research` to expand
it with sourced content if it's too thin to be useful.

**Rationale:** Thin notes are worse than missing notes — they exist
enough to be retrieved but don't contain enough content to be useful.
[[RAG-evaluation-metrics]] shows that thin notes reduce retrieval
precision because they match queries but don't satisfy information
needs.
