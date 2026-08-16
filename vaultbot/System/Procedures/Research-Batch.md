---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-08-03
description: Batch-triage multiple research topics at once. Given a list of topics or a note with multiple gaps, the small model classifies each topic by type (factual, conceptual, procedural, controversial) and priority, then fills a research plan template for each. The big model only does the actual research synthesis — all triage and planning is small-cartridge.
when_to_use: when multiple topics need research, when the vault has 3+ dangling wikilinks that all need research, when Sean gives a list of things to look up, or when a Research-Roadmap phase has multiple remaining topics
falsifiable_if: it misclassifies a topic type (e.g. marks a controversial topic as factual) or produces a plan template that doesn't match the topic's research needs
applies_to:
  - research
  - batch-processing
  - triage
  - token-efficiency
allowed_tools:
  - run_procedure
  - vault_list
  - llm_generate
summary: SUMMARY
tags:
  - procedure
  - procedures
---

# Research-Batch

## When to Run This

Run when there are multiple topics to research. The small model triages and plans; the big model only synthesizes the actual research notes.

## Steps

### Step 1: Gather the topic list

1. ```python
import json, re

topics = args.get("topics", [])

if not topics:
    # Fall back to dangling wikilinks from Pattern-Scan
    run_procedure("Find-Broken-Links")
    out_file = str(Path(vault_path) / "vaultbot_stuff" / "Memory" / "Build-Log" / "pattern_scan_latest.json")
    try:
        with open(out_file, "r", encoding="utf-8") as f:
            scan = json.load(f)
        dangling = set()
        for note_path, note_data in scan.get("notes", {}).items():
            for link in note_data.get("unresolved_out", []):
                dangling.add(link)
        topics = sorted(dangling)
    except Exception as e:
        topics = []
        print(f"Could not load Pattern-Scan: {e}")

roadmap_path = args.get("roadmap_note")
if roadmap_path and not topics:
    content = ""
    try:
        with open(Path(vault_path) / roadmap_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        pass
    topics = re.findall(r'- \[ \] (.+)', content)

result = json.dumps({"topic_count": len(topics), "topics": topics[:20]}, indent=2)
```

### Step 2: Classify each topic and fill plan template (small model)

2. [llm: You are a research triage classifier. For each topic below, classify it
   into one of these types and assign a priority. Output JSON only.

   Topic types:
   - "factual" — has a known answer, needs 2-3 sources to verify
   - "conceptual" — needs explanation/synthesis, requires 4-6 sources
   - "procedural" — how-to/process, needs step-by-step sources
   - "controversial" — multiple viewpoints, needs 6+ sources representing all sides

   Priority levels:
   - "high" — blocks other work, frequently referenced, or Sean asked for it
   - "medium" — fills a gap but isn't blocking
   - "low" — nice to have, rarely referenced

   For each topic, produce a research plan with:
   - topic: the topic name
   - type: one of the four types above
   - priority: high/medium/low
   - search_queries: 2-3 web search queries to start with
   - expected_sources: minimum number of sources needed
   - note_title: suggested wikilink title for the resulting note

   Output a JSON array of plan objects. Topics:
   {topics}]

### Step 3: Output the batch plan and route to research

3. ```python
import json

# Parse the LLM output from step 2
plans_raw = prior_results[1] if len(prior_results) > 1 else "[]"
if isinstance(plans_raw, str):
    try:
        start = plans_raw.find("[")
        end = plans_raw.rfind("]")
        plans = json.loads(plans_raw[start:end+1]) if start != -1 else []
    except Exception:
        plans = []
else:
    plans = plans_raw

plan_file = str(Path(vault_path) / "vaultbot_stuff" / "Memory" / "Build-Log" / "research_batch_plan.json")
with open(plan_file, "w", encoding="utf-8") as f:
    json.dump(plans, f, indent=2)

high = [p for p in plans if p.get("priority") == "high"]
medium = [p for p in plans if p.get("priority") == "medium"]
low = [p for p in plans if p.get("priority") == "low"]

summary = {
    "total_plans": len(plans),
    "high_priority": len(high),
    "medium_priority": len(medium),
    "low_priority": len(low),
    "plan_file": plan_file,
    "high_topics": [p["topic"] for p in high],
}
result = json.dumps(summary, indent=2)
```

## Notes

- The small model does ALL the triage work. The big model only reads the final plan and does the actual web research + note synthesis.
- This saves ~2000-4000 tokens of big-model reasoning per batch.

## Related

- [[Procedure-Expansion-Proposal]] — Research-Batch was proposed as Tier 2
- [[Tiny-LLM-Use-Cases-Mapping-to-VaultBot-Procedure-Cartridge]]
- [[Research-Roadmap]] — the primary source of batch research topics
- [[Gap-Fill]] — identifies which dangling links need research
