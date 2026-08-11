---
type: procedure
status: verified
model_cartridge: small
created: 2026-07-31
description: Scan chat history for recurring tool-call patterns and multi-step workflows that happen 3+ times, then draft candidate procedure specs for each. Use to automate what you do manually. Run periodically or when asked to find what to proceduralize next.
when_to_use: when you want to find recurring patterns in your behavior that could be turned into procedures, or when asked 'what should I automate next'
applies_to:
  - self-improvement
  - procedures
  - automation
allowed_tools:
  - vault_search
  - vault_list
summary: "# Discover-Procedures"
tags:
  - procedure
  - procedures
---

# Discover-Procedures

## When to Run This

Run this when you want to find recurring workflows in your chat history that could be turned into procedures. The goal: each time you run this, you find patterns you repeat manually and draft procedures to automate them. Over time, you do less and less manually.

## What It Does

1. Runs `pattern_extractor.py` deterministically to extract tool co-occurrence and recurring topic data from all chat logs
2. Small model classifies which patterns are procedure candidates (repeated 3+ times, same tool sequence, same task type)
3. Small model checks each candidate against existing procedures to avoid duplicates
4. Small model drafts a procedure spec for each new candidate
5. Writes candidates to a proposal note for review

## Steps

### Step 1: Extract patterns from chat history

1. ```python
   # Run the pattern extractor to get tool co-occurrence and recurring topics
   from pattern_extractor import PatternExtractor
   
   extractor = PatternExtractor()
   sessions = extractor.load_sessions()
   
   # Get tool usage patterns
   tool_patterns = extractor.extract_tool_patterns(sessions)
   
   # Get recurring topics (wikilinks appearing in 3+ sessions)
   recurring_topics = extractor.extract_recurring_topics(sessions)
   
   # Print the raw data for the LLM step to analyze
   import json
   print("=== TOOL FREQUENCY ===")
   print(json.dumps(tool_patterns.get('tool_frequency', {}), indent=2))
   print("\n=== TOP WORKFLOWS (tool co-occurrence) ===")
   print(json.dumps(tool_patterns.get('top_workflows', []), indent=2))
   print("\n=== RECURRING TOPICS (3+ sessions) ===")
   print(json.dumps(recurring_topics[:20], indent=2))
   print(f"\nTotal sessions scanned: {len(sessions)}")
   ```

### Step 2: List existing procedures to avoid duplicates

2. ```python
   # List all existing procedures so we can check for duplicates
   from custom_tools.vault_list import run as _list
   result = _list({"directory": "vaultbot_stuff/System/Procedures"})
   print("=== EXISTING PROCEDURES ===")
   if isinstance(result, dict) and "files" in result:
       for f in result["files"]:
           print(f"  - {f}")
   else:
       print(result)
   ```

### Step 3: Classify patterns as procedure candidates

3. [llm: You are a pattern classifier. Look at the tool co-occurrence data and recurring topics from Step 1, and the existing procedures from Step 2. 

For each tool co-occurrence pattern that appears 3+ times:
- Is there already a procedure that covers this workflow? (Check the existing procedures list)
- If NOT already covered: this is a CANDIDATE
- Classify the candidate by task type: research, vault-maintenance, self-improvement, code-editing, note-writing, gap-filling, quality-check, other

Output a JSON array of candidates, each with:
{
  "pattern": "tool1 + tool2 + tool3",
  "occurrence_count": N,
  "task_type": "classification",
  "proposed_name": "Procedure-Name",
  "what_it_automates": "one sentence description",
  "already_exists": false
}

Only include candidates where already_exists is false. Sort by occurrence_count descending.]

### Step 4: Draft procedure specs for each candidate

4. [llm: For each candidate from Step 3, draft a procedure spec. Use this template:

---
type: procedure
status: draft
model_cartridge: small|big
created: {today}
description: "{what_it_automates}"
when_to_use: "{when this pattern occurs}"
applies_to: [{task_type}]
allowed_tools: [{tools from the pattern}]
---

# {Procedure-Name}

## When to Run This
{when to use}

## Steps
{numbered steps based on the tool sequence observed in the pattern}

For each candidate, output the full markdown of the procedure spec, separated by ---.

Choose model_cartridge: small for classification, extraction, routing, formatting tasks.
Choose model_cartridge: big only for novel reasoning, complex synthesis, or creative work.]

### Step 5: Write candidates to a proposal note

5. ```python
   # Write the candidate procedures to a proposal note for review
   from custom_tools.vault_safe_write import run as _write
   from datetime import datetime, timezone
   
   today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
   timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
   
   # The LLM step above should have produced the candidate specs
   # We write them to a proposal note
   content = f"""---
type: procedure-proposal
status: draft
created: {today}
generated_by: Discover-Procedures
---

# Procedure Candidates — {today}

Auto-generated by running [[Discover-Procedures]]. Each candidate is a recurring pattern found in chat history that has no existing procedure. Review each and promote to `System/Procedures/` if approved.

## Candidates

{candidates_markdown}

## Stats
- Sessions scanned: {len(sessions)}
- Patterns found: {len(candidates)}
- Already covered by existing procedures: {len(duplicates)}
"""
   
   result = _write({
       "file_path": f"vaultbot_stuff/System/Procedure-Candidates-{timestamp}.md",
       "content": content
   })
   print(result)
   ```

### Step 6: Report to operator

6. [llm: Summarize the findings for the operator. Report:
- How many sessions were scanned
- How many recurring patterns were found
- How many are already covered by existing procedures
- How many new candidates were drafted
- List the top 3 candidates by frequency with their proposed names and what they automate
- Note where the full proposal was written]

## Notes

- This procedure gets MORE useful over time — as more chat history accumulates, more patterns emerge
- Run periodically (e.g., after every 10-20 chat sessions) to catch new patterns
- Each approved candidate becomes a new procedure that eliminates manual work
- The small model is sufficient for all steps: classification (is this a pattern?), extraction (what tools?), and formatting (draft the spec)
- This is the self-improvement loop: the system observes its own behavior and proposes its own automation

## Related
- [[Procedural-Bootstrap-and-Evolution-Plan]] — the overall strategy
- [[Cloud-Model-Obsolescence-Architecture]] — where this fits in the obsolescence roadmap
- [[Cross-Session-Patterns-from-75-Chat-Logs]] — previous manual pattern analysis
- [[Semantic-Consolidation-Architecture]] — how patterns feed into knowledge