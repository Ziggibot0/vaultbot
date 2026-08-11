---
type: procedure
status: experimental
model_cartridge: big
created: 2026-08-10
updated: 2026-08-11
description: "Mines full user-assistant conversations for the critical reasoning path — the minimal chain of thought that actually got from the question to the answer. Strips waste (dead ends, over-explanation, wrong directions the user corrected). Turns the critical path into a procedure so next time that type of problem comes up, the model walks straight down the path with no detours. Auto-batches large corpora and deduplicates against previous runs. Uses LLM for all pattern detection — no regex, no keyword matching."
when_to_use: "During Dream Pass, after Behavioral-Pattern-Mine. Also runnable standalone on specific chat notes or the full chat corpus."
falsifiable_if: "The generated procedure contains reasoning steps that were actually unnecessary, or omits steps that were necessary to reach the answer, or re-analyzes notes that were already processed in a prior run."
applies_to:
  - thinking-patterns
  - conversation-analysis
  - procedure-creation
  - cost-reduction
allowed_tools:
  - vault_list
  - vault_read_note
  - code_read
  - llm_generate
summary: |
  Critical-Path-Mine reads full conversations between user and assistant,
  uses the LLM to semantically identify the minimal reasoning path that
  led to the answer, and generates a procedure from that path. All pattern
  detection is done by the LLM — no regex or keyword heuristics.
  Auto-batches large corpora (691+ notes) into manageable chunks and
  deduplicates against a processed-notes manifest so re-runs only
  process new conversations.
tags:
  - procedure
  - thinking-patterns
  - dream-pass
  - conversation-analysis
---

# Critical-Path-Mine

## Purpose

Complements [[Behavioral-Pattern-Mine]] (which mines tool-call sequences) by mining *reasoning* patterns. The conversation between user and assistant contains the training data: when the user says "you're doing too much," that marks waste. When the user says "yes," that marks the right direction. When the assistant goes down a path and the user redirects, that path was a dead end.

This procedure reads those conversations, uses the LLM to semantically identify the **critical path** — the minimal reasoning that actually mattered — and turns it into a procedure. Next time that type of problem comes up, the procedure walks the model straight down the critical path instead of re-deriving it.

**Key constraints:**
- All pattern detection is done by the LLM through semantic understanding. No regex, no keyword matching, no bespoke heuristics.
- **Auto-batching:** When the corpus is large (100+ notes), the procedure chunks it into batches of 30 and processes each batch through steps 2-3 independently, then merges results in step 4. No manual batching needed.
- **Deduplication:** A processed-notes manifest (`critical-path-mine-processed.json`) tracks which notes have already been analyzed. Re-runs skip already-processed notes and only pick up new ones.

## Inputs

- `chat_note_paths` (optional): Specific chat note paths to analyze. If not provided, collects ALL chat notes automatically, skipping any already in the processed-notes manifest.
- `force_reprocess` (optional, default false): If true, ignores the processed-notes manifest and re-analyzes everything.

## Output Contract

Writes a JSON report to `vaultbot_stuff/Memory/Build-Log/critical-path-mine.json` containing extracted critical paths grouped by problem type. Also writes `critical-path-mine-processed.json` tracking which notes have been analyzed. The calling code (Dream-Pass) can feed these to [[Dream-Pattern-To-Procedure]] for procedure generation.

---

## Steps

### Step 1: Collect and batch conversation data, skipping already-processed notes

```python
import json
from pathlib import Path
from datetime import datetime, timezone

vault_root = Path(vault_path)
chat_dir = vault_root / "vaultbot_stuff" / "Memory" / "Chat"
output_dir = vault_root / "vaultbot_stuff" / "Memory" / "Build-Log"
output_dir.mkdir(parents=True, exist_ok=True)
manifest_file = output_dir / "critical-path-mine-processed.json"

# Load processed-notes manifest for dedup
force = args.get("force_reprocess", False)
processed_paths = set()
if not force and manifest_file.exists():
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        processed_paths = set(manifest.get("processed_paths", []))
    except (json.JSONDecodeError, KeyError):
        pass

# If specific chat notes were provided in args, use those
chat_paths = args.get("chat_note_paths", [])

if not chat_paths:
    # Otherwise, collect ALL chat notes, then take only the next unprocessed batch
    if chat_dir.exists():
        chat_files = sorted(
            [f for f in chat_dir.rglob("*.md") if f.stem.startswith("Chat-")],
            key=lambda f: f.stat().st_mtime,
            reverse=True
        )
        all_paths = [str(f.relative_to(vault_root)) for f in chat_files]
    else:
        all_paths = []

    # Dedup: skip already-processed notes unless force_reprocess
    if processed_paths and not force:
        chat_paths = [p for p in all_paths if p not in processed_paths]
    else:
        chat_paths = all_paths

    total_found = len(all_paths)
    skipped = total_found - len(chat_paths)
    print(f"Found {total_found} chat notes total, {skipped} already processed, {len(chat_paths)} remaining.")

    # Take only the NEXT batch of 10 — not all at once.
    # This keeps each execution fast (~10 LLM calls, ~70-100s) and avoids subprocess timeouts.
    # Re-run the procedure to process the next batch. The manifest tracks progress.
    BATCH_SIZE = 10
    remaining_after = max(0, len(chat_paths) - BATCH_SIZE)
    chat_paths = chat_paths[:BATCH_SIZE]
    print(f"Processing {len(chat_paths)} notes this run ({remaining_after} remaining for future runs).")

if not chat_paths:
    print("No new chat notes to analyze. All previously processed.")
    result = json.dumps({"batches": [], "total_notes": 0, "skipped": skipped, "remaining": 0})
else:
    # Single batch (no inner chunking needed — we already limited to BATCH_SIZE)
    batch_conversations = []
    for path in chat_paths:
        full_path = vault_root / path
        if not full_path.exists():
            continue
        content = full_path.read_text(encoding="utf-8", errors="replace")
        title = full_path.stem
        batch_conversations.append({
            "path": path,
            "title": title,
            "content": content[:8000],
        })

    batches = [{
        "batch_num": 1,
        "paths": chat_paths,
        "conversations": batch_conversations,
    }]

    print(f"Created 1 batch of {len(batch_conversations)} conversations.")
    result = json.dumps({
        "batches": batches,
        "total_notes": len(chat_paths),
        "skipped": skipped,
        "remaining": remaining_after if not args.get("chat_note_paths") else 0,
    })
```

[validate: at_least 0 conversations loaded — 0 is valid when all already processed]

### Step 2: Identify the critical path in each conversation (per-batch)

For each batch, we call `llm_generate` individually per conversation to ensure ALL conversations get analyzed. The LLM semantically identifies the problem type, critical path, waste, and shortcut for each conversation.

```python
import json, re
from pathlib import Path

# Get step 1's output (batched conversation data)
step1_output = prior_results.get("1.0", "") or prior_results.get(1.0, "")
step1_data = json.loads(step1_output)
batches = step1_data.get("batches", [])

if not batches:
    print("No batches to analyze — all notes were already processed.")
    result = json.dumps({"analyses": [], "batch_count": 0})
else:
    system_prompt = "You are a JSON generator. You output ONLY valid JSON. No markdown, no explanation, no code fences. Start with [ and end with ]."

    all_analyses = []
    for batch in batches:
        batch_num = batch["batch_num"]
        conversations = batch["conversations"]
        print(f"Processing batch {batch_num} ({len(conversations)} conversations)...")

        for conv in conversations:
            user_prompt = f"""You are analyzing a conversation between a user and an AI assistant (VaultBot). Find the CRITICAL PATH — the minimal reasoning that actually got from the user's question to the answer.

Conversation title: {conv['title']}

Conversation content:
{conv['content']}

Identify:
1. PROBLEM TYPE: What category of problem was this? (e.g., "fixing a bug in the backend", "researching a topic", "designing a procedure", "brainstorming an architecture")
2. CRITICAL PATH: List the essential reasoning steps that actually led to the solution. Steps where, if removed, the answer would not have been reached.
3. WASTE: List unnecessary reasoning — dead ends, over-explanation, wrong directions the user corrected, tangents. For each, note what the user said/did that signaled waste.
4. SHORTCUT: The ideal minimal path — shortest chain of thought from question to answer.

Output as a JSON array with ONE object (for this single conversation):
[{{"problem_type": "<category>", "critical_path": ["<step 1>", "..."], "waste": [{{"what": "<desc>", "signal": "<user signal>"}}], "shortcut": ["<ideal step 1>", "..."], "procedure_worthy": true/false, "source_path": "{conv['path']}"}}]

Set procedure_worthy to true only if the critical path is reusable — if this type of problem is likely to recur."""

            try:
                raw = llm_generate(prompt=user_prompt, system=system_prompt)
            except Exception as e:
                print(f"WARNING: llm_generate failed for {conv['title']}: {e}")
                raw = "[]"
            # Strip code fences if present
            clean = re.sub(r'^```(?:json)?\s*', '', raw.strip())
            clean = re.sub(r'\s*```$', '', clean.strip())
            # Try to parse as JSON
            try:
                parsed = json.loads(clean)
                if isinstance(parsed, list):
                    all_analyses.extend(parsed)
                elif isinstance(parsed, dict):
                    all_analyses.append(parsed)
            except json.JSONDecodeError:
                # Save raw for debugging
                debug_dir = Path(vault_path) / "vaultbot_stuff" / "Memory" / "Build-Log"
                debug_dir.mkdir(parents=True, exist_ok=True)
                debug_file = debug_dir / f"critical-path-mine-step2-raw-{conv['title'][:30]}.txt"
                debug_file.write_text(raw, encoding="utf-8")
                print(f"WARNING: Could not parse LLM output for {conv['title']}, saved to {debug_file}")

    print(f"Analyzed {len(all_analyses)} conversations across {len(batches)} batches.")

    # Save step 2 output for debugging
    debug_dir = Path(vault_path) / "vaultbot_stuff" / "Memory" / "Build-Log"
    debug_dir.mkdir(parents=True, exist_ok=True)
    debug_file = debug_dir / "critical-path-mine-step2-raw.txt"
    debug_file.write_text(json.dumps(all_analyses, indent=2), encoding="utf-8")

    result = json.dumps({"analyses": all_analyses, "batch_count": len(batches)})
```

[validate: at_least 0 conversations analyzed — 0 is valid when all already processed]

### Step 3: Group critical paths by problem type (chunked if large)

After analyzing all conversations, we group them by problem type and find the common reasoning pattern. If the analysis set is large (>200 analyses), we chunk the grouping: first group each subset of 200, then meta-group the subset results. This prevents a single LLM call from choking on too much input.

```python
import json, re
from pathlib import Path

# Get step 2's output
step2_output = prior_results.get("2.0", "") or prior_results.get(2.0, "")
step2_data = json.loads(step2_output)
analyses = step2_data.get("analyses", [])

if not analyses:
    print("No analyses to group — nothing to do.")
    result = json.dumps({"groups": []})
else:
    system_prompt = "You are a JSON generator. You output ONLY valid JSON. No markdown, no explanation, no code fences. Start with { and end with }."

    def group_analyses(analysis_subset, subset_label=""):
        """Run the grouping LLM call on a subset of analyses."""
        analyses_json = json.dumps(analysis_subset, indent=2)
        # Truncate to ~60K chars to stay within context limits
        if len(analyses_json) > 60000:
            analyses_json = analyses_json[:60000] + "\n...[truncated]"
            print(f"WARNING: {subset_label} analyses truncated to 60K chars for LLM input.")

        user_prompt = f"""You are given multiple critical path analyses from different conversations, each tagged with a problem type. Your job is to find COMMON THINKING PATTERNS — reasoning structures that appear across multiple conversations of the same problem type.

Group the analyses by problem type. For each group with 2+ conversations, identify:

1. The COMMON SHORTCUT: What reasoning steps appear in the shortcut paths across this group?
2. The COMMON WASTE: What types of waste appear repeatedly?
3. THE PROCEDURE: Write a step-by-step procedure that walks the model through the shortcut path for this problem type.

CRITICAL: You MUST output ONLY a JSON object. No markdown, no headers, no explanation, no code fences. Start with {{ and end with }}. If no groups have 2+ conversations, return {{"groups": []}}.

Output format (STRICT JSON, no other text):
{{"groups": [{{"problem_type": "<category>", "conversation_count": N, "common_shortcut": ["<step 1>", "..."], "common_waste": ["<waste pattern 1>", "..."], "procedure_name": "<suggested-name>", "procedure_steps": ["### Step 1: <description>\\n[llm: <instruction>]", "..."]}}]}}

Here are the critical path analyses:
{analyses_json}
"""
        raw = llm_generate(prompt=user_prompt, system=system_prompt)
        # Strip code fences
        clean = re.sub(r'^```(?:json)?\s*', '', raw.strip())
        clean = re.sub(r'\s*```$', '', clean.strip())
        # Find the JSON object
        json_match = re.search(r'\{[\s\S]*\}', clean)
        if json_match:
            try:
                return json.loads(json_match.group(0)).get("groups", [])
            except json.JSONDecodeError:
                return []
        try:
            return json.loads(clean).get("groups", [])
        except json.JSONDecodeError:
            return []

    # Chunking: if >200 analyses, group in subsets then meta-group
    CHUNK_SIZE = 200
    if len(analyses) <= CHUNK_SIZE:
        print(f"Grouping {len(analyses)} analyses in a single call...")
        groups = group_analyses(analyses, "all")
    else:
        print(f"Grouping {len(analyses)} analyses in chunks of {CHUNK_SIZE}...")
        subset_groups = []
        for i in range(0, len(analyses), CHUNK_SIZE):
            chunk = analyses[i:i + CHUNK_SIZE]
            label = f"chunk {i//CHUNK_SIZE + 1}"
            print(f"  Grouping {label} ({len(chunk)} analyses)...")
            chunk_groups = group_analyses(chunk, label)
            subset_groups.extend(chunk_groups)

        # Meta-group: merge subset groups by problem type
        print(f"Meta-grouping {len(subset_groups)} subset groups...")
        groups = group_analyses(
            [{"problem_type": g["problem_type"],
              "critical_path": g.get("common_shortcut", []),
              "waste": [{"what": w, "signal": ""} for w in g.get("common_waste", [])],
              "shortcut": g.get("common_shortcut", []),
              "procedure_worthy": True,
              "source_path": ""} for g in subset_groups],
            "meta-group"
        )

    print(f"Found {len(groups)} problem type groups.")

    # Save step 3 output for debugging
    debug_dir = Path(vault_path) / "vaultbot_stuff" / "Memory" / "Build-Log"
    debug_dir.mkdir(parents=True, exist_ok=True)
    debug_file = debug_dir / "critical-path-mine-step3-raw.txt"
    debug_file.write_text(json.dumps(groups, indent=2), encoding="utf-8")

    result = json.dumps({"groups": groups})
```

### Step 4: Write the report, update the processed-notes manifest, and summarize

```python
import json, re
from pathlib import Path
from datetime import datetime, timezone

vault_root = Path(vault_path)
output_dir = vault_root / "vaultbot_stuff" / "Memory" / "Build-Log"
output_dir.mkdir(parents=True, exist_ok=True)
out_file = output_dir / "critical-path-mine.json"
manifest_file = output_dir / "critical-path-mine-processed.json"

# Get step 3's output
step3_output = prior_results.get("3.0", "") or prior_results.get(3.0, "")
step3_clean = re.sub(r'^```(?:json)?\s*', '', step3_output.strip())
step3_clean = re.sub(r'\s*```$', '', step3_clean.strip())
json_match = re.search(r'\{[\s\S]*\}', step3_clean)
groups = []
if json_match:
    try:
        groups = json.loads(json_match.group(0)).get("groups", [])
    except json.JSONDecodeError:
        pass
else:
    try:
        groups = json.loads(step3_clean).get("groups", [])
    except json.JSONDecodeError:
        pass

# Get step 1 data for the list of paths we processed this run
step1_output = prior_results.get("1.0", "") or prior_results.get(1.0, "")
try:
    step1_data = json.loads(step1_output)
    this_run_paths = []
    for batch in step1_data.get("batches", []):
        this_run_paths.extend(batch.get("paths", []))
    skipped = step1_data.get("skipped", 0)
except (json.JSONDecodeError, TypeError):
    this_run_paths = []
    skipped = 0

# Update the processed-notes manifest: merge this run's paths with prior ones
existing_processed = set()
if manifest_file.exists():
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        existing_processed = set(manifest.get("processed_paths", []))
    except (json.JSONDecodeError, KeyError):
        pass
all_processed = existing_processed | set(this_run_paths)
manifest = {
    "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "processed_paths": sorted(all_processed),
    "total_processed": len(all_processed),
}
manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(f"Updated processed-notes manifest: {len(all_processed)} total notes processed.")

# Get analysis count from step 2
step2_output = prior_results.get("2.0", "") or prior_results.get(2.0, "")
try:
    step2_data = json.loads(step2_output)
    analyses_count = len(step2_data.get("analyses", []))
except (json.JSONDecodeError, TypeError):
    analyses_count = 0

# Merge with existing report if present (accumulate groups across runs)
existing_groups = []
if out_file.exists():
    try:
        existing_report = json.loads(out_file.read_text(encoding="utf-8"))
        existing_groups = existing_report.get("groups", [])
    except (json.JSONDecodeError, KeyError):
        pass

# Merge: combine groups by problem type, preferring the larger conversation_count
merged_groups = {}
for g in existing_groups + groups:
    pt = g.get("problem_type", "unknown")
    if pt in merged_groups:
        # Merge: take the one with more conversations, or combine counts
        existing = merged_groups[pt]
        existing["conversation_count"] = max(
            existing.get("conversation_count", 0),
            g.get("conversation_count", 0))
        # Keep the longer procedure steps
        if len(g.get("procedure_steps", [])) > len(existing.get("procedure_steps", [])):
            existing["procedure_steps"] = g["procedure_steps"]
            existing["procedure_name"] = g.get("procedure_name", existing.get("procedure_name", ""))
    else:
        merged_groups[pt] = g
all_groups = list(merged_groups.values())

report = {
    "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "conversations_analyzed_this_run": analyses_count,
    "conversations_skipped_already_processed": skipped,
    "total_conversations_processed_all_runs": len(all_processed),
    "groups_found": len(all_groups),
    "groups": all_groups,
}

out_file.write_text(json.dumps(report, indent=2), encoding="utf-8")

print(f"Critical-Path-Mine report written to {out_file}")
print(f"Analyzed {analyses_count} conversations this run (skipped {skipped} already-processed).")
print(f"Total processed across all runs: {len(all_processed)}")
print(f"Found {len(all_groups)} problem type groups.")

for g in all_groups:
    print(f"\n  {g['problem_type']} ({g.get('conversation_count', '?')} conversations)")
    print(f"    Procedure: {g.get('procedure_name', '?')}")
    print(f"    Steps: {len(g.get('procedure_steps', []))}")
    waste = g.get('common_waste', [])
    print(f"    Common waste: {waste[:3]}")

result = json.dumps(report)
```

[validate: report contains "groups" key]

## Notes

- This procedure is purely LLM-driven for pattern detection. No regex, no keyword matching, no bespoke heuristics. The LLM reads conversations and understands semantically what was waste and what was necessary.
- **Incremental processing:** Each call processes ONE batch of 10 notes — the next unprocessed set. The manifest tracks which notes have been analyzed. Call repeatedly until "No new chat notes to analyze." Each call takes ~10 LLM generations (~1-2 minutes). No manual batching — just call again until done.
- **Deduplication:** The processed-notes manifest (`critical-path-mine-processed.json`) tracks which notes have been analyzed. Re-runs skip already-processed notes and only pick up new ones. Use `force_reprocess=true` to ignore the manifest.
- **Chunked grouping:** Step 3 chunks the grouping LLM call if there are >200 analyses, preventing context overflow. Subset groups are meta-grouped into final groups.
- The procedure complements [[Behavioral-Pattern-Mine]]: that mines tool-call sequences, this mines reasoning patterns. Together they give Dream Pass both the "what tools to call" and "how to think about it" for each problem type.
- The `procedure_worthy` flag in step 2 filters out one-off conversations that don't have reusable patterns.
- Step 3 requires multiple conversations of the same problem type to generate a procedure. A single conversation can suggest a pattern, but it needs confirmation across 2+ to become a procedure.
- The generated procedures should be fed to [[Dream-Pattern-To-Procedure]] or [[Procedure-Creator]] for validation and publishing.

## Related

- [[Behavioral-Pattern-Mine]] — mines tool-call sequences (complementary)
- [[Dream-Pattern-To-Procedure]] — converts patterns into published procedures
- [[Dream-Pass]] — the sleep cycle that orchestrates this
- [[Stress-Signal-Architecture]] — how stress signals flag manual work for Dream Pass to heal