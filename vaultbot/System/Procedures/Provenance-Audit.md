---
type: procedure
status: experimental
baseline: true
created: 2026-08-11
summary: "Audit a procedure note for missing research provenance. Extracts claims/design decisions, checks each for wikilinks to research notes or inline citations, reports gaps. LLM identifies claims (semantic), code checks for citations (structural)."
description: "Provenance audit: extract claims from a procedure note, verify each has a wikilink or citation to a research source, report missing provenance."
allowed_tools:
  - vault_search
  - vault_read_note
  - llm_generate
  - code_read
tags: [procedure, audit, provenance, quality, research]
---

# Provenance Audit

## Purpose

Systematically check a procedure note for missing research provenance. Every design decision, cognitive science claim, and architectural choice should cite a vault research note via wikilink or have an inline `[sources: ...]` citation. This procedure finds the gaps.

## Design Principle

Claim extraction is **semantic** — the LLM identifies what counts as a claim needing provenance. Citation checking is **structural** — deterministic code verifies wikilinks resolve and citations exist. No regex trying to understand what a claim is.

## Inputs

- `note_path`: Path to the procedure note to audit (relative to vault root)

## Outputs

- List of claims found in the note
- For each claim: whether it has provenance (wikilink or citation)
- List of claims missing provenance
- Overall provenance coverage percentage

---

### Step 1: Read the procedure note and extract the body text

```python
note_path = args.get('note_path', '')
if not note_path:
    print('ERROR: note_path argument required')
    exit(1)

from pathlib import Path
p = Path(note_path)
if not p.exists():
    # Try resolving relative to vault root
    vault = Path(os.environ.get('VAULT_PATH', '.'))
    p = vault / note_path
if not p.exists():
    print(f'ERROR: note not found: {note_path}')
    exit(1)

note_text = p.read_text(encoding='utf-8', errors='replace')
note_name = p.stem

# Strip frontmatter for claim extraction (frontmatter is metadata, not claims)
body = note_text
if body.startswith('---'):
    end = body.find('\n---', 3)
    if end != -1:
        body = body[end + 4:]

# Strip code blocks (code is implementation, not claims)
lines = body.split('\n')
in_code = False
prose_lines = []
for line in lines:
    if line.strip().startswith('```'):
        in_code = not in_code
        continue
    if not in_code:
        prose_lines.append(line)

prose = '\n'.join(prose_lines)

result = f"NOTE: {note_name}\nPROSE_LENGTH: {len(prose)} chars\nNOTE_PATH: {note_path}"
print(result)
```

[validate: contains "NOTE:"]
[validate: contains "NOTE_PATH"]

---

### Step 2: Extract claims that need provenance (triple-try)

The LLM identifies design decisions, cognitive science claims, and architectural choices that should have research backing. This is a semantic task — understanding what counts as a claim. Triple-try for consistency.

```python
# Parse Step 1
lines = output.strip().split('\n')
note_name = ''
note_path = ''
for line in lines:
    if line.startswith('NOTE: '):
        note_name = line.replace('NOTE: ', '').strip()
    elif line.startswith('NOTE_PATH: '):
        note_path = line.replace('NOTE_PATH: ', '').strip()

# Re-read the prose (code blocks stripped)
from pathlib import Path
p = Path(note_path)
if not p.exists():
    vault = Path(os.environ.get('VAULT_PATH', '.'))
    p = vault / note_path
note_text = p.read_text(encoding='utf-8', errors='replace')
body = note_text
if body.startswith('---'):
    end = body.find('\n---', 3)
    if end != -1:
        body = body[end + 4:]
lines_body = body.split('\n')
in_code = False
prose_lines = []
for line in lines_body:
    if line.strip().startswith('```'):
        in_code = not in_code
        continue
    if not in_code:
        prose_lines.append(line)
prose = '\n'.join(prose_lines)

prompt = f"""You are auditing a procedure note for research provenance. Read the following prose (code blocks removed) from the procedure note "{note_name}".

Identify CLAIMS that need research provenance. A claim is:
- A design decision (e.g., "we use triple-try because...")
- A cognitive science assertion (e.g., "System 1 jumps to conclusions")
- An architectural choice (e.g., "the synthesis uses the big LLM")
- A research finding (e.g., "studies show that structured questioning improves reasoning")

Do NOT include:
- Implementation details (how code works)
- Metadata (frontmatter, tags)
- Formatting instructions
- Obvious statements that don't need research backing

For each claim, output:
CLAIM: [the claim text, quoted from the note]
CONTEXT: [1-2 words: where in the note it appears]

List ALL claims you find, one per pair of lines. If no claims need provenance, output:
CLAIM: none

Prose from "{note_name}":
{prose[:4000]}
"""

# Triple-try
responses = []
for i in range(3):
    resp = llm_generate(prompt).strip()
    responses.append(resp)

# Parse claims from each response
def parse_claims(text):
    claims = []
    current_claim = None
    for line in text.split('\n'):
        if line.startswith('CLAIM: '):
            if current_claim:
                claims.append(current_claim)
            claim_text = line.replace('CLAIM: ', '').strip()
            if claim_text.lower() == 'none':
                return []
            current_claim = {'claim': claim_text, 'context': ''}
        elif line.startswith('CONTEXT: ') and current_claim:
            current_claim['context'] = line.replace('CONTEXT: ', '').strip()
    if current_claim:
        claims.append(current_claim)
    return claims

all_claim_sets = [parse_claims(r) for r in responses]

# Union all claims (deduplicated by claim text, case-insensitive)
seen = set()
all_claims = []
for claim_set in all_claim_sets:
    for c in claim_set:
        key = c['claim'].lower()[:100]  # dedupe by first 100 chars
        if key not in seen:
            seen.add(key)
            all_claims.append(c)

# Format for next step
claim_lines = []
for i, c in enumerate(all_claims, 1):
    claim_lines.append(f"CLAIM_{i}: {c['claim']}")
    claim_lines.append(f"CONTEXT_{i}: {c['context']}")

result = f"NOTE: {note_name}\nNOTE_PATH: {note_path}\nTOTAL_CLAIMS: {len(all_claims)}\n" + '\n'.join(claim_lines)
print(result)
```

[validate: contains "TOTAL_CLAIMS"]

---

### Step 3: Check each claim for provenance (structural)

For each claim, check whether the original note text contains a wikilink or `[sources:` citation near the claim. This is structural — code searches for citation patterns in the vicinity of the claim text.

```python
# Parse Step 2
lines = output.strip().split('\n')
note_name = ''
note_path = ''
total_claims = 0
claims = {}
current_num = None

for line in lines:
    if line.startswith('NOTE: '):
        note_name = line.replace('NOTE: ', '').strip()
    elif line.startswith('NOTE_PATH: '):
        note_path = line.replace('NOTE_PATH: ', '').strip()
    elif line.startswith('TOTAL_CLAIMS: '):
        total_claims = int(line.replace('TOTAL_CLAIMS: ', '').strip())
    elif line.startswith('CLAIM_') and ': ' in line:
        parts = line.split(': ', 1)
        num = parts[0].replace('CLAIM_', '')
        claims[num] = {'claim': parts[1]}
        current_num = num
    elif line.startswith('CONTEXT_') and current_num:
        parts = line.split(': ', 1)
        claims[current_num]['context'] = parts[1] if len(parts) > 1 else ''

# Re-read the full note text (with code blocks, since citations might be in code comments)
from pathlib import Path
p = Path(note_path)
if not p.exists():
    vault = Path(os.environ.get('VAULT_PATH', '.'))
    p = vault / note_path
full_text = p.read_text(encoding='utf-8', errors='replace')

# For each claim, check if there's a wikilink or [sources: citation near the claim text
# "Near" = within 500 chars of the claim text in the original note
import re as _re

def check_provenance(claim_text, full_text):
    """Check if a claim has provenance: a wikilink or [sources: citation nearby."""
    # Find the claim in the full text
    claim_lower = claim_text.lower()[:80]  # use first 80 chars for matching
    text_lower = full_text.lower()
    
    # Find approximate position of the claim
    pos = text_lower.find(claim_lower)
    if pos == -1:
        # Try first 40 chars
        pos = text_lower.find(claim_lower[:40])
    if pos == -1:
        # Claim not found in text — can't verify
        return 'claim_not_found'
    
    # Check within 500 chars before and after the claim
    window_start = max(0, pos - 500)
    window_end = min(len(full_text), pos + len(claim_text) + 500)
    window = full_text[window_start:window_end]
    
    # Check for wikilinks [[...]] in the window
    wikilinks = _re.findall(r'\[\[([^\]]+)\]\]', window)
    if wikilinks:
        return f'wikilink: {wikilinks[0]}'
    
    # Check for [sources: ...] citations in the window
    sources = _re.findall(r'\[sources?:\s*([^\]]+)\]', window, re.IGNORECASE)
    if sources:
        return f'citation: {sources[0][:60]}'
    
    return 'missing'

# Check each claim
audit_results = []
missing_count = 0
for num, claim_data in sorted(claims.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0):
    status = check_provenance(claim_data['claim'], full_text)
    audit_results.append(f"CLAIM_{num}: {claim_data['claim'][:80]}")
    audit_results.append(f"STATUS_{num}: {status}")
    if status == 'missing':
        missing_count += 1

total = len(claims)
coverage = ((total - missing_count) / total * 100) if total > 0 else 100

result_lines = [
    f"NOTE: {note_name}",
    f"NOTE_PATH: {note_path}",
    f"TOTAL_CLAIMS: {total}",
    f"MISSING_PROVENANCE: {missing_count}",
    f"COVERAGE: {coverage:.0f}%",
]
result_lines.extend(audit_results)
result = '\n'.join(result_lines)
print(result)
```

[validate: contains "COVERAGE"]
[validate: contains "MISSING_PROVENANCE"]

---

### Step 4: Report missing provenance with recommendations

Summarize the audit results. List claims missing provenance with a recommendation for what research note should be linked.

```python
# Parse Step 3
lines = output.strip().split('\n')
note_name = ''
note_path = ''
total_claims = 0
missing_count = 0
coverage = ''
missing_claims = []
all_status = {}
current_num = None

for line in lines:
    if line.startswith('NOTE: '):
        note_name = line.replace('NOTE: ', '').strip()
    elif line.startswith('NOTE_PATH: '):
        note_path = line.replace('NOTE_PATH: ', '').strip()
    elif line.startswith('TOTAL_CLAIMS: '):
        total_claims = int(line.replace('TOTAL_CLAIMS: ', '').strip())
    elif line.startswith('MISSING_PROVENANCE: '):
        missing_count = int(line.replace('MISSING_PROVENANCE: ', '').strip())
    elif line.startswith('COVERAGE: '):
        coverage = line.replace('COVERAGE: ', '').strip()
    elif line.startswith('CLAIM_') and ': ' in line:
        parts = line.split(': ', 1)
        current_num = parts[0].replace('CLAIM_', '')
    elif line.startswith('STATUS_') and ': ' in line:
        parts = line.split(': ', 1)
        num = parts[0].replace('STATUS_', '')
        status = parts[1].strip()
        if status == 'missing':
            missing_claims.append(num)

# Build report
report_lines = [
    f"PROVENANCE AUDIT REPORT",
    f"========================",
    f"Note: {note_name}",
    f"Path: {note_path}",
    f"Total claims checked: {total_claims}",
    f"Claims with provenance: {total_claims - missing_count}",
    f"Claims missing provenance: {missing_count}",
    f"Coverage: {coverage}",
    "",
]

if missing_claims:
    report_lines.append("MISSING PROVENANCE:")
    for num in missing_claims:
        # Find the claim text from the audit results
        for line in lines:
            if line.startswith(f'CLAIM_{num}: '):
                claim_text = line.replace(f'CLAIM_{num}: ', '').strip()
                report_lines.append(f"  - [{num}] {claim_text}")
                break
    report_lines.append("")
    report_lines.append("RECOMMENDATION: Add wikilinks to relevant research notes or [sources: ...] citations for each missing claim.")
else:
    report_lines.append("All claims have provenance. No gaps found.")

result = '\n'.join(report_lines)
print(result)
```

[validate: contains "PROVENANCE AUDIT REPORT"]
[validate: contains "Coverage"]