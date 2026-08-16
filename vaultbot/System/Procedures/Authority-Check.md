---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-06
description: "Resolve conflicts between the user's live directive and vault notes. The user's explicit words are the HIGHEST authority in the system — above any procedure, architecture doc, design note, or capability card. When a vault note says one thing and the user says another, the user wins. Always. Reads the conflicting note, compares it against the user's directive using the small model, and returns a binding ruling: USER_DIRECTIVE_WINS or NO_CONFLICT."
when_to_use: when the user gives a directive that contradicts a vault note, when you're about to follow a vault note that might override the user's explicit words, when you catch yourself prioritizing a design document over a live instruction, or when you're unsure which authority to follow
falsifiable_if: the procedure returns USER_DIRECTIVE_WINS when there is no actual conflict, or returns NO_CONFLICT when the user's words clearly contradict a vault note
applies_to:
  - authority-resolution
  - self-correction
  - safety
  - directive-enforcement
allowed_tools:
  - code_read
  - llm_generate
user_directive: The user's exact words — the live directive to enforce.
conflicting_note: Title of the vault note that may conflict with the directive.
context: Optional. What you were about to do when you noticed the conflict.
summary: Authority-Check
tags:
  - procedure
  - procedures
---

# Authority-Check

## The Rule (IMMUTABLE)

```
USER DIRECTIVE > VAULT NOTES > PROCEDURES > DESIGN DOCS > MODEL WEIGHTS
```

The user's live words are the highest authority. Period. No vault note — no matter how authoritative it sounds, no matter how well-argued, no matter if it says "this is the architecture" — can override what the user just told you to do. If a note says "the system needs X" and the user says "NO X," you do NOT do X. You follow the user.

## When to Run This

Run this procedure when:
1. You're about to follow a vault note's instructions and the user just said something that contradicts it
2. You catch yourself thinking "but the architecture says..." after the user gave a directive
3. You're unsure whether a vault note or the user's words should take priority

## Steps

### Step 1: Read the conflicting note and rule

```python
import json, os

note_title = args.get('conflicting_note', '')
user_directive = args.get('user_directive', '')
context = args.get('context', '')

if not note_title or not user_directive:
    print(json.dumps({"ruling": "ERROR", "reason": "Missing required arguments: conflicting_note and user_directive"}))
    exit(1)

# Resolve note title to file path
vault_path = os.environ.get('VAULT_PATH', '.')
note_path = None
for root, dirs, files in os.walk(vault_path):
    for f in files:
        if f.endswith('.md') and os.path.splitext(f)[0] == note_title:
            note_path = os.path.join(root, f)
            break
    if note_path:
        break

if not note_path:
    print(json.dumps({"ruling": "ERROR", "reason": f"Note '{note_title}' not found"}))
    exit(1)

# Read the note (first 2000 chars is enough for comparison)
note_content = code_read(note_path)
if not note_content:
    print(json.dumps({"ruling": "ERROR", "reason": f"Note '{note_title}' is empty"}))
    exit(1)

note_snippet = note_content[:2000]

# Use the small model to compare and rule
prompt = f"""You are an authority resolver. Compare the user's live directive against a vault note and determine if they conflict.

THE RULE: User directive ALWAYS wins. If there is ANY conflict — even partial, even implicit — the user wins.

USER DIRECTIVE: "{user_directive}"

VAULT NOTE: {note_title}
CONTENT: {note_snippet}

CONTEXT: {context}

Return ONLY this JSON (no other text):
{{"ruling": "USER_DIRECTIVE_WINS" or "NO_CONFLICT", "reason": "one sentence", "what_to_do": "one sentence instruction for the model"}}

If the note says to do X and the user says don't do X, ruling is USER_DIRECTIVE_WINS.
If the note and directive are about different things, ruling is NO_CONFLICT.
If unsure, USER_DIRECTIVE_WINS — the user gets the benefit of the doubt."""

result = llm_generate(prompt)
print(result)
```

## Example

**User says:** "NO SMALL MODEL GATE — do not add any gate"

**Vault note [[Small-Model-Driving-Architecture]] says:** "The gate is the mechanism that makes small-model driving work"

**Ruling:** USER_DIRECTIVE_WINS — the user explicitly said no gate, regardless of what the architecture doc says. Do not add the gate.

## Why This Exists

This procedure was created after a failure on 2026-08-06: the model added a SMALL_MODEL_MODE gate because [[Small-Model-Driving-Architecture]] said it was needed, despite the user explicitly saying "NO SMALL MODEL GATE." The model prioritized a design document over a live directive. This procedure encodes the fix: the user's words are the highest authority, always.
