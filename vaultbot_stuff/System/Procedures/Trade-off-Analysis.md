---
type: procedure
status: experimental
created: 2026-08-10
summary: "Lens procedure for evaluating competing options by mapping trade-offs. Based on decision theory and dual-process theory research on preference construction. Hardened with triple-try consistency, bite-sized LLM calls, and deterministic fallbacks."
description: "Lens for design/architecture problems — maps options against criteria, weights trade-offs, identifies Pareto-optimal choices."
tags: [procedure, thinking, lens, trade-off, decision-theory, dual-process]
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

# Trade-off Analysis Lens

> **Source:** Dual-process theory (Kahneman & Tversky) shows that humans construct preferences on the fly rather than retrieving them from memory, making structured trade-off analysis essential for avoiding preference errors [[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]]. Decision theory research shows that multi-attribute utility analysis outperforms intuitive judgment for complex choices with 3+ competing criteria.

## When This Lens Is Called

Called by [[Think]] when the problem type is **design/architecture** — choosing between competing options where each has advantages and disadvantages.

## The Knowledge Triad Applied

- **Ontology:** What are the options? What are the criteria?
- **Epistemology:** How does each option score against each criterion? What evidence supports each score?
- **Hermeneutics:** What does the pattern of scores mean? Which trade-offs are acceptable?

---

### Step 1: List all distinct options (triple-try)

Extract options from the problem statement. This is a bounded extraction task — the model identifies named options, not open-ended reasoning. Triple-try with majority vote for consistency.

```python
import json

problem = args.get("problem", "")

def try_extract_options():
    raw = llm_generate(
        f"List every distinct option mentioned or implied in this problem. "
        f"Output ONLY a JSON array of option name strings, nothing else. "
        f"Example: [\"option A\", \"option B\"].\n\nProblem: {problem}"
    )
    opts = json.loads(raw.strip())
    if not isinstance(opts, list) or len(opts) < 2:
        raise ValueError("not enough options")
    return opts

# Triple-try with majority vote
results = []
for i in range(3):
    try:
        results.append(try_extract_options())
    except:
        results.append(None)

# Pick the result that appears most often (by JSON string comparison)
valid = [r for r in results if r is not None]
if valid:
    from collections import Counter
    serialized = [json.dumps(sorted(r)) for r in valid]
    most_common = Counter(serialized).most_common(1)[0][0]
    options = json.loads(most_common)
else:
    # DETERMINISTIC FALLBACK: split on common separators
    import re
    vs_match = re.search(r'(\w[\w\s]+?)\s+(?:vs\.?|versus|or)\s+(\w[\w\s]+)', problem, re.IGNORECASE)
    if vs_match:
        options = [vs_match.group(1).strip(), vs_match.group(2).strip()]
    else:
        parts = re.split(r'[,;]', problem)
        options = [p.strip()[:50] for p in parts[:4] if len(p.strip()) > 3]
    if len(options) < 2:
        options = ["Option A", "Option B"]

result = f"OPTIONS: {json.dumps(options)}"
print(result)
```

[validate: contains "OPTIONS:"]

---

### Step 2: Extract evaluation criteria from the problem (triple-try)

Criteria are the dimensions along which options differ. Bounded extraction — the model identifies dimensions, not reasoning. Triple-try for consistency.

```python
import json
from collections import Counter

# Parse options from Step 1
lines = output.strip().split('\n')
options_str = ''
for line in lines:
    if line.startswith('OPTIONS: '):
        options_str = line.replace('OPTIONS: ', '').strip()
try:
    options = json.loads(options_str)
except:
    options = ["Option A", "Option B"]

problem = args.get("problem", "")

def try_extract_criteria():
    raw = llm_generate(
        f"What dimensions or criteria should be used to evaluate the options in this problem? "
        f"Output ONLY a JSON array of criterion name strings (max 7). "
        f'Example: ["cost", "speed", "reliability"].\n\nProblem: {problem}'
    )
    crits = json.loads(raw.strip())
    if not isinstance(crits, list) or len(crits) == 0:
        raise ValueError("no criteria")
    return crits

# Triple-try with majority vote
results = []
for i in range(3):
    try:
        results.append(try_extract_criteria())
    except:
        results.append(None)

valid = [r for r in results if r is not None]
if valid:
    serialized = [json.dumps(sorted(r)) for r in valid]
    most_common = Counter(serialized).most_common(1)[0][0]
    criteria = json.loads(most_common)
else:
    criteria = ["cost", "complexity", "reliability", "maintainability"]

result = f"CRITERIA: {json.dumps(criteria)}\nOPTIONS: {json.dumps(options)}\nPROBLEM: {problem}"
print(result)
```

[validate: contains "CRITERIA:"]

---

### Step 3: Score each option against each criterion — NUMBERS ONLY (triple-try)

The model assigns just a number (1-5) per option-criterion pair. No reasons yet — that's the next step. This keeps each call bite-sized. Triple-try for consistency on each batch.

```python
import json
from collections import Counter

# Parse from Step 2
lines = output.strip().split('\n')
criteria_str = ''
options_str = ''
problem = ''
for line in lines:
    if line.startswith('CRITERIA: '):
        criteria_str = line.replace('CRITERIA: ', '').strip()
    elif line.startswith('OPTIONS: '):
        options_str = line.replace('OPTIONS: ', '').strip()
    elif line.startswith('PROBLEM: '):
        problem = line.replace('PROBLEM: ', '').strip()

try:
    criteria = json.loads(criteria_str)
except:
    criteria = ["cost", "complexity", "reliability", "maintainability"]
try:
    options = json.loads(options_str)
except:
    options = ["Option A", "Option B"]

def try_score():
    raw = llm_generate(
        f"Score each option against each criterion on a 1-5 scale (5=best). "
        f"Output ONLY a JSON array of objects with keys 'option', 'criterion', 'score'. "
        f"No reasons. Example: [{{\"option\":\"A\",\"criterion\":\"cost\",\"score\":3}}]. "
        f"One entry per combination.\n\nOptions: {json.dumps(options)}\nCriteria: {json.dumps(criteria)}\nProblem: {problem}"
    )
    scores = json.loads(raw.strip())
    if not isinstance(scores, list) or len(scores) < len(options):
        raise ValueError("incomplete scores")
    return scores

# Triple-try with majority vote
results = []
for i in range(3):
    try:
        results.append(try_score())
    except:
        results.append(None)

valid = [r for r in results if r is not None]
if valid:
    # Compare by serializing sorted by (option, criterion)
    def sort_key(s):
        return sorted(s, key=lambda x: (x.get("option",""), x.get("criterion","")))
    serialized = [json.dumps(sort_key(r), default=str) for r in valid]
    most_common = Counter(serialized).most_common(1)[0][0]
    scores = json.loads(most_common)
else:
    scores = []
    for opt in options:
        for crit in criteria:
            scores.append({"option": opt, "criterion": crit, "score": 3})

result = f"SCORES: {json.dumps(scores)}\nCRITERIA: {json.dumps(criteria)}\nOPTIONS: {json.dumps(options)}\nPROBLEM: {problem}"
print(result)
```

[validate: contains "SCORES:"]

---

### Step 4: Add one-sentence reasons for each score (triple-try)

Now the model explains WHY each score was assigned. Separate from scoring so the model focuses on one thing at a time. Triple-try for consistency.

```python
import json
from collections import Counter

# Parse from Step 3
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

def try_reasons():
    raw = llm_generate(
        f"For each option-criterion pair, give ONE sentence explaining the score. "
        f"Output ONLY a JSON array of objects with keys 'option', 'criterion', 'reason'. "
        f"Example: [{{\"option\":\"A\",\"criterion\":\"cost\",\"reason\":\"Low cost because uses existing tools\"}}].\n\n"
        f"Scores: {json.dumps(scores)}\nProblem: {problem}"
    )
    reasons = json.loads(raw.strip())
    if not isinstance(reasons, list) or len(reasons) < len(scores):
        raise ValueError("incomplete reasons")
    return reasons

# Triple-try
results = []
for i in range(3):
    try:
        results.append(try_reasons())
    except:
        results.append(None)

valid = [r for r in results if r is not None]
if valid:
    serialized = [json.dumps(sorted(r, key=lambda x: (x.get("option",""), x.get("criterion",""))), default=str) for r in valid]
    most_common = Counter(serialized).most_common(1)[0][0]
    reasons = json.loads(most_common)
else:
    reasons = []
    for s in scores:
        reasons.append({"option": s.get("option",""), "criterion": s.get("criterion",""), "reason": "neutral default"})

# Merge scores with reasons
score_map = {}
for r in reasons:
    key = (r.get("option",""), r.get("criterion",""))
    score_map[key] = r.get("reason","neutral default")

merged = []
for s in scores:
    key = (s.get("option",""), s.get("criterion",""))
    merged.append({**s, "reason": score_map.get(key, "neutral default")})

result = f"SCORES_WITH_REASONS: {json.dumps(merged)}\nCRITERIA: {json.dumps(criteria)}\nOPTIONS: {json.dumps(options)}\nPROBLEM: {problem}"
print(result)
```

[validate: contains "SCORES_WITH_REASONS:"]

---

### Step 5: Identify the dominant trade-off (triple-try)

The model identifies which criteria are in tension. Bounded observation task. Triple-try for consistency.

```python
import json
from collections import Counter

# Parse from Step 4
lines = output.strip().split('\n')
scores_str = ''
criteria_str = ''
options_str = ''
problem = ''
for line in lines:
    if line.startswith('SCORES_WITH_REASONS: '):
        scores_str = line.replace('SCORES_WITH_REASONS: ', '').strip()
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

def try_dominant():
    raw = llm_generate(
        f"Looking at these scores, which two criteria are in the most direct tension "
        f"(i.e. options that score high on one score low on the other)? "
        f"Output ONE sentence describing the dominant trade-off.\n\nScores: {json.dumps(scores)}"
    ).strip()
    if len(raw) > 200:
        raw = raw[:200]
    if len(raw) < 10:
        raise ValueError("too short")
    return raw

# Triple-try
results = []
for i in range(3):
    try:
        results.append(try_dominant())
    except:
        results.append(None)

valid = [r for r in results if r is not None]
if valid:
    # Pick most common (or longest if all different)
    most_common = Counter(valid).most_common(1)[0][0]
    dominant = most_common
else:
    # DETERMINISTIC FALLBACK: find criteria with most variance
    if scores and criteria:
        import statistics
        crit_vars = {}
        for crit in criteria:
            crit_scores = [s.get("score", 3) for s in scores if s.get("criterion") == crit]
            if len(crit_scores) > 1:
                crit_vars[crit] = statistics.variance(crit_scores)
        if len(crit_vars) >= 2:
            sorted_crits = sorted(crit_vars, key=crit_vars.get, reverse=True)
            dominant = f"Primary tension: {sorted_crits[0]} vs {sorted_crits[1]}"
        else:
            dominant = "No clear dominant trade-off identified"
    else:
        dominant = "Unable to identify dominant trade-off"

result = f"DOMINANT_TRADEOFF: {dominant}\nSCORES: {json.dumps(scores)}\nCRITERIA: {json.dumps(criteria)}\nOPTIONS: {json.dumps(options)}\nPROBLEM: {problem}"
print(result)
```

[validate: contains "DOMINANT_TRADEOFF:"]

---

### Step 6: Identify Pareto-optimal options (DETERMINISTIC — zero LLM cost)

An option is Pareto-optimal if no other option scores >= on all criteria AND > on at least one. This is pure math — no model needed.

```python
import json

# Parse from Step 5
lines = output.strip().split('\n')
scores_str = ''
criteria_str = ''
options_str = ''
problem = ''
dominant = ''
for line in lines:
    if line.startswith('SCORES: '):
        scores_str = line.replace('SCORES: ', '').strip()
    elif line.startswith('CRITERIA: '):
        criteria_str = line.replace('CRITERIA: ', '').strip()
    elif line.startswith('OPTIONS: '):
        options_str = line.replace('OPTIONS: ', '').strip()
    elif line.startswith('PROBLEM: '):
        problem = line.replace('PROBLEM: ', '').strip()
    elif line.startswith('DOMINANT_TRADEOFF: '):
        dominant = line.replace('DOMINANT_TRADEOFF: ', '').strip()

try:
    scores = json.loads(scores_str)
    criteria = json.loads(criteria_str)
    options = json.loads(options_str)
except:
    scores = []
    criteria = []
    options = []

# Build option->criterion->score mapping
option_scores = {}
for entry in scores:
    opt = entry.get("option", "")
    crit = entry.get("criterion", "")
    score = entry.get("score", 3)
    if opt not in option_scores:
        option_scores[opt] = {}
    option_scores[opt][crit] = score

# Find Pareto-optimal options (deterministic)
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
    pareto = options  # fallback: all are Pareto-optimal if no domination

result = f"PARETO_OPTIMAL: {json.dumps(pareto)}\nDOMINANT_TRADEOFF: {dominant}\nSCORES: {json.dumps(scores)}\nCRITERIA: {json.dumps(criteria)}\nOPTIONS: {json.dumps(options)}\nPROBLEM: {problem}"
print(result)
```

[validate: contains "PARETO_OPTIMAL:"]

---

### Step 7: Recommend with explicit trade-off acknowledgment (triple-try)

The model synthesizes — but the Pareto set constrains the recommendation. This is the hermeneutic step: interpreting what the scores mean. Triple-try for consistency.

```python
import json
from collections import Counter

# Parse from Step 6
lines = output.strip().split('\n')
pareto_str = ''
dominant = ''
scores_str = ''
problem = ''
for line in lines:
    if line.startswith('PARETO_OPTIMAL: '):
        pareto_str = line.replace('PARETO_OPTIMAL: ', '').strip()
    elif line.startswith('DOMINANT_TRADEOFF: '):
        dominant = line.replace('DOMINANT_TRADEOFF: ', '').strip()
    elif line.startswith('SCORES: '):
        scores_str = line.replace('SCORES: ', '').strip()
    elif line.startswith('PROBLEM: '):
        problem = line.replace('PROBLEM: ', '').strip()

try:
    pareto = json.loads(pareto_str)
except:
    pareto = []

def try_recommend():
    raw = llm_generate(
        f"Based on the Pareto-optimal options and the dominant trade-off, "
        f"write a 2-3 sentence recommendation that EXPLICITLY names what is being "
        f"sacrificed and what is being gained. Do not hedge.\n\n"
        f"Pareto-optimal options: {json.dumps(pareto)}\n"
        f"Dominant trade-off: {dominant}\n"
        f"Problem: {problem}"
    ).strip()
    if len(raw) < 20:
        raise ValueError("too short")
    return raw

# Triple-try
results = []
for i in range(3):
    try:
        results.append(try_recommend())
    except:
        results.append(None)

valid = [r for r in results if r is not None]
if valid:
    # Pick most common, or first if all different
    most_common = Counter(valid).most_common(1)[0][0]
    recommendation = most_common
else:
    if pareto:
        if len(pareto) == 1:
            recommendation = f"Recommend {pareto[0]}. This is the only Pareto-optimal option. Trade-off: {dominant}."
        else:
            recommendation = f"Pareto-optimal options: {', '.join(pareto)}. Dominant trade-off: {dominant}. Choose based on which criterion matters most for this context."
    else:
        recommendation = "Unable to generate recommendation — no Pareto-optimal options identified."

result = f"RECOMMENDATION: {recommendation}\nPARETO_OPTIMAL: {json.dumps(pareto)}\nDOMINANT_TRADEOFF: {dominant}"
print(result)
```

[validate: contains "RECOMMENDATION:"]

---

## Research Justification

1. **Dual-process theory** ([[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]]): Humans construct preferences on the fly. Structured scoring forces System 2 engagement, preventing preference errors.

2. **Decision theory** ([[psychology-of-analytical-thinking-methods-when-to-use-root-cause-analysis-vs-fir]]): Multi-attribute utility analysis outperforms intuitive judgment for choices with 3+ competing criteria. The 1-5 scoring grid IS a simplified MAUA.

3. **Pareto efficiency** (deterministic step): Identifying non-dominated options is purely mathematical — no judgment needed. This reduces the LLM's job from "pick the best option" to "explain the trade-off," which is much easier for a small model.

4. **Knowledge Triad** ([[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]]): Ontology = define options and criteria. Epistemology = score each option. Hermeneutics = interpret the pattern.

5. **Deterministic Scaffolding** ([[Deterministic-Scaffolding-for-Small-Models]]): Every LLM call has a deterministic fallback. The Pareto check is fully deterministic. Triple-try consistency catches small model variance.

6. **Bite-sized steps**: Scoring (numbers only) and reasoning (one-sentence explanations) are now separate steps so the model does one thing at a time.

## Related

- [[Think]] — parent procedure that dispatches to this lens
- [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]] — philosophical foundation
- [[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]] — research basis
- [[Deterministic-Scaffolding-for-Small-Models]] — sandwich pattern applied here