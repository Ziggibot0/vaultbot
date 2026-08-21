---
type: procedure
status: experimental
baseline: true
model_cartridge: big
created: 2026-08-15
description: "Feedback-driven trigger/inhibitor update for procedure and note retrieval. Scans session JSONL logs for model_relevance_tags events, pairs each with the user's next-message sentiment, applies an evidence threshold, LLM-distills query texts into concise trigger/inhibitor phrases, and patches note frontmatter. Closes the retrieval feedback loop: notes that proved helpful earn trigger phrases; notes that the user reacted negatively to earn inhibitor phrases. The retrieval gate then drops notes whose inhibitors match the query."
when_to_use: "During Dream Pass (after Dream-Evaluate). When tuning trigger/inhibitor fields from user feedback. When procedure retrieval surfaces noise. When the vault has accumulated chat sessions with model_relevance_tags events. When improving retrieval precision by learning what NOT to surface. When closing the feedback loop between user sentiment and retrieval gating."
falsifiable_if: "After running, trigger/inhibitor phrases written to notes are semantically wrong (distillation produced garbage), or the evidence threshold let a single noisy turn write a phrase, or notes that should have been gated are still surfaced."
allowed_tools:
  - vault_list
  - vault_read_note
  - llm_generate
  - md_safe_replace
  - code_read
tags:
  - procedure
  - dream-pass
  - trigger
  - inhibitor
  - retrieval
  - rag
  - self-improvement
  - feedback-loop
  - frontmatter
summary: "Dream-Trigger-Inhibitor-Update reads session logs for model_relevance_tags events, pairs each with the user's next-message sentiment (positive/negative/neutral via keyword matching), aggregates per-note signals with a 2+ evidence threshold, LLM-distills query texts into 3-8 word phrases, deduplicates against existing trigger/inhibitor lists, and patches note frontmatter via md_safe_replace. This closes the retrieval feedback loop: the gate drops notes whose inhibitors match the query, so the model sees less noise over time."
---

# Dream-Trigger-Inhibitor-Update

## Purpose

This is the **offline half** of the trigger/inhibitor feedback loop. The online half (`model_relevance_tags` in `chat_handler.py`) logs which retrieved notes the model cited as `useful` vs `neutral` after each turn. This procedure reads those logs, pairs each tag with the user's *next* message (classified for sentiment), and writes the result back into the note's `trigger` and `inhibitor` frontmatter fields.

**Why this exists:** The retrieval gate (`fused_retrieval.py`) drops notes whose `inhibitor` phrases match the query more strongly than their `trigger` phrases. But inhibitors have to come from *somewhere* — they're earned. This procedure is where they're earned: from real user reactions. A note that the user reacted negatively to gets an inhibitor phrase derived from that query. Next time a similar query comes in, the gate drops it.

**Data flow:**

```
Session JSONL (chronological lines):
  {"event": "websocket_message", "data": {"direction": "in", "payload": {"message": "user text"}}}
  ... (chat handler runs, retrieval surfaces notes, model answers) ...
  {"event": "model_relevance_tags", "data": {"query": "...", "tags": [{"path","stem","tag":"useful"|"neutral"}], ...}}
  ... (model delivers answer, user sees it) ...
  {"event": "websocket_message", "data": {"direction": "in", "payload": {"message": "user's reaction"}}}  ← sentiment source
```

Each `model_relevance_tags` event is paired with the **next** `websocket_message` (direction "in") in the same session file. That message's text is classified for sentiment. The pairing is the feedback signal.

## Why This Exists

The retrieval gate drops notes whose `inhibitor` phrases match the query, but those inhibitors have to come from somewhere — they're earned from real user reactions. This procedure exists as the offline half of that feedback loop, reading `model_relevance_tags` logs and writing trigger/inhibitor phrases back into note frontmatter. The key tradeoff is an evidence threshold (default 2) so a single noisy turn can't poison a note's trigger/inhibitor list.

## Inputs

- `evidence_threshold`: Minimum consistent signals before writing a phrase (default: 2). A single noisy turn (sarcasm, terse "ok") cannot poison a trigger/inhibitor.
- `max_phrases`: Cap on trigger/inhibitor phrases per note (default: 15).
- `target_note`: Optional — only update a specific note by path stem.

## Output Contract

**File written:** `vaultbot/Memory/Build-Log/trigger-inhibitor-update.json`

Human-readable summary is printed as the final output.

---

## Steps

### Step 1: Scan session logs for model_relevance_tags events + pair with next user message

Walk every session JSONL file. For each `model_relevance_tags` event, find the next `websocket_message` (direction "in") that follows it in the same file — that's the user's reaction. Classify the reaction's sentiment using the same keyword sets as `pattern_extractor.py` (inlined below — the procedure can't import Python modules).

```python
import json
import os
from pathlib import Path

# Resolve vault root (use injected vault_path from wrapper)
vault_root = Path(vault_path)
sessions_dir = vault_root / "vaultbot" / "vaultbot_backend" / "sessions"

if not sessions_dir.is_dir():
    raise RuntimeError(
        f"Sessions directory not found: {sessions_dir}. "
        "No session logs to scan for feedback."
    )

# Sentiment keyword sets — copied verbatim from pattern_extractor.py
# (_POSITIVE_KW / _NEGATIVE_KW). The procedure can't import the module,
# so the keywords are inlined. If pattern_extractor's sets change, update
# this copy too.
_POSITIVE_KW = {
    "yes", "go ahead", "cool", "nice", "good", "great", "like", "love",
    "proceed", "begin", "please do", "definitely", "yeah", "yea", "beans",
    "go for it", "please", "exactly", "perfect", "awesome", "sweet",
    "agree", "right", "correct",
}
_NEGATIVE_KW = {
    "no", "wrong", "fix", "didn't", "didnt", "lagging", "junk", "stale",
    "break", "broke", "not convinced", "don't trust", "huge", "didn't read",
    "too much", "i thought you already", "sync yourself", "dinosaur",
    "empty files", "haven't", "not what i", "are you sure", "double check",
    "make sure",
}

def _classify_sentiment(text):
    """Classify a user message as positive/negative/neutral via keywords."""
    text_lower = (text or "").lower()
    for kw in _POSITIVE_KW:
        if kw in text_lower:
            return "positive"
    for kw in _NEGATIVE_KW:
        if kw in text_lower:
            return "negative"
    return "neutral"

# Walk session files, extract (model_relevance_tags, next_user_message) pairs.
feedback = []  # list of {file_path, stem, tag, sentiment, query}

session_files = sorted(sessions_dir.glob("*.jsonl"))
if not session_files:
    print("FEEDBACK_COUNT: 0")
    print("FEEDBACK: []")
    raise SystemExit(0)

for sf in session_files:
    # Read all lines, parse JSON, keep a running list so we can look ahead.
    lines = sf.read_text(encoding="utf-8", errors="replace").splitlines()
    events = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    # Walk events; for each model_relevance_tags, find the next
    # websocket_message (direction "in") and pair them.
    for i, ev in enumerate(events):
        if ev.get("event") != "model_relevance_tags":
            continue
        data = ev.get("data", {})
        query = data.get("query", "")
        tags = data.get("tags", [])
        if not tags:
            continue

        # Find the next websocket_message (direction "in") after this event.
        user_msg = ""
        for j in range(i + 1, len(events)):
            nxt = events[j]
            if nxt.get("event") != "websocket_message":
                continue
            nxt_data = nxt.get("data", {})
            if nxt_data.get("direction") != "in":
                continue
            payload = nxt_data.get("payload", {})
            user_msg = payload.get("message", "")
            break  # first "in" message after the tags event

        if not user_msg:
            # No user reaction found — the session ended or the model
            # crashed. Skip; no sentiment to pair.
            continue

        sentiment = _classify_sentiment(user_msg)

        for tag_entry in tags:
            feedback.append({
                "file_path": tag_entry.get("path", ""),
                "stem": tag_entry.get("stem", ""),
                "tag": tag_entry.get("tag", "neutral"),
                "sentiment": sentiment,
                "query": query,
            })

print(f"FEEDBACK_COUNT: {len(feedback)}")
print(f"FEEDBACK: {json.dumps(feedback)}")
```

[validate: contains "FEEDBACK_COUNT:"]

---

### Step 2: Build per-note signal aggregates + apply evidence threshold

Group feedback by note. For each note, count trigger candidates vs inhibitor candidates. A note earns a **trigger** phrase when the model cited it (`useful`) AND the user reacted positively or neutrally. A note earns an **inhibitor** phrase when the user reacted negatively — **regardless of what the model tagged** (the user is ground truth: if the model said "useful" but the user was unhappy, the note gets an inhibitor).

Apply the evidence threshold (default 2): only keep candidates with 2+ consistent signals in the same direction. A single noisy turn can't write a phrase.

```python
import json
from collections import defaultdict

evidence_threshold = int(args.get("evidence_threshold", 2))
target = args.get("target_note", "")

# Parse Step 1
lines = output.strip().split("\n")
feedback = []
for line in lines:
    if line.startswith("FEEDBACK: "):
        feedback = json.loads(line.replace("FEEDBACK: ", "", 1).strip())
        break

if not feedback:
    print("AGGREGATES: 0")
    print("AGGREGATES: {}")
    raise SystemExit(0)

# Group by file_path
by_note = defaultdict(lambda: {"trigger_queries": [], "inhibitor_queries": []})
for f in feedback:
    fp = f["file_path"]
    stem = f["stem"]
    if target and target.lower() not in stem.lower():
        continue
    if f["sentiment"] == "negative":
        # User negative always wins — inhibitor regardless of model tag.
        by_note[fp]["inhibitor_queries"].append(f["query"])
    elif f["tag"] == "useful":
        # Model cited it + user was positive/neutral → trigger.
        by_note[fp]["trigger_queries"].append(f["query"])
    # tag=="neutral" + sentiment=="neutral" → no signal (skip)

# Apply evidence threshold: only keep notes with >= threshold consistent signals.
aggregates = {}
for fp, sigs in by_note.items():
    trig = sigs["trigger_queries"]
    inib = sigs["inhibitor_queries"]
    entry = {}
    if len(trig) >= evidence_threshold:
        entry["trigger_queries"] = trig
    if len(inib) >= evidence_threshold:
        entry["inhibitor_queries"] = inib
    if entry:
        aggregates[fp] = entry

print(f"AGGREGATES: {len(aggregates)}")
print(f"AGGREGATES: {json.dumps(aggregates)}")
```

[validate: contains "AGGREGATES:"]

---

### Step 3: LLM-distill query texts into concise trigger/inhibitor phrases

Raw user queries are long and conversational. Distill them into 3-8 word phrases that describe WHEN the note should (or shouldn't) be used — the same format as existing `when_to_use` clauses. One `llm_generate` call per note.

```python
import json

# Parse Step 2
lines = output.strip().split("\n")
aggregates = {}
for line in lines:
    if line.startswith("AGGREGATES: "):
        # Second line with the json payload (first is the count)
        pass
    if line.startswith("AGGREGATES: ") and line.replace("AGGREGATES: ", "", 1).strip().startswith("{"):
        aggregates = json.loads(line.replace("AGGREGATES: ", "", 1).strip())
        break

if not aggregates:
    print("DISTILLED: 0")
    print("DISTILLED: {}")
    raise SystemExit(0)

distilled = {}
for fp, sigs in aggregates.items():
    trigger_queries = sigs.get("trigger_queries", [])
    inhibitor_queries = sigs.get("inhibitor_queries", [])
    entry = {}

    if trigger_queries:
        # Deduplicate the raw queries (same query repeated = one signal).
        unique_queries = list(dict.fromkeys(trigger_queries))[:10]
        prompt = (
            "Distill these user queries into concise TRIGGER phrases "
            "(3-8 words each) that describe WHEN this note/procedure should "
            "be used. Each phrase should start with 'when'. Output one phrase "
            "per line, no numbering, no quotes.\n\n"
            f"Queries:\n" + "\n".join(f"- {q}" for q in unique_queries) + "\n\n"
            "Trigger phrases:"
        )
        raw = llm_generate(prompt).strip()
        phrases = [p.strip().strip('"').strip("'") for p in raw.split("\n") if p.strip()]
        # Filter: keep only lines that look like phrases (3+ words or starts with 'when').
        entry["trigger_phrases"] = [p for p in phrases if len(p) >= 10][:15]

    if inhibitor_queries:
        unique_queries = list(dict.fromkeys(inhibitor_queries))[:10]
        prompt = (
            "Distill these user queries into concise INHIBITOR phrases "
            "(3-8 words each) that describe WHEN this note/procedure should "
            "NOT be used. Each phrase should start with 'when'. Output one "
            "phrase per line, no numbering, no quotes.\n\n"
            f"Queries:\n" + "\n".join(f"- {q}" for q in unique_queries) + "\n\n"
            "Inhibitor phrases:"
        )
        raw = llm_generate(prompt).strip()
        phrases = [p.strip().strip('"').strip("'") for p in raw.split("\n") if p.strip()]
        entry["inhibitor_phrases"] = [p for p in phrases if len(p) >= 10][:15]

    if entry.get("trigger_phrases") or entry.get("inhibitor_phrases"):
        distilled[fp] = entry

print(f"DISTILLED: {len(distilled)}")
print(f"DISTILLED: {json.dumps(distilled)}")
```

[validate: contains "DISTILLED:"]

---

### Step 4: Deduplicate against existing lists + patch frontmatter

For each note with distilled phrases: read the existing `trigger` and `inhibitor` frontmatter lists, skip new phrases that are >80% word-overlapping with an existing one (redundant), enforce the 15-phrase cap, and patch via `md_safe_replace`. Insert the list after `when_to_use:` (or after `description:` if no `when_to_use`) when the list doesn't exist yet.

```python
import re
import json

max_phrases = int(args.get("max_phrases", 15))

# Parse Step 3
lines = output.strip().split("\n")
distilled = {}
for line in lines:
    if line.startswith("DISTILLED: ") and line.replace("DISTILLED: ", "", 1).strip().startswith("{"):
        distilled = json.loads(line.replace("DISTILLED: ", "", 1).strip())
        break

if not distilled:
    print("PATCHED: 0")
    print("PATCHED: []")
    raise SystemExit(0)

def _word_overlap(a, b):
    """Fraction of shared words between two phrases (0.0-1.0)."""
    wa = set(a.lower().split())
    wb = set(b.lower().split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))

def _parse_list_field(content, field_name):
    """Extract existing list items from frontmatter for a field."""
    # Match "field_name:" followed by list items "  - value" or inline.
    pattern = rf'^{field_name}:\s*(.*?)$'
    m = re.search(pattern, content, re.MULTILINE)
    if not m:
        return None  # field doesn't exist
    inline = m.group(1).strip()
    if inline == "[]" or inline == "":
        return []
    if inline.startswith("["):
        # Inline list: [a, b, c]
        items = [x.strip().strip('"').strip("'") for x in inline.strip("[]").split(",")]
        return [x for x in items if x]
    # Block list: collect "  - value" lines after this field
    items = []
    pos = m.end()
    for line in content[pos:].split("\n"):
        line = line.rstrip()
        if line.startswith("  - "):
            items.append(line[4:].strip().strip('"').strip("'"))
        elif line.startswith("  "):
            continue
        else:
            break
    return items

patched = []
for fp, entry in distilled.items():
    new_triggers = entry.get("trigger_phrases", [])
    new_inhibitors = entry.get("inhibitor_phrases", [])

    try:
        note_result = vault_read_note(fp, max_lines=0)
        if isinstance(note_result, dict):
            content = note_result.get("content", "")
        else:
            content = str(note_result)
    except Exception:
        continue

    if not content.startswith("---"):
        continue

    # Skip baseline notes — trigger/inhibitor phrases are learned from THIS
    # user's reactions, so they are personal. Writing them into a shared
    # baseline file would make every install's copy diverge.
    _fm_end = content.find("\n---", 3)
    _fm_block = content[:_fm_end] if _fm_end != -1 else content[:200]
    if "baseline: true" in _fm_block:
        continue

    changes = []
    for field_name, new_phrases in [("trigger", new_triggers), ("inhibitor", new_inhibitors)]:
        if not new_phrases:
            continue
        existing = _parse_list_field(content, field_name)
        if existing is None:
            # Field doesn't exist — insert after when_to_use or description.
            existing = []
        # Deduplicate: skip phrases that >80% overlap with an existing one.
        to_add = []
        for np in new_phrases:
            if len(existing) + len(to_add) >= max_phrases:
                break
            if any(_word_overlap(np, ep) > 0.8 for ep in existing):
                continue
            if any(_word_overlap(np, ap) > 0.8 for ap in to_add):
                continue
            to_add.append(np)
        if not to_add:
            continue

        if existing:
            # Append to existing list: find the last list item and add after it.
            # Find the field line + its last "  - " item.
            lines_list = content.split("\n")
            field_idx = None
            for i, line in enumerate(lines_list):
                if line.startswith(f"{field_name}:"):
                    field_idx = i
                    break
            if field_idx is None:
                continue
            # Find the last "  - " line after field_idx
            last_item_idx = field_idx
            for i in range(field_idx + 1, len(lines_list)):
                if lines_list[i].startswith("  - "):
                    last_item_idx = i
                elif lines_list[i].startswith("  "):
                    continue
                else:
                    break
            old_anchor = "\n".join(lines_list[last_item_idx])
            new_block = old_anchor + "\n" + "\n".join(f'  - "{p}"' for p in to_add)
            md_safe_replace(fp, old_anchor, new_block)
        else:
            # Insert new list after when_to_use (or description, or type).
            anchor_match = re.search(r'^(when_to_use:.*?)(\s*)$', content, re.MULTILINE)
            if not anchor_match:
                anchor_match = re.search(r'^(description:.*?)(\s*)$', content, re.MULTILINE)
            if not anchor_match:
                anchor_match = re.search(r'^(type:.*?)(\s*)$', content, re.MULTILINE)
            if not anchor_match:
                continue
            old_anchor = anchor_match.group(0)
            list_lines = f"{field_name}:"
            for p in to_add:
                list_lines += f'\n  - "{p}"'
            new_block = old_anchor + "\n" + list_lines
            md_safe_replace(fp, old_anchor, new_block)

        changes.append({"field": field_name, "phrases_added": to_add})

    if changes:
        patched.append({"file_path": fp, "changes": changes})

print(f"PATCHED: {len(patched)}")
print(f"PATCHED: {json.dumps(patched)}")
```

[validate: contains "PATCHED:"]

---

### Step 5: Write report

Write a JSON report to the build-log directory and print a human-readable summary.

```python
import json
from pathlib import Path
from datetime import datetime, timezone

# Parse Step 4
lines = output.strip().split("\n")
patched = []
for line in lines:
    if line.startswith("PATCHED: ") and line.replace("PATCHED: ", "", 1).strip().startswith("["):
        patched = json.loads(line.replace("PATCHED: ", "", 1).strip())
        break

# Parse Step 1 for counts
feedback_count = 0
for line in lines:
    if line.startswith("FEEDBACK_COUNT: "):
        feedback_count = int(line.replace("FEEDBACK_COUNT: ", "").strip())
        break

vault_root = Path(vault_path)
output_dir = vault_root / "vaultbot" / "Memory" / "Build-Log"
output_dir.mkdir(parents=True, exist_ok=True)
out_file = output_dir / "trigger-inhibitor-update.json"

report = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "feedback_events_found": feedback_count,
    "notes_patched": len(patched),
    "changes": patched,
}

out_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

# Human-readable summary
summary_lines = [
    f"Trigger/Inhibitor Update Complete",
    f"  Feedback events scanned: {feedback_count}",
    f"  Notes patched: {len(patched)}",
]
for p in patched:
    for ch in p["changes"]:
        summary_lines.append(
            f"  {p['file_path']} — {ch['field']}: +{len(ch['phrases_added'])} phrases"
        )
print("\n".join(summary_lines))
```

[validate: contains "Trigger/Inhibitor Update Complete"]

## Related

- [[Dream-Pass]] — the orchestrator that calls this
- [[Dream-When-To-Use-Update]] — sibling retrieval-feedback loop for when_to_use fields
- [[Migrate-Triggers]] — seeds trigger lists from existing when_to_use fields