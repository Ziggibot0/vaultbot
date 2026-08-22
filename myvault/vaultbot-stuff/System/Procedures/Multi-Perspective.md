---
type: procedure
status: verified
baseline: true
created: 2026-08-10
summary: "Lens procedure for evaluating a problem from multiple conflicting viewpoints to find synthesis. v4.1: batched thesis+antithesis into single call, merged shared ground + synthesis + hermeneutic check into 2 calls. ~3-4 LLM calls (down from 5)."
description: "Evaluate from multiple conflicting viewpoints to find synthesis. Use for judgment/evaluation problems with no clear right answer."
allowed_tools:
  - vault_search
  - llm_generate
  - run_procedure
when_to_use: "When evaluating a problem from multiple conflicting viewpoints. When there's no clear right answer. When you need thesis-antithesis-synthesis. When asked 'what are the different perspectives on this?' or 'evaluate this from multiple angles'. When doing dialectical analysis."
tags: [procedure, thinking, lens, multi-perspective, dialectical, hermeneutics, v4, qwen3.5-4b, batched]
depends_on:
  - "[[Think]]"
  - "[[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]]"
research_sources:
  - "[[critical-thinking-frameworks-and-methods-Socratic-method-dialectical-reasoning-f]]"
  - "[[cognitive-psychology-metacognition-thinking-dispositions-need-for-cognition-refl]]"
success_count: 11
failure_count: 2
success_rate: 0.85
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
model_cartridge: big
---

# Multi-Perspective Lens (v4.1)

## Research Basis

This lens implements **dialectical reasoning** — the thesis-antithesis-synthesis pattern — which research shows is the most effective approach for problems where multiple valid viewpoints exist and the goal is a synthesized understanding rather than a single "correct" answer.

Dialectical reasoning operates through the tension between opposing ideas to arrive at a more comprehensive synthesis [sources: [[critical-thinking-frameworks-and-methods-Socratic-method-dialectical-reasoning-f]]].

The **hermeneutic circle** — where part and whole inform each other iteratively — is the interpretive engine [sources: [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]]].

**Metacognitive perspective-taking** research shows that actively generating multiple perspectives before evaluating them improves reasoning quality [sources: [[cognitive-psychology-metacognition-thinking-dispositions-need-for-cognition-refl]]].

## What Changed in v4.1

| Problem in v4 | Fix in v4.1 |
|---|---|
| Step 1 (thesis) + Step 2 (antithesis) = 2 sequential calls | **Batched into 1 call** — thesis and antithesis are independent given the problem |
| Step 3 (shared ground) + Step 4 (synthesis) + Step 5 (hermeneutic check) = 3 sequential calls | **Merged into 2 calls** — shared ground + synthesis in one, hermeneutic check in one |
| ~5 LLM calls total | **~3-4 LLM calls total** (saves 1-2 calls) |

## What Changed in v4 (from v3)

| Problem in v3 | Fix in v4 |
|---|---|
| Triple-try on ALL 10 steps = 30 LLM calls | Single-call per step. 4B is consistent. |
| Thesis and antithesis were separate steps with separate evidence steps | Thesis+reasoning in one call, antithesis+evidence in one call |
| Shared ground and key tension were separate steps | Merged into one call — 4B can identify both |
| Synthesis and recommendation were separate steps | Merged into one call |
| Hermeneutic circle checks (fairness + emergent insight) were separate steps | Merged into one call |
| 10 steps, ~30 LLM calls | 5 steps, ~5 LLM calls |

## Why This Exists

Some problems have no single right answer — multiple valid viewpoints exist
and the goal is a synthesized understanding. This lens implements
thesis-antithesis-synthesis (dialectical reasoning) to evaluate such problems.
The tradeoff: it batches independent LLM calls to cut cost (v4.1 reduced ~5
calls to ~3-4), but keeps the hermeneutic check separate because it genuinely
depends on the synthesis output.

## Inputs

- `problem`: The problem requiring multi-perspective evaluation
- `context`: Additional context

## Outputs

- A dialectical analysis with thesis, antithesis, shared ground, key tension, synthesis, recommendation, and hermeneutic circle check

---

### Step 1: Generate thesis AND antithesis (BATCHED — single LLM call)

**Single call** — both the thesis (default/obvious position) and antithesis (strongest opposing position) are independent given the problem. The 4B can generate both with reasoning and evidence in one structured output.

```python
problem = args.get("problem", "")
context = args.get("context", "")

prompt = f"""Given this problem: "{problem}"
Context: {context}

Generate TWO opposing positions:

THESIS: The most obvious or default answer. State as a clear claim with reasoning.
Format:
THESIS: [one sentence claim]
THESIS_REASONING: [2-3 sentences why someone would hold this view]

ANTITHESIS: The STRONGEST opposing position — not a strawman, the best version of the opposite view. State the claim, reasoning, and 2-3 key pieces of evidence.
Format:
ANTITHESIS: [one sentence claim opposing the thesis]
ANTITHESIS_REASONING: [2-3 sentences why someone would hold this opposing view]
KEY_EVIDENCE:
- [evidence 1]
- [evidence 2]"""

resp = llm_generate(prompt).strip()

# Parse
thesis = ""
thesis_reasoning = ""
antithesis = ""
ant_reasoning = ""
evidence_lines = []
in_evidence = False
current_section = None

for line in resp.split('\n'):
    line = line.strip()
    if line.startswith("THESIS:"):
        thesis = line.replace("THESIS:", "").strip()
        current_section = "thesis"
    elif line.startswith("THESIS_REASONING:"):
        thesis_reasoning = line.replace("THESIS_REASONING:", "").strip()
        current_section = "thesis_reasoning"
    elif line.startswith("ANTITHESIS:"):
        antithesis = line.replace("ANTITHESIS:", "").strip()
        current_section = "antithesis"
    elif line.startswith("ANTITHESIS_REASONING:"):
        ant_reasoning = line.replace("ANTITHESIS_REASONING:", "").strip()
        current_section = "ant_reasoning"
    elif line.startswith("KEY_EVIDENCE:"):
        in_evidence = True
        current_section = "evidence"
    elif line.startswith("- ") and in_evidence:
        evidence_lines.append(line.replace("- ", "").strip())

# Fallbacks
if not thesis:
    thesis = f"The default approach to '{problem}' should be taken."
    thesis_reasoning = "The most obvious answer is usually the first one considered."
if not antithesis:
    antithesis = f"The opposite of the thesis is correct."
    ant_reasoning = "The thesis overlooks important counter-considerations."
    evidence_lines = ["Counter-examples the thesis doesn't account for", "Edge cases where the thesis fails"]

result = f"THESIS: {thesis}\nTHESIS_REASONING: {thesis_reasoning}\nANTITHESIS: {antithesis}\nANT_REASONING: {ant_reasoning}\nKEY_EVIDENCE: {'|'.join(evidence_lines)}\nPROBLEM: {problem}"
if context:
    result += f"\nCONTEXT: {context}"
print(result)
```

[validate: contains "THESIS:"]
[validate: contains "ANTITHESIS:"]

---

### Step 2: Identify shared ground, key tension, AND construct synthesis with recommendation (single call)

**Single call** — given the thesis and antithesis, the 4B can find shared ground, identify the key tension, AND construct a synthesis with recommendation in one structured output. These are naturally connected: you can't synthesize without knowing what's shared and where the tension lies.

```python
# Parse Step 1
lines = output.strip().split('\n')
thesis = ""
thesis_reasoning = ""
antithesis = ""
ant_reasoning = ""
problem = ""
context = ""
for line in lines:
    if line.startswith("THESIS: "):
        thesis = line.replace("THESIS: ", "").strip()
    elif line.startswith("THESIS_REASONING: "):
        thesis_reasoning = line.replace("THESIS_REASONING: ", "").strip()
    elif line.startswith("ANTITHESIS: "):
        antithesis = line.replace("ANTITHESIS: ", "").strip()
    elif line.startswith("ANT_REASONING: "):
        ant_reasoning = line.replace("ANT_REASONING: ", "").strip()
    elif line.startswith("PROBLEM: "):
        problem = line.replace("PROBLEM: ", "").strip()
    elif line.startswith("CONTEXT: "):
        context = line.replace("CONTEXT: ", "").strip()

prompt = f"""Given:
- Thesis: "{thesis}" (reasoning: {thesis_reasoning})
- Antithesis: "{antithesis}" (reasoning: {ant_reasoning})

Answer THREE parts:

PART 1 - SHARED GROUND & KEY TENSION:
What do BOTH positions agree on? What assumptions or facts do they share?
What is the SPECIFIC point of conflict? Where exactly do they disagree, and what values/priorities/assumptions drive that disagreement?

Format:
SHARED_GROUND:
- [agreement 1]
- [agreement 2]
- [agreement 3 if any]
KEY_TENSION: [one sentence describing the core conflict]
WHAT_DIFFERS: [what value/priority/assumption differs between the two sides]

PART 2 - SYNTHESIS & RECOMMENDATION:
Construct a synthesis that integrates BOTH perspectives. Then provide a concrete recommendation.
The synthesis should:
1. Acknowledge what is valid in each position
2. Resolve the key tension
3. Provide a more complete understanding than either alone

Format:
SYNTHESIS: [2-3 sentences integrating both views]
CONDITIONS: [under what conditions does the thesis hold? Under what conditions does the antithesis hold?]
RECOMMENDATION: [one clear sentence stating what to do]"""

resp = llm_generate(prompt).strip()

# Parse
shared_ground = []
key_tension = ""
what_differs = ""
synthesis = ""
conditions = ""
recommendation = ""
in_shared = False
current_part = None

for line in resp.split('\n'):
    line = line.strip()
    if line.startswith("SHARED_GROUND:"):
        in_shared = True
        current_part = "shared"
    elif line.startswith("KEY_TENSION:"):
        in_shared = False
        key_tension = line.replace("KEY_TENSION:", "").strip()
        current_part = "tension"
    elif line.startswith("WHAT_DIFFERS:"):
        what_differs = line.replace("WHAT_DIFFERS:", "").strip()
        current_part = "differs"
    elif line.startswith("SYNTHESIS:"):
        synthesis = line.replace("SYNTHESIS:", "").strip()
        current_part = "synthesis"
    elif line.startswith("CONDITIONS:"):
        conditions = line.replace("CONDITIONS:", "").strip()
        current_part = "conditions"
    elif line.startswith("RECOMMENDATION:"):
        recommendation = line.replace("RECOMMENDATION:", "").strip()
        current_part = "recommendation"
    elif line.startswith("- ") and in_shared:
        shared_ground.append(line.replace("- ", "").strip())

# Fallbacks
if not shared_ground:
    shared_ground = ["Both sides agree the problem is worth addressing", "Both sides agree the outcome matters"]
if not key_tension:
    key_tension = "The two sides disagree on which priority matters most given the available evidence."
if not what_differs:
    what_differs = "The relative weight given to competing values or assumptions."
if not synthesis:
    synthesis = "Both perspectives contain valid insights that apply under different conditions."
if not conditions:
    conditions = "The thesis applies in the common case; the antithesis applies in edge cases."
if not recommendation:
    recommendation = "Plan for both scenarios and choose based on which conditions apply to the current situation."

result = f"THESIS: {thesis}\nANTITHESIS: {antithesis}\nSHARED_GROUND: {'|'.join(shared_ground)}\nKEY_TENSION: {key_tension}\nWHAT_DIFFERS: {what_differs}\nSYNTHESIS: {synthesis}\nCONDITIONS: {conditions}\nRECOMMENDATION: {recommendation}\nPROBLEM: {problem}"
if context:
    result += f"\nCONTEXT: {context}"
print(result)
```

[validate: contains "SHARED_GROUND:"]
[validate: contains "KEY_TENSION:"]
[validate: contains "SYNTHESIS:"]
[validate: contains "RECOMMENDATION:"]

---

### Step 3: Hermeneutic circle check — fairness and emergent insight (single call)

Verify the synthesis by cycling back to both perspectives. Check fairness AND identify emergent insight in one call. The 4B can handle both checks together.

```python
# Parse Step 2
lines = output.strip().split('\n')
thesis = ""
antithesis = ""
synthesis = ""
recommendation = ""
problem = ""
for line in lines:
    if line.startswith("THESIS: "):
        thesis = line.replace("THESIS: ", "").strip()
    elif line.startswith("ANTITHESIS: "):
        antithesis = line.replace("ANTITHESIS: ", "").strip()
    elif line.startswith("SYNTHESIS: "):
        synthesis = line.replace("SYNTHESIS: ", "").strip()
    elif line.startswith("RECOMMENDATION: "):
        recommendation = line.replace("RECOMMENDATION: ", "").strip()
    elif line.startswith("PROBLEM: "):
        problem = line.replace("PROBLEM: ", "").strip()

prompt = f"""Given this synthesis: "{synthesis}"

Re-examine it from each perspective:

1. From the THESIS perspective: Does the synthesis fairly represent the thesis's concerns? (YES/NO + why)
2. From the ANTITHESIS perspective: Does the synthesis fairly represent the antithesis's concerns? (YES/NO + why)
3. Does the synthesis reveal anything that NEITHER perspective saw alone? What emerged from combining them?

Format:
THESIS_CHECK: [YES/NO + explanation]
ANTITHESIS_CHECK: [YES/NO + explanation]
EMERGENT_INSIGHT: [what emerged from the synthesis that neither side saw alone?]"""

resp = llm_generate(prompt).strip()

# Parse
thesis_check = ""
antithesis_check = ""
emergent = ""
for line in resp.split('\n'):
    if line.startswith("THESIS_CHECK:"):
        thesis_check = line.replace("THESIS_CHECK:", "").strip()
    elif line.startswith("ANTITHESIS_CHECK:"):
        antithesis_check = line.replace("ANTITHESIS_CHECK:", "").strip()
    elif line.startswith("EMERGENT_INSIGHT:"):
        emergent = line.replace("EMERGENT_INSIGHT:", "").strip()

# Fallback
if not thesis_check:
    thesis_check = "YES - the synthesis acknowledges the thesis's valid points."
if not antithesis_check:
    antithesis_check = "YES - the synthesis acknowledges the antithesis's valid points."
if not emergent:
    emergent = "The synthesis reveals that the two perspectives are not truly contradictory but apply under different conditions."

# Final output
result = f"""MULTI_PERSPECTIVE_RESULT:
  THESIS: {thesis}
  ANTITHESIS: {antithesis}
  SYNTHESIS: {synthesis}
  CONDITIONS: {conditions if 'conditions' in locals() else 'not available'}
  RECOMMENDATION: {recommendation}
  THESIS_CHECK: {thesis_check}
  ANTITHESIS_CHECK: {antithesis_check}
  EMERGENT_INSIGHT: {emergent}
  METHOD: Dialectical reasoning (thesis-antithesis-synthesis) + hermeneutic circle
  TRIAD_LAYER: Hermeneutics (interpretation through perspective cycling)"""
print(result)
```

[validate: contains "MULTI_PERSPECTIVE_RESULT:"]
[validate: contains "SYNTHESIS:"]
[validate: contains "EMERGENT_INSIGHT:"]

---

## Research Justification

1. **Dialectical reasoning** ([[critical-thinking-frameworks-and-methods-Socratic-method-dialectical-reasoning-f]]): Thesis-antithesis-synthesis is the most effective approach for problems with multiple valid viewpoints.

2. **Hermeneutic circle** ([[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]]): Understanding each perspective requires understanding the others, and understanding the whole requires understanding each part.

3. **Metacognitive perspective-taking** ([[cognitive-psychology-metacognition-thinking-dispositions-need-for-cognition-refl]]): Actively generating multiple perspectives before evaluating them improves reasoning quality.

4. **Deterministic Scaffolding** ([[Deterministic-Scaffolding-for-Small-Models]]): Every LLM call has a deterministic fallback. The 4B model is consistent enough that triple-try is no longer needed — single calls with fallbacks suffice.

5. **v4.1 batching rationale**: 
   - **Thesis + Antithesis batching**: These are independent given the problem statement — neither depends on the other's output. The 4B model demonstrated in Think v4 that it can handle multi-part structured prompts (claim extraction, classification+lens selection, synthesis) reliably. Batching these into one call follows the same validated pattern.
   - **Shared ground + Synthesis + Recommendation merging**: These are naturally sequential but tightly coupled — you can't synthesize without identifying shared ground and key tension. The 4B can produce all three in one structured output (PART 1 + PART 2 format). This mirrors the Think v4 pattern where classification+lens selection and synthesis were merged.
   - **Hermeneutic check kept separate**: This step cycles back to evaluate the synthesis from both perspectives — it genuinely depends on the synthesis output. Keeping it as a separate call preserves the hermeneutic circle's iterative nature while still reducing total calls from 5 to 3-4.
   - Deterministic fallbacks for each part preserve safety.

## Related

- [[Think]] — parent procedure that dispatches to this lens
- [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]] — philosophical foundation
- [[critical-thinking-frameworks-and-methods-Socratic-method-dialectical-reasoning-f]] — dialectical reasoning research
- [[cognitive-psychology-metacognition-thinking-dispositions-need-for-cognition-refl]] — metacognitive perspective-taking research
- [[Deterministic-Scaffolding-for-Small-Models]] — scaffolding principles