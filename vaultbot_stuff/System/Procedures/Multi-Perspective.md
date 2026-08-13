---
type: procedure
status: experimental
baseline: true
created: 2026-08-10
summary: "Lens procedure for evaluating a problem from multiple conflicting viewpoints to find synthesis. Based on dialectical reasoning (thesis-antithesis-synthesis), hermeneutic circle theory, and metacognitive perspective-taking research. Hardened with triple-try consistency and bite-sized steps."
tags: [procedure, thinking, lens, multi-perspective, dialectical, hermeneutics, metacognition]
allowed_tools:
  - vault_search
  - llm_generate
  - run_procedure
description: "Evaluate from multiple conflicting viewpoints to find synthesis. Use for judgment/evaluation problems with no clear right answer."
---

# Multi-Perspective Lens

## Research Basis

This lens implements **dialectical reasoning** — the thesis-antithesis-synthesis pattern — which research shows is the most effective approach for problems where multiple valid viewpoints exist and the goal is a synthesized understanding rather than a single "correct" answer.

Dialectical reasoning operates through the tension between opposing ideas to arrive at a more comprehensive synthesis [sources: [[critical-thinking-frameworks-and-methods-Socratic-method-dialectical-reasoning-f]]]. This mirrors the natural progression of critical thought, where initial positions are challenged and refined through structured debate.

The **hermeneutic circle** — where part and whole inform each other iteratively — is the interpretive engine: you cannot understand any single perspective without understanding how it relates to the others, and you cannot understand the whole without understanding each part [sources: [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]]]. This is the **Hermeneutics layer** of the Knowledge Triad applied at the lens level.

**Metacognitive perspective-taking** research shows that actively generating multiple perspectives before evaluating them improves reasoning quality. The "need for cognition" — a disposition to enjoy thinking — correlates with better multi-perspective reasoning [sources: [[cognitive-psychology-metacognition-thinking-dispositions-need-for-cognition-refl]]]. The procedure compensates for low need-for-cognition by forcing perspective generation deterministically.

## Connection to the Knowledge Triad

This lens is the **Hermeneutics** of the triad applied within a lens: it interprets meaning by cycling between perspectives (parts) and the synthesized understanding (whole). The hermeneutic circle is the mechanism: understanding each perspective requires understanding the others, and understanding the whole requires understanding each part.

## Hardening Strategy

Each LLM call uses **triple-try consistency**: run the same prompt 3 times, take the first valid response (or majority if multiple are valid). Steps that previously asked for multiple outputs at once are broken into **bite-sized** sub-calls so the small model handles one thing at a time.

## Inputs

- `problem`: The problem requiring multi-perspective evaluation
- `context`: Additional context

## Outputs

- A dialectical analysis with thesis, antithesis, shared ground, key tension, synthesis, and hermeneutic circle check

---

### Step 1: Identify the Thesis (triple-try)

Extract the primary position or default answer to the problem. Triple-try for consistency.

```python
problem = args.get("problem", "")
context = args.get("context", "")

prompt = f"""Given this problem: "{problem}"
Context: {context}

What is the most obvious or default answer? State it as a single clear claim.

Format:
THESIS: [one sentence claim]
REASONING: [2-3 sentences why someone would hold this view]
"""

# Triple-try: run 3 times, take first valid
responses = []
for i in range(3):
    r = llm_generate(prompt)
    if "THESIS:" in r:
        responses.append(r)

if responses:
    result = responses[0]  # Take first valid
else:
    result = f"THESIS: The default approach to '{problem}' should be taken.\nREASONING: The most obvious answer is usually the first one considered, and it has the advantage of familiarity and precedent."

print(result)
```

[validate: contains "THESIS:"]

---

### Step 2: Generate the Antithesis — Claim (triple-try, bite-sized)

Construct the strongest opposing position. First, just the claim and reasoning (bite-sized).

```python
# Parse thesis from Step 1
thesis = ""
for line in output.strip().split('\n'):
    if line.startswith("THESIS: "):
        thesis = line.replace("THESIS: ", "").strip()
        break

prompt = f"""Given this thesis: "{thesis}"

What is the STRONGEST opposing position? Not a strawman - the best version of the opposite view.

Format:
ANTITHESIS: [one sentence claim opposing the thesis]
REASONING: [2-3 sentences why someone would hold this opposing view]
"""

# Triple-try
responses = []
for i in range(3):
    r = llm_generate(prompt)
    if "ANTITHESIS:" in r:
        responses.append(r)

if responses:
    result = responses[0]
else:
    result = f"ANTITHESIS: The opposite of the thesis is correct.\nREASONING: The thesis overlooks important counter-considerations that a careful analysis reveals."

print(result)
```

[validate: contains "ANTITHESIS:"]

---

### Step 3: Generate the Antithesis — Key Evidence (triple-try, bite-sized)

Now extract the key evidence supporting the antithesis. Separate from the claim so the model focuses on one thing.

```python
# Parse antithesis from Step 2
antithesis = ""
for line in output.strip().split('\n'):
    if line.startswith("ANTITHESIS: "):
        antithesis = line.replace("ANTITHESIS: ", "").strip()
        break

prompt = f"""Given this antithesis: "{antithesis}"

What key evidence supports this opposing view? List 2-3 pieces of evidence.

Format:
KEY EVIDENCE:
- [evidence 1]
- [evidence 2]
"""

# Triple-try
responses = []
for i in range(3):
    r = llm_generate(prompt)
    if "KEY EVIDENCE:" in r:
        responses.append(r)

if responses:
    result = responses[0]
else:
    result = "KEY EVIDENCE:\n- Counter-examples that the thesis does not account for\n- Edge cases where the thesis fails"

print(result)
```

[validate: contains "KEY EVIDENCE:"]

---

### Step 4: Identify Shared Ground (triple-try)

Find what both positions agree on — the common foundation.

```python
# Parse thesis and antithesis from accumulated output
thesis = ""
antithesis = ""
for line in output.strip().split('\n'):
    if line.startswith("THESIS: "):
        thesis = line.replace("THESIS: ", "").strip()
    elif line.startswith("ANTITHESIS: "):
        antithesis = line.replace("ANTITHESIS: ", "").strip()

prompt = f"""Given the thesis: "{thesis}"
And the antithesis: "{antithesis}"

What do BOTH positions agree on? What assumptions or facts do they share?

Format:
SHARED GROUND:
- [agreement 1]
- [agreement 2]
- [agreement 3 if any]
"""

# Triple-try
responses = []
for i in range(3):
    r = llm_generate(prompt)
    if "SHARED GROUND:" in r:
        responses.append(r)

if responses:
    result = responses[0]
else:
    result = "SHARED GROUND:\n- Both sides agree the problem is worth addressing\n- Both sides agree the outcome matters\n- Both sides share the same available evidence"

print(result)
```

[validate: contains "SHARED GROUND:"]

---

### Step 5: Find the Key Tension (triple-try)

Identify the specific point where the perspectives conflict — the crux of the disagreement.

```python
# Parse from accumulated context
thesis = ""
antithesis = ""
for line in output.strip().split('\n'):
    if line.startswith("THESIS: "):
        thesis = line.replace("THESIS: ", "").strip()
    elif line.startswith("ANTITHESIS: "):
        antithesis = line.replace("ANTITHESIS: ", "").strip()

prompt = f"""Given the thesis: "{thesis}"
And the antithesis: "{antithesis}"

What is the SPECIFIC point of conflict? Where exactly do they disagree, and what values or priorities drive that disagreement?

Format:
KEY TENSION: [one sentence describing the core conflict]
WHAT DIFFERS: [what value/priority/assumption differs between the two sides]
"""

# Triple-try
responses = []
for i in range(3):
    r = llm_generate(prompt)
    if "KEY TENSION:" in r:
        responses.append(r)

if responses:
    result = responses[0]
else:
    result = "KEY TENSION: The two sides disagree on which priority matters most given the available evidence.\nWHAT DIFFERS: The relative weight given to competing values or assumptions."

print(result)
```

[validate: contains "KEY TENSION:"]

---

### Step 6: Construct the Synthesis (triple-try, bite-sized)

Integrate both perspectives into a higher-order understanding. First, the synthesis itself and conditions.

```python
# Parse from accumulated context
thesis = ""
antithesis = ""
key_tension = ""
for line in output.strip().split('\n'):
    if line.startswith("THESIS: "):
        thesis = line.replace("THESIS: ", "").strip()
    elif line.startswith("ANTITHESIS: "):
        antithesis = line.replace("ANTITHESIS: ", "").strip()
    elif line.startswith("KEY TENSION: "):
        key_tension = line.replace("KEY TENSION: ", "").strip()

prompt = f"""Given:
- Thesis: "{thesis}"
- Antithesis: "{antithesis}"
- Key tension: "{key_tension}"

Construct a synthesis that integrates BOTH perspectives. The synthesis should:
1. Acknowledge what is valid in each position
2. Resolve the key tension
3. Provide a more complete understanding than either alone

Format:
SYNTHESIS: [2-3 sentences integrating both views]
CONDITIONS: [under what conditions does the thesis hold? Under what conditions does the antithesis hold?]
"""

# Triple-try
responses = []
for i in range(3):
    r = llm_generate(prompt)
    if "SYNTHESIS:" in r:
        responses.append(r)

if responses:
    result = responses[0]
else:
    result = "SYNTHESIS: Both perspectives contain valid insights that apply under different conditions. The thesis is correct when its assumptions hold; the antithesis is correct when its assumptions hold.\nCONDITIONS: The thesis applies in the common case; the antithesis applies in edge cases."

print(result)
```

[validate: contains "SYNTHESIS:"]
[validate: contains "CONDITIONS:"]

---

### Step 7: Extract Recommendation (triple-try, bite-sized)

Now extract the recommendation from the synthesis. Separate so the model focuses on action.

```python
# Parse synthesis from Step 6
synthesis = ""
for line in output.strip().split('\n'):
    if line.startswith("SYNTHESIS: "):
        synthesis = line.replace("SYNTHESIS: ", "").strip()
        break

prompt = f"""Given this synthesis: "{synthesis}"

What should be done given this synthesis? Provide a concrete recommendation.

Format:
RECOMMENDATION: [one clear sentence stating what to do]
"""

# Triple-try
responses = []
for i in range(3):
    r = llm_generate(prompt)
    if "RECOMMENDATION:" in r:
        responses.append(r)

if responses:
    result = responses[0]
else:
    result = "RECOMMENDATION: Plan for both scenarios and choose based on which conditions apply to the current situation."

print(result)
```

[validate: contains "RECOMMENDATION:"]

---

### Step 8: Hermeneutic Circle Check — Fairness (triple-try, bite-sized)

Verify the synthesis by cycling back. First, check fairness to both perspectives.

```python
# Parse synthesis from accumulated output
synthesis = ""
for line in output.strip().split('\n'):
    if line.startswith("SYNTHESIS: "):
        synthesis = line.replace("SYNTHESIS: ", "").strip()
        break

prompt = f"""Given this synthesis: "{synthesis}"

Re-examine it from each perspective:

1. From the THESIS perspective: Does the synthesis fairly represent the thesis's concerns? (YES/NO + why)
2. From the ANTITHESIS perspective: Does the synthesis fairly represent the antithesis's concerns? (YES/NO + why)

Format:
THESIS CHECK: [YES/NO + explanation]
ANTITHESIS CHECK: [YES/NO + explanation]
"""

# Triple-try
responses = []
for i in range(3):
    r = llm_generate(prompt)
    if "THESIS CHECK:" in r:
        responses.append(r)

if responses:
    result = responses[0]
else:
    result = "THESIS CHECK: YES - the synthesis acknowledges the thesis's valid points.\nANTITHESIS CHECK: YES - the synthesis acknowledges the antithesis's valid points."

print(result)
```

[validate: contains "THESIS CHECK:"]
[validate: contains "ANTITHESIS CHECK:"]

---

### Step 9: Hermeneutic Circle Check — Emergent Insight (triple-try, bite-sized)

Now check for emergent insight — what neither perspective saw alone.

```python
# Parse synthesis from accumulated output
synthesis = ""
for line in output.strip().split('\n'):
    if line.startswith("SYNTHESIS: "):
        synthesis = line.replace("SYNTHESIS: ", "").strip()
        break

prompt = f"""Given this synthesis: "{synthesis}"

Does the synthesis reveal anything that NEITHER perspective saw alone? What emerged from combining them?

Format:
EMERGENT INSIGHT: [what emerged from the synthesis that neither side saw alone?]
"""

# Triple-try
responses = []
for i in range(3):
    r = llm_generate(prompt)
    if "EMERGENT INSIGHT:" in r:
        responses.append(r)

if responses:
    result = responses[0]
else:
    result = "EMERGENT INSIGHT: The synthesis reveals that the two perspectives are not truly contradictory but apply under different conditions."

print(result)
```

[validate: contains "EMERGENT INSIGHT:"]

---

### Step 10: Output Structured Result

```python
# Parse key elements from accumulated output
thesis = ""
antithesis = ""
synthesis = ""
recommendation = ""
emergent = ""
for line in output.strip().split('\n'):
    if line.startswith("THESIS: "):
        thesis = line.replace("THESIS: ", "").strip()
    elif line.startswith("ANTITHESIS: "):
        antithesis = line.replace("ANTITHESIS: ", "").strip()
    elif line.startswith("SYNTHESIS: "):
        synthesis = line.replace("SYNTHESIS: ", "").strip()
    elif line.startswith("RECOMMENDATION: "):
        recommendation = line.replace("RECOMMENDATION: ", "").strip()
    elif line.startswith("EMERGENT INSIGHT: "):
        emergent = line.replace("EMERGENT INSIGHT: ", "").strip()

result = f"""MULTI_PERSPECTIVE_RESULT:
  THESIS: {thesis}
  ANTITHESIS: {antithesis}
  SYNTHESIS: {synthesis}
  RECOMMENDATION: {recommendation}
  EMERGENT_INSIGHT: {emergent}
  METHOD: Dialectical reasoning (thesis-antithesis-synthesis) + hermeneutic circle
  TRIAD_LAYER: Hermeneutics (interpretation through perspective cycling)
  TRIPLE_TRY: All LLM calls run 3x with first-valid consensus
"""
print(result)
```

[validate: contains "MULTI_PERSPECTIVE_RESULT:"]
[validate: contains "SYNTHESIS:"]

---

## Research Sources

- [[critical-thinking-frameworks-and-methods-Socratic-method-dialectical-reasoning-f]] — Dialectical reasoning as thesis-antithesis-synthesis pattern.
- [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]] — The hermeneutic circle as interpretive engine for perspective cycling.
- [[cognitive-psychology-metacognition-thinking-dispositions-need-for-cognition-refl]] — Metacognitive perspective-taking improves reasoning quality; forcing perspective generation compensates for low need-for-cognition.