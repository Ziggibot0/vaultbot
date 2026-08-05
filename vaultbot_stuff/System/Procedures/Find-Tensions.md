---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-03
description: Find notes in the vault that take opposing positions on the same question. Scans architecture/semantic/claim notes for contradiction signals (status conflicts on shared dependency, prose contradiction words near wikilinks, explicit rejection language). Returns candidate tension pairs with the evidence context. One small-model call classifies whether each pair is a real tension or noise. Use when auditing vault coherence, before Dream-Pass, or when asked 'does the vault contradict itself'.
when_to_use: when auditing vault coherence, before a Dream-Pass, when asked 'does the vault contradict itself', or when checking if notes that depend on the same concept take compatible positions
applies_to:
  - vault-coherence
  - contradiction-detection
  - knowledge-quality
  - vault-maintenance
allowed_tools:
  - vault_list
  - code_read
  - llm_generate
falsifiable_if: a flagged tension is not actually a real disagreement (both notes agree), or a real tension is missed
summary: Find-Tensions
tags:
  - procedure
  - procedures
---

# Find-Tensions

Finds pairs of vault notes that take opposing positions on the same question.
This is claim-vs-claim contradiction detection (not note-vs-code like
[[Find-Contradictions]]). The vault does the detection work deterministically;
the small model only classifies whether each candidate pair is real.

## What It Does

1. Scans `System/`, `Knowledge/`, and notes with `type: architecture`/`semantic`/
   `pattern` for three deterministic tension signals:
   - **Status conflict on shared dependency** — two notes that both
     `depends_on` the same concept but have incompatible `status` (e.g., one
     `verified`, one `superseded`).
   - **Prose contradiction signals near wikilinks** — a note that links to
     another note using language like "rejected", "contradicts", "wrong",
     "overturned", "conflicts with" near the link.
   - **Explicit rejection patterns** — notes containing "rejected",
     "overturned", "not enough evidence" in reference to a named concept
     or note.
2. One small-model batch call classifies: for each candidate pair, is this
   a genuine tension (two notes disagree on a substantive claim) or noise
   (incidental word match, historical reference, unrelated topic)?
3. Returns the confirmed tensions with evidence context.

## Steps

### Step 1: Collect candidate tension pairs (deterministic, zero LLM)

1. ```python
import os, re, json
from collections import defaultdict

# --- Parse frontmatter ---
def parse_fm(text):
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm_str = text[3:end].strip()
    fm = {}
    cur = None
    for line in fm_str.split("\n"):
        line = line.rstrip()
        if not line:
            continue
        if line.startswith("  - ") and cur:
            v = line[4:].strip().strip('"').strip("'")
            if not isinstance(fm.get(cur), list):
                fm[cur] = [fm[cur]] if cur in fm and fm[cur] else []
            fm[cur].append(v)
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            if v:
                fm[k] = v
                cur = k
            else:
                cur = k
    return fm

WIKILINK_RE = re.compile(r'\[\[([^\][\|\r\n]+)(?:\|[^\]\r\n]+)?\]\]')
IGNORED_DIRS = {'.venv','vaultbot_venv','vaultbot_index','sessions','partials',
                '.git','.obsidian','node_modules','__pycache__','vaultbot_backend'}

# Tension signal keywords (curated for precision over recall).
# Multi-word phrases only — single words like 'wrong' or 'rejected'
# generate ~10x false positives ("what went wrong", "topic choice is wrong").
CONTRA_SIGNALS = [
    'contradicts', 'overturned', 'not enough evidence',
    'conflicts with', 'disagrees', 'too much maintenance',
    'does not justify', 'not strong enough',
    'should not build', 'rejected because', 'rejected: ',
    'not overturned', 'rejected but',
]

# Walk all .md files, build note index
notes = {}
for root, dirs, files in os.walk(vault_path):
    dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
    for f in files:
        if not f.endswith(".md"):
            continue
        path = os.path.join(root, f)
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        stem = f[:-3]
        fm = parse_fm(text)
        body = re.sub(r'^\s*---.*?---\s*', '', text, count=1, flags=re.DOTALL)
        links = set(WIKILINK_RE.findall(text))
        # Only collect notes that could contain claims/positions
        note_type = fm.get('type', '')
        is_claim_like = (
            note_type in ('architecture', 'semantic', 'pattern', 'claim',
                         'pattern-highway', 'diagnostic', 'system-design',
                         'architecture-plan', 'synthesis', 'concept')
            or 'System/' in path.replace("\\", "/")
            or 'Knowledge/' in path.replace("\\", "/")
        )
        if not is_claim_like:
            continue
        notes[stem] = {
            'path': str(Path(path).relative_to(vault_path)).replace("\\", "/"),
            'fm': fm, 'type': note_type, 'status': fm.get('status', ''),
            'links': links, 'body': body, 'title': stem,
        }

# --- Signal 1: Status conflict on shared dependency ---
# Two notes that depends_on the same target but have incompatible status
dep_targets = defaultdict(set)  # dep_target -> {(stem, status)}
for stem, info in notes.items():
    deps = info['fm'].get('depends_on', [])
    if isinstance(deps, str):
        deps = [deps]
    for dep in deps:
        dep_clean = dep.strip('[]').strip()
        dep_targets[dep_clean].add((stem, info['status']))

status_tensions = []
for target, sources in dep_targets.items():
    statuses = {s for _, s in sources if s}
    # Flag if one is verified/complete and another is superseded/deprecated/flagged
    good = {'verified', 'complete', 'active'}
    bad = {'superseded', 'deprecated', 'flagged', 'rejected'}
    if statuses & good and statuses & bad:
        status_tensions.append({
            'target': target,
            'notes': [{'stem': s, 'status': st, 'path': notes[s]['path']}
                       for s, st in sorted(sources)],
            'signal': 'status_conflict_on_shared_dependency',
        })

# --- Signal 2: Prose contradiction near wikilinks ---
# A note links to another note with contradiction language in surrounding context
prose_tensions = []
for stem, info in notes.items():
    body = info['body']
    for link in info['links']:
        link_pos = body.find(f'[[{link}')
        if link_pos == -1:
            continue
        context = body[max(0, link_pos - 250):link_pos + 250].lower()
        for signal in CONTRA_SIGNALS:
            if signal in context:
                # Verify the target note exists in our claim-like set
                target_stem = link.strip()
                if target_stem in notes:
                    prose_tensions.append({
                        'source': stem,
                        'target': target_stem,
                        'signal_word': signal,
                        'source_path': info['path'],
                        'target_path': notes[target_stem]['path'],
                        'context': body[max(0, link_pos - 200):link_pos + 200],
                    })
                break

# --- Signal 3: Notes that reference rejection of a concept by name ---
# Notes containing "rejected" or "not enough evidence" near a note title
rejection_tensions = []
for stem, info in notes.items():
    body_lower = info['body'].lower()
    for signal in ['rejected because', 'not strong enough', 'should not build',
                   'does not justify', 'too much maintenance']:
        if signal not in body_lower:
            continue
        # Find which other note titles appear near this signal
        for other_stem, other_info in notes.items():
            if other_stem == stem:
                continue
            # Check if the other note's title appears within 300 chars of signal
            sig_pos = body_lower.find(signal)
            title_lower = other_stem.lower()
            title_pos = body_lower.find(title_lower, max(0, sig_pos - 300),
                                        sig_pos + 300)
            if title_pos != -1:
                rejection_tensions.append({
                    'source': stem,
                    'target': other_stem,
                    'signal_word': signal,
                    'source_path': info['path'],
                    'target_path': other_info['path'],
                    'context': info['body'][max(0, sig_pos - 200):sig_pos + 200],
                })
                break  # one signal per (source, target) pair

# Deduplicate: merge overlapping pairs
seen_pairs = set()
all_candidates = []
for t in status_tensions:
    key = ('status', t['target'])
    if key not in seen_pairs:
        all_candidates.append(t)
        seen_pairs.add(key)
for t in prose_tensions + rejection_tensions:
    pair = tuple(sorted([t['source'], t.get('target', '')]))
    if pair not in seen_pairs:
        all_candidates.append(t)
        seen_pairs.add(pair)

result = json.dumps({
    'total_notes_scanned': len(notes),
    'status_tensions': len(status_tensions),
    'prose_tensions': len(prose_tensions),
    'rejection_tensions': len(rejection_tensions),
    'candidates': all_candidates[:40],
    'total_candidates': len(all_candidates),
}, indent=2)
```

### Step 2: Small-model batch classification (one LLM call)

2. ```python
import json as _json, re as _re

data = _json.loads(output)
candidates = data.get("candidates", [])

if not candidates:
    result = _json.dumps({"confirmed_tensions": [], "reason": "no candidates"})
else:
    # Build a detailed batch prompt — give the model enough context to judge
    lines = []
    for i, c in enumerate(candidates):
        if c.get('signal') == 'status_conflict_on_shared_dependency':
            notes_list = "; ".join(
                f"{n['stem']} (status:{n['status']})" for n in c['notes']
            )
            lines.append(
                f"{i}. STATUS CONFLICT: Two notes depend on [{c['target']}], "
                f"but one is verified/active while another is superseded/flagged. "
                f"Notes: {notes_list}"
            )
        else:
            ctx = c.get('context', '')[:300].replace('\n', ' ')
            lines.append(
                f"{i}. {c['source']} uses '{c.get('signal_word','')}' "
                f"near a link to {c.get('target','?')}. "
                f"Context: {ctx}"
            )

    batch_text = "\n".join(lines)

    prompt = f"""You are auditing a knowledge vault for internal contradictions.
For each candidate below, decide if it is a REAL_TENSION (two notes genuinely
disagree on a substantive architectural or factual claim) or NOISE (incidental
word match, historical reference, or unrelated topic).

Respond with ONLY a JSON list of indices that are REAL_TENSION.
Format: [0, 3, 5] or [] if none are real.

Candidates:
{batch_text}"""

    classification = llm_generate(prompt)

    # Parse the model's response — handle [0]\n[3] and [0, 3] formats
    nums = _re.findall(r'\d+', classification)
    real_indices = set(int(n) for n in nums if int(n) < len(candidates))

    confirmed = []
    for i, c in enumerate(candidates):
        if i in real_indices:
            confirmed.append(c)

    result = _json.dumps({
        'confirmed_tensions': confirmed,
        'total_candidates': len(candidates),
        'confirmed_count': len(confirmed),
        'raw_classification': classification,
    }, indent=2)
```

### Step 3: Format the final report

3. ```python
import json as _json2

data = _json2.loads(output)
tensions = data.get("confirmed_tensions", [])


if not tensions:
    result = "No confirmed tensions found. Vault appears internally consistent on scanned notes."
else:
    lines = [f"# Vault Tension Report\n"]
    lines.append(f"Found {len(tensions)} confirmed tension(s) across "
                 f"{data.get('total_candidates', 0)} candidates.\n")
    for i, t in enumerate(tensions):
        lines.append(f"## Tension {i+1}")
        if t.get('signal') == 'status_conflict_on_shared_dependency':
            lines.append(f"**Signal:** Status conflict on shared dependency")
            lines.append(f"**Shared target:** {t['target']}")
            for n in t['notes']:
                lines.append(f"  - {n['stem']} (status: {n['status']}) "
                             f"-> {n['path']}")
        else:
            lines.append(f"**Signal:** {t.get('signal_word', 'prose')}")
            lines.append(f"**Source:** {t['source']} ({t['source_path']})")
            lines.append(f"**Target:** {t.get('target', '?')} "
                         f"({t.get('target_path', '?')})")
            ctx = t.get('context', '')[:300].replace('\n', ' ')
            lines.append(f"**Context:** {ctx}")
        lines.append("")
    result = "\n".join(lines)
```