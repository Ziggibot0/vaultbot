---
type: procedure
status: experimental
baseline: true
created: 2026-08-10
summary: "Lens procedure for evaluating competing options by mapping trade-offs. v4: redesigned for qwen3.5:4b — killed triple-try, merged scoring+reasoning, merged tradeoff+pareto+recommendation. ~3 LLM calls (down from ~21). Already optimal — no v4.1 changes needed."
description: "Lens for design/architecture problems — maps options against criteria, weights trade-offs, identifies Pareto-optimal choices."
when_to_use: "When choosing between competing options with different trade-offs. When evaluating design alternatives. When comparing approaches. When asked 'should I use X or Y?' or 'which option is better?' or 'compare these alternatives'. When making architecture decisions."
tags: [procedure, thinking, lens, trade-off, decision-theory, v4, qwen3.5-4b, optimal]
allowed_tools:
  - vault_search
  - llm_generate
  - run_procedure
depends_on:
  - "[[Think]]"
  - "[[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]]"
research_sources:
  - "[[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]]"
  - "[[psychology-of-analytical-thinking-methods-when-to-use-root-cause-analysis-vs-fir]]"
---

# Trade-off Analysis Lens (v4)

> **Source:** Dual-process theory (Kahneman & Tversky) shows that humans construct preferences on the fly rather than retrieving them from memory, making structured trade-off analysis essential for avoiding preference errors [[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]]. Decision theory research shows that multi-attribute utility analysis outperforms intuitive judgment for complex choices with 3+ competing criteria.

## When This Lens Is Called

Called by [[Think]] when the problem type is **design/architecture** — choosing between competing options where each has advantages and disadvantages.

## What Changed in v4

| Problem in v3 | Fix in v4 |
|---|---|
| Triple-try on every step (3x LLM calls per step = 21 total) | Single calls — 4B is consistent enough without majority vote |
| Scoring and reasoning were separate steps (2 calls) | Merged into one call — 4B can score AND explain in one pass |
| Dominant trade-off, Pareto check, and recommendation were 3 steps | Merged into one step — Pareto is deterministic, trade-off + recommendation is one LLM call |
| 7 steps, ~21 LLM calls | 3 steps, ~3 LLM calls |

## v4.1 Audit Result: **Already Optimal**

This lens was audited for further simplification opportunities. **No changes needed** — it's already at its optimal call count:

- **Step 1**: Extract options and criteria (1 LLM call, JSON output)
- **Step 2**: Score each option against each criterion WITH reasons (1 LLM call, JSON output)
- **Step 3**: Identify dominant trade-off and recommend (1 LLM call) + Pareto check (deterministic, zero LLM cost)
- **Total: 3 LLM calls**

All three steps are necessary and cannot be further batched without losing the structured reasoning that makes this lens effective. The Pareto check being deterministic is a key architectural win.

## The Knowledge Triad Applied

- **Ontology:** What are the options? What are the criteria?
- **Epistemology:** How does each option score against each criterion? What evidence supports each score?
- **Hermeneutics:** What does the pattern of scores mean? Which trade-offs are acceptable?

---

### Step 1: Extract options and criteria

Single call: the 4B can extract both options and evaluation criteria from the problem in one pass. JSON output for structured parsing.

```python
import json

problem = args.get("problem", "")

prompt = f"""Analyze this problem and extract:
1. All distinct options being compared
2. All evaluation criteria (dimensions the options differ on)

Output ONLY a JSON object with two keys:
{{"options": ["option A", "option B"], "criteria": ["cost", "speed", "reliability"]}}

Problem: {problem}"""

try:
    raw = llm_generate(prompt).strip()
    # Try to extract JSON from the response
    # Find first { and last }
    start = raw.find('{')
    end = raw.rfind('}')
    if start >= 0 and end > start:
        json_str = raw[start:end+1]
        parsed = json.loads(json_str)
        options = parsed.get('options', [])
        criteria = parsed.get('criteria', [])
    else:
        raise ValueError("no JSON found")
    
    if not isinstance(options, list) or len(options) < 2:
        raise ValueError("not enough options")
    if not isinstance(criteria, list) or len(criteria) == 0:
        raise ValueError("no criteria")
except Exception:
    # Deterministic fallback: structural split
    options = []
    for sep in [' vs ', ' vs. ', ' versus ', ' or ']:
        if sep in problem.lower():
            idx = problem.lower().index(sep)
            left = problem[:idx].strip()
            right = problem[idx + len(sep):].strip()
            if left and right:
                options = [left[:80], right[:80]]
                break
    if len(options) < 2:
        for sep in [',', ';']:
            if sep in problem:
                parts = problem.split(sep)
                options = [p.strip()[:50] for p in parts[:4] if len(p.strip()) > 3]
                break
    if len(options) < 2:
        options = ["Option A", "Option B"]
    criteria = ["cost", "complexity", "reliability", "maintainability"]

result = f"OPTIONS: {json.dumps(options)}\nCRITERIA: {json.dumps(criteria)}\nPROBLEM: {problem}"
print(result)
```

[validate: contains "OPTIONS:"]
[validate: contains "CRITERIA:"]

---

### Step 2: Score each option against each criterion WITH reasons

Single call: the 4B can assign scores AND explain why in one pass. JSON output with score + reason per pair.

```python
import json

# Parse Step 1
lines = output.strip().split('\n')
options_str = ''
criteria_str = ''
problem = ''
for line in lines:
    if line.startswith('OPTIONS: '):
        options_str = line.replace('OPTIONS: ', '').strip()
    elif line.startswith('CRITERIA: '):
        criteria_str = line.replace('CRITERIA: ', '').strip()
    elif line.startswith('PROBLEM: '):
        problem = line.replace('PROBLEM: ', '').strip()

try:
    options = json.loads(options_str)
    criteria = json.loads(criteria_str)
except:
    options = ["Option A", "Option B"]
    criteria = ["cost", "complexity", "reliability", "maintainability"]

prompt = f"""Score each option against each criterion on a 1-5 scale (5=best) and give ONE sentence explaining why.

Output ONLY a JSON array of objects with keys "option", "criterion", "score", "reason".
Example: [{{"option":"A","criterion":"cost","score":3,"reason":"Moderate cost because uses standard components"}}]

Options: {json.dumps(options)}
Criteria: {json.dumps(criteria)}
Problem: {problem}"""

try:
    raw = llm_generate(prompt).strip()
    start = raw.find('[')
    end = raw.rfind(']')
    if start >= 0 and end > start:
        scores = json.loads(raw[start:end+1])
    else:
        raise ValueError("no JSON array found")
    
    if not isinstance(scores, list) or len(scores) < len(options):
        raise ValueError("incomplete scores")
except Exception:
    # Deterministic fallback: all 3s with generic reasons
    scores = []
    for opt in options:
        for crit in criteria:
            scores.append({"option": opt, "criterion": crit, "score": 3, "reason": "Neutral default — insufficient information for assessment"})

result = f"SCORES: {json.dumps(scores)}\nCRITERIA: {json.dumps(criteria)}\nOPTIONS: {json.dumps(options)}\nPROBLEM: {problem}"
print(result)
```

[validate: contains "SCORES:"]

---

### Step 3: Identify dominant trade-off and recommend

Single call: the 4B can identify the key tension AND make a recommendation in one pass. The Pareto check is deterministic (pure math, no LLM).

```python
import json

# Parse Step 2
lines = output.strip().split('\n')
scores_str = ''
criteria_str = ''
options_str = ''
problem = ''
for line in lines:
    if line.startswith('SCORES: '):
        scores_str = line.replace('SCORES: ', '').strip()
    elif line.startswith('CRITERIA: '):
        criteria_str = line.replace('CRITERIA: ', '').strip()
    elif line.startswith('OPTIONS: '):
        options_str = line.replace('OPTIONS: ', '').strip()
    elif line.startswith('PROBLEM: '):
        problem = line.replace('PROBLEM: ', '').strip()

try:
    scores = json.loads(scores_str)
    criteria = json.loads(criteria_str)
    options = json.loads(options_str)
except:
    scores = []
    criteria = []
    options = []

# --- DETERMINISTIC: Pareto-optimal check (zero LLM cost) ---
option_scores = {}
for entry in scores:
    opt = entry.get("option", "")
    crit = entry.get("criterion", "")
    score = entry.get("score", 3)
    if opt not in option_scores:
        option_scores[opt] = {}
    option_scores[opt][crit] = score

pareto = []
for opt in options:
    if opt not in option_scores:
        continue
    crit_scores = option_scores[opt]
    dominated = False
    for other_opt in options:
        if other_opt == opt or other_opt not in option_scores:
            continue
        other_scores = option_scores[other_opt]
        all_ge = all(other_scores.get(c, 0) >= crit_scores.get(c, 0) for c in criteria)
        any_gt = any(other_scores.get(c, 0) > crit_scores.get(c, 0) for c in criteria)
        if all_ge and any_gt:
            dominated = True
            break
    if not dominated:
        pareto.append(opt)

if not pareto:
    pareto = options  # fallback

# --- LLM: dominant trade-off + recommendation in one call ---
prompt = f"""Based on these scores, write a 2-3 sentence recommendation that:
1. Names the dominant trade-off (which two criteria are in most tension)
2. Names what is being sacrificed and what is being gained
3. Recommends a specific option from the Pareto-optimal set

Scores: {json.dumps(scores)}
Pareto-optimal options: {json.dumps(pareto)}
Problem: {problem}

Format:
DOMINANT_TRADEOFF: [one sentence]
RECOMMENDATION: [2-3 sentences with explicit trade-off acknowledgment]"""

try:
    resp = llm_generate(prompt).strip()
    if "RECOMMENDATION:" not in resp or len(resp) < 30:
        raise ValueError("invalid response")
except Exception:
    # Deterministic fallback
    if len(pareto) == 1:
        resp = f"DOMINANT_TRADEOFF: The primary tension is between competing criteria.\nRECOMMENDATION: Recommend {pareto[0]}. This is the only Pareto-optimal option."
    else:
        resp = f"DOMINANT_TRADEOFF: Multiple criteria are in tension.\nRECOMMENDATION: Pareto-optimal options: {', '.join(pareto)}. Choose based on which criterion matters most for this context."

# Parse recommendation
dominant = ""
recommendation = ""
for line in resp.split('\n'):
    if line.startswith("DOMINANT_TRADEOFF:"):
        dominant = line.replace("DOMINANT_TRADEOFF:", "").strip()
    elif line.startswith("RECOMMENDATION:"):
        recommendation = line.replace("RECOMMENDATION:", "").strip()

if not dominant:
    dominant = "Unable to identify dominant trade-off"
if not recommendation:
    recommendation = resp[:300] if resp else "Unable to generate recommendation"

result = f"""TRADE_OFF_ANALYSIS_RESULT:
  OPTIONS: {json.dumps(options)}
  CRITERIA: {json.dumps(criteria)}
  SCORES: {json.dumps(scores)}
  PARETO_OPTIMAL: {json.dumps(pareto)}
  DOMINANT_TRADEOFF: {dominant}
  RECOMMENDATION: {recommendation}
  METHOD: Multi-attribute utility analysis + Pareto efficiency (deterministic)
  TRIAD_LAYER: Ontology (options/criteria) -> Epistemology (scoring) -> Hermeneutics (recommendation)
"""
print(result)
```

[validate: contains "TRADE_OFF_ANALYSIS_RESULT:"]
[validate: contains "RECOMMENDATION:"]

---

## Research Justification

1. **Dual-process theory** ([[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]]): Humans construct preferences on the fly. Structured scoring forces System 2 engagement, preventing preference errors.

2. **Decision theory** ([[psychology-of-analytical-thinking-methods-when-to-use-root-cause-analysis-vs-fir]]): Multi-attribute utility analysis outperforms intuitive judgment for choices with 3+ competing criteria. The 1-5 scoring grid IS a simplified MAUA.

3. **Pareto efficiency** (deterministic step): Identifying non-dominated options is purely mathematical — no judgment needed. This reduces the LLM's job from "pick the best option" to "explain the trade-off," which is much easier for a small model.

4. **Knowledge Triad** ([[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]]): Ontology = define options and criteria. Epistemology = score each option. Hermeneutics = interpret the pattern.

5. **Deterministic Scaffolding** ([[Deterministic-Scaffolding-for-Small-Models]]): Every LLM call has a deterministic fallback. The Pareto check is fully deterministic.

## Related

- [[Think]] — parent procedure that dispatches to this lens
- [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]] — philosophical foundation
- [[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]] — research basis
- [[Deterministic-Scaffolding-for-Small-Models]] — sandwich pattern applied here