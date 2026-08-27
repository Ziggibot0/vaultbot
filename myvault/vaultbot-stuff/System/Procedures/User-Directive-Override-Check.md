---
type: procedure
status: experimental
baseline: true
created: 2026-08-06
description: Pre-check that runs before any routing or tool work. Detects explicit user directives (negations, prohibitions, mandates) and checks whether any vault design doc, architecture note, or procedure contradicts them. If conflict exists, the user directive wins — the vault note is flagged for review, not obeyed.
when_to_use: before Route-Task, before any tool work, whenever the user says "don't", "never", "always", "NO", or any explicit prohibition/mandate
falsifiable_if: a user directive is overridden by a vault note despite this check running, or a false positive flags a non-conflicting note
applies_to:
  - directive-enforcement
  - safety
  - user-authority
allowed_tools:
  - vault_search
  - vault_read_note
  - llm_generate
tags:
  - procedure
  - procedures
  - safety
summary: User-Directive-Override-Check
---

# User-Directive-Override-Check

## Purpose

Prevent VaultBot from ever letting a vault design document, architecture note, or procedure override the user's explicit live directive. The user's words are the highest authority in the system — above any note, any procedure, any architecture doc.

## The Rule

```
USER DIRECTIVE > vault notes > procedures > model weights > nothing
```

If the user says "DON'T do X" and a vault note says "X is required," the user wins. Always. No exceptions.

## Why This Exists

A vault design doc, architecture note, or procedure could contradict the user's explicit live directive, and VaultBot might obey the note over the user. This procedure exists to detect explicit directives (negations, prohibitions, mandates) and flag any vault note that contradicts them. The key tradeoff: when a conflict exists, the user directive wins and the note is flagged for review — never obeyed.

## Inputs

- `intent`: the user's raw request/message (required)
- `vault_context`: the vault context already retrieved for this turn (optional — if omitted, the procedure searches)

## Steps

### Step 1: Detect explicit directives

1. ```python
intent = args.get("intent", "")
vault_context = args.get("vault_context", "")

# Directive patterns: negations, prohibitions, mandates
import re

directives = []

# Pattern 1: "NO X", "DON'T X", "NEVER X", "DO NOT X"
negation_patterns = [
    r"\bNO\s+([A-Z][A-Z_ ]+)\b",           # NO SMALL MODEL GATE
    r"\bDON'?T\s+(.+?)(?:\.|$)",           # DON'T add a gate
    r"\bNEVER\s+(.+?)(?:\.|$)",            # NEVER override my settings
    r"\bDO\s+NOT\s+(.+?)(?:\.|$)",         # DO NOT use small model mode
    r"\bSTOP\s+(.+?)(?:\.|$)",             # STOP gating tools
]

# Pattern 2: "ALWAYS X", "MUST X", "ONLY X"
mandate_patterns = [
    r"\bALWAYS\s+(.+?)(?:\.|$)",
    r"\bMUST\s+(.+?)(?:\.|$)",
    r"\bONLY\s+(.+?)(?:\.|$)",
]

# Pattern 3: "REMOVE X", "DELETE X", "UNDO X"
action_patterns = [
    r"\bREMOVE\s+(.+?)(?:\.|$)",
    r"\bDELETE\s+(.+?)(?:\.|$)",
    r"\bUNDO\s+(.+?)(?:\.|$)",
    r"\bREVERT\s+(.+?)(?:\.|$)",
]

all_patterns = negation_patterns + mandate_patterns + action_patterns
for pat in all_patterns:
    for match in re.finditer(pat, intent, re.IGNORECASE):
        directives.append({
            "type": "negation" if pat in negation_patterns else ("mandate" if pat in mandate_patterns else "action"),
            "text": match.group(0).strip(),
            "target": match.group(1).strip() if match.lastindex else match.group(0).strip(),
        })

result = json.dumps({"directives": directives, "count": len(directives)})
```

### Step 2: Search vault for conflicting notes

2. ```python
import json

step1 = json.loads(prior_results[0]) if prior_results else {}
directives = step1.get("directives", [])

if not directives:
    result = json.dumps({"conflict": False, "reason": "no explicit directives detected"})
else:
    # Build search queries from directive targets
    targets = [d["target"] for d in directives]
    targets_str = ", ".join(targets)
    
    # Search for notes that might contradict
    # We'll search for each target term
    result = json.dumps({
        "conflict_possible": True,
        "directives": directives,
        "targets": targets,
        "instructions": f"Search vault for notes about: {targets_str}. For each note found, check if it contradicts any directive. If it does, FLAG the note — do NOT obey it. The user's directive wins."
    })
```

### Step 3: Resolve conflicts — user always wins

3. ```python
import json

step2 = json.loads(prior_results[1]) if len(prior_results) > 1 else {}
directives = step2.get("directives", [])

# The resolution is simple: user directives win. Always.
# Any vault note that contradicts a user directive is flagged, not obeyed.
# The procedure returns a "constraint" object that downstream steps
# (Route-Task, any procedure) MUST check before acting.

constraints = []
for d in directives:
    constraints.append({
        "type": d["type"],
        "directive": d["text"],
        "rule": f"DO NOT contradict this directive. If any vault note, procedure, or architecture doc says otherwise, the note is wrong — not the user.",
    })

result = json.dumps({
    "conflict_resolved": True,
    "constraints": constraints,
    "override_rule": "USER DIRECTIVE > vault notes > procedures > model weights",
    "instructions": "Pass these constraints to Route-Task and all downstream procedures. If any step would violate a constraint, STOP and report the conflict to the user."
})
```

## Usage

Call this BEFORE Route-Task:

```
execute_procedure('User-Directive-Override-Check', args={'intent': '<user request>'})
```

Then pass the constraints to Route-Task:

```
execute_procedure('Route-Task', args={'intent': '<user request>', 'constraints': <constraints from check>})
```

## Falsifiability

This procedure is falsifiable if:
- A user directive is overridden by a vault note despite this check running (testable: feed it "NO X" + a note that says "X is required" and verify the note is flagged)
- A false positive flags a non-conflicting note (testable: feed it normal requests and verify no false constraints)
- The check misses a directive (testable: feed it edge cases like "i don't want any gates" and verify detection)

## Related

- [[Route-Task]] — the router this check runs before
- [[Authority-Check]] — resolves authority conflicts between user and vault
- [[Preflight-Safety-Check]] — the preflight safety gate
