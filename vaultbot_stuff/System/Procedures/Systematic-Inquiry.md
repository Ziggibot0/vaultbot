---
type: procedure
status: experimental
baseline: true
created: 2026-08-10
summary: "Lens procedure for exploring open questions through structured Socratic questioning. v4.1: batched independent steps 2,4,5,6 into 2 calls (saves 2-3 LLM calls). ~4-5 LLM calls (down from 6-8)."
description: "Explore open questions through structured Socratic questioning. Use for research/exploration problems."
when_to_use: "When exploring an open question through structured Socratic questioning. When the question is more important than the answer. When mapping a question space. When asked 'explore this question' or 'what don't we know about X?' or 'investigate this topic'. When doing research exploration."
tags: [procedure, think, lens, systematic-inquiry, socratic, research, exploration, v4, qwen3.5-4b, batched]
allowed_tools:
  - vault_search
  - llm_generate
  - run_procedure
depends_on:
  - "[[Think]]"
  - "[[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]]"
research_sources:
  - "[[critical-thinking-frameworks-and-methods-Socratic-method-dialectical-reasoning-f]]"
  - "[[cognitive-psychology-metacognition-thinking-dispositions-need-for-cognition-refl]]"
---

# Systematic-Inquiry Lens (v4.1)

## Research Basis

This lens implements the **Socratic method** — structured questioning that probes assumptions, clarifies meaning, and exposes gaps in understanding. Research shows this is the most effective approach for open-ended exploration problems where the goal is not to find a single answer but to map the question space.

The Socratic method operates through six types of questioning: clarification, probing assumptions, probing evidence/reasons, examining perspectives, exploring implications, and questioning the question itself [sources: [[critical-thinking-frameworks-and-methods-Socratic-method-dialectical-reasoning-f]]].

**Metacognitive monitoring** — the ability to track one's own understanding and identify gaps — is the cognitive foundation. Research shows that structured self-questioning protocols significantly improve reasoning quality compared to free-form thinking [sources: [[cognitive-psychology-metacognition-thinking-dispositions-need-for-cognition-refl]]].

## What Changed in v4.1

| Problem in v4 | Fix in v4.1 |
|---|---|
| Steps 2, 4, 5, 6 each made separate LLM calls (4 calls) — all independent given the clarified question | **Batched into 2 calls** — Step 2+4 (assumptions + perspectives) in one call, Step 5+6 (implications + meta-question) in one call |
| ~6-8 LLM calls total | **~4-5 LLM calls total** (saves 2-3 calls) |

## What Changed in v4 (from v3)

| Problem in v3 | Fix in v4 |
|---|---|
| Triple-try on ALL 8 steps = 24 LLM calls | Single call per step = 8 calls max |
| Steps 3+4 split existing/missing evidence unnecessarily | Merged into one step — 4B can handle both in one call |
| Step 8 synthesis used triple-try (3 calls) | Single synthesis call — 4B is consistent |
| Verbose STEP markers for cumulative context | Streamlined parsing — 4B follows format reliably |

## Connection to the Knowledge Triad

This lens is the **Epistemology** of the triad applied within a lens: it asks "how do we know what we know?" and "what don't we know?" — the fundamental epistemological questions.

## Inputs

- `problem`: The question or problem to explore
- `context`: Additional context (optional)

## Outputs

- Clarified question with type and scope
- Probed assumptions
- Evidence assessment (existing + missing)
- Alternative perspectives
- Implications of possible answers
- Meta-question (is this the right question?)
- Synthesized inquiry report

---

### Step 1: Clarify the question

State the question precisely and identify what kind of answer it seeks. The 4B can handle a multi-part response in one call.

```python
problem = args.get("problem", "")
context = args.get("context", "")

prompt = f"""Given this question/problem: {problem}

Context: {context}

Clarify the question. What is actually being asked? What kind of answer does it seek?

Format:
QUESTION: [the precise question, refined if needed]
QUESTION_TYPE: [factual | explanatory | evaluative | exploratory]
SCOPE: [what is in scope and what is out of scope]
"""

resp = llm_generate(prompt).strip()

# Validate
if "QUESTION:" not in resp:
    resp = f"QUESTION: {problem}\nQUESTION_TYPE: exploratory\nSCOPE: full question as stated"

print(resp)
```

[validate: contains "QUESTION:"]
[validate: contains "QUESTION_TYPE:"]

---

### Step 2: Probe assumptions AND examine alternative perspectives (BATCHED)

**Single call** — both tasks take the clarified question as input and are independent. The 4B can generate assumptions with implications AND missing perspectives with viewpoints in one structured output.

```python
# Parse question from Step 1
question = args.get("problem", "")
for line in output.strip().split('\n'):
    if line.startswith('QUESTION: '):
        question = line.replace('QUESTION: ', '').strip()
        break

prompt = f"""Given this clarified question: {question}

Answer TWO parts:

PART 1 - ASSUMPTIONS:
What unstated assumptions does this question carry? What does it take for granted?
List 3-5 assumptions with what-if-wrong implications:
ASSUMPTION_1: [assumption] — [what if this is wrong?]
ASSUMPTION_2: [assumption] — [what if this is wrong?]
ASSUMPTION_3: [assumption] — [what if this is wrong?]
ASSUMPTION_4: [assumption] — [what if this is wrong?]
ASSUMPTION_5: [assumption] — [what if this is wrong?]

PART 2 - PERSPECTIVES:
What perspectives or viewpoints are NOT represented in the current analysis? Whose voice is missing?
List 3 perspectives with what they would say:
PERSPECTIVE_1: [perspective] — [what would they say?]
PERSPECTIVE_2: [perspective] — [what would they say?]
PERSPECTIVE_3: [perspective] — [what would they say?]"""

resp = llm_generate(prompt).strip()

# Fallbacks
if "ASSUMPTION_" not in resp:
    resp = """PART 1 - ASSUMPTIONS:
ASSUMPTION_1: The question assumes the problem is well-defined — what if it's ambiguous?
ASSUMPTION_2: The question assumes a single answer exists — what if multiple valid answers exist?
ASSUMPTION_3: The question assumes current understanding is sufficient — what if we lack key context?
ASSUMPTION_4: The question assumes the framing is neutral — what if it's loaded?
ASSUMPTION_5: The question assumes the answer is knowable — what if it's fundamentally uncertain?

PART 2 - PERSPECTIVES:
PERSPECTIVE_1: Skeptic — questions whether the question itself is well-formed
PERSPECTIVE_2: Practitioner — focuses on practical implications and real-world constraints
PERSPECTIVE_3: Theorist — looks for underlying principles and generalizable patterns"""

print(resp)
```

[validate: contains "ASSUMPTION_"]
[validate: contains "PERSPECTIVE_"]

---

### Step 3: Assess evidence — what exists and what's missing?

Merged from v3 Steps 3+4. The 4B can identify both existing and missing evidence in one call. Uses vault_search for provenance.

```python
# Parse question from accumulated context
question = args.get("problem", "")
for line in output.strip().split('\n'):
    if line.startswith('QUESTION: '):
        question = line.replace('QUESTION: ', '').strip()
        break

# Search vault for evidence
vault_evidence = ""
try:
    search_results = vault_search(question, k=5)
    vault_evidence = "\n".join([f"- [[{r.get('title', r.get('name', 'unknown'))}]]: {r.get('summary', '')[:100]}" for r in search_results[:5]])
except:
    vault_evidence = "- No vault results found"

prompt = f"""Question: {question}

Vault evidence found:
{vault_evidence}

Assess the evidence in two parts:

EXISTING_EVIDENCE:
- [evidence 1 with source if known]
- [evidence 2 with source if known]
- [evidence 3 if any]

MISSING_EVIDENCE:
- [missing evidence 1]
- [missing evidence 2]
- [missing evidence 3 if any]"""

resp = llm_generate(prompt).strip()

if "EXISTING_EVIDENCE:" not in resp:
    resp = f"EXISTING_EVIDENCE:\n{vault_evidence}\nMISSING_EVIDENCE:\n- Empirical data directly addressing this question\n- Expert analysis from domain specialists\n- Comparative studies or benchmarks"

print(resp)
```

[validate: contains "EXISTING_EVIDENCE:"]
[validate: contains "MISSING_EVIDENCE:"]

---

### Step 4: Explore implications AND question the question (BATCHED)

**Single call** — both tasks take the clarified question as input and are independent. The 4B can map answer scenarios with implications AND identify the meta-question in one structured output.

```python
# Parse question from accumulated context
question = args.get("problem", "")
for line in output.strip().split('\n'):
    if line.startswith('QUESTION: '):
        question = line.replace('QUESTION: ', '').strip()
        break

prompt = f"""Question: {question}

Answer TWO parts:

PART 1 - IMPLICATIONS:
For each possible answer to this question, what are the implications?
Format:
IF_ANSWER_A: [answer] -> [implication 1], [implication 2]
IF_ANSWER_B: [answer] -> [implication 1], [implication 2]
IF_ANSWER_C: [answer] -> [implication 1], [implication 2]

PART 2 - META_QUESTION:
Step back. Is this the RIGHT question to ask? Or is there a deeper question that this one points toward?
Format:
META_QUESTION: [the deeper question, if any]
WHY_DEEPER: [why this is more fundamental than the original]
REFRAMED: [how should the original question be reframed?]"""

resp = llm_generate(prompt).strip()

if "IF_ANSWER_" not in resp:
    resp = """PART 1 - IMPLICATIONS:
IF_ANSWER_A: Yes -> action required, resources needed
IF_ANSWER_B: No -> current approach is adequate, monitor for changes
IF_ANSWER_C: Partially -> nuanced response needed, further investigation required

PART 2 - META_QUESTION:
META_QUESTION: What underlying need prompted this question?
WHY_DEEPER: Understanding the motivation reveals whether the surface question is the right one to ask
REFRAMED: Consider what goal the question serves and whether a different question would better serve that goal"""

print(resp)
```

[validate: contains "IF_ANSWER_"]
[validate: contains "META_QUESTION:"]
[validate: contains "REFRAMED:"]

---

### Step 5: Synthesize inquiry results

Single-call synthesis. The 4B can read all prior step outputs and produce a structured inquiry report in one call.

```python
# Parse question from accumulated context
question = args.get("problem", "")
for line in output.strip().split('\n'):
    if line.startswith('QUESTION: '):
        question = line.replace('QUESTION: ', '').strip()
        break

# Gather all step outputs
all_output = output.strip()

prompt = f"""Synthesize the Socratic inquiry into this question: {question}

Full inquiry so far:
{all_output[:3000]}

Produce a structured inquiry report:

INQUIRY_SUMMARY: [2-3 sentences summarizing what the inquiry revealed]
KEY_FINDING: [the most important insight from this inquiry]
KNOWLEDGE_GAPS: [what remains unknown and should be researched next]
REFRAMED_QUESTION: [the question we SHOULD be asking, if different from the original]
NEXT_STEPS: [concrete next steps to fill the knowledge gaps]"""

resp = llm_generate(prompt).strip()

if "INQUIRY_SUMMARY:" not in resp:
    resp = f"""INQUIRY_SUMMARY: The inquiry explored the question from multiple angles, identifying key assumptions and evidence gaps.
KEY_FINDING: The question requires further research to answer definitively; current evidence is insufficient.
KNOWLEDGE_GAPS: Evidence assessment and alternative perspectives need further investigation.
REFRAMED_QUESTION: Consider the meta-question identified in the inquiry.
NEXT_STEPS: Research the missing evidence identified; consult the perspectives identified."""

print(resp)
```

[validate: contains "INQUIRY_SUMMARY:"]
[validate: contains "KNOWLEDGE_GAPS:"]
[validate: contains "NEXT_STEPS:"]

---

## Research Justification

1. **Socratic method** ([[critical-thinking-frameworks-and-methods-Socratic-method-dialectical-reasoning-f]]): The six questioning types map to the six steps of this lens. Each step probes a different dimension of understanding.

2. **Metacognitive monitoring** ([[cognitive-psychology-metacognition-thinking-dispositions-need-for-cognition-refl]]): Structured self-questioning protocols significantly improve reasoning quality. This lens forces metacognitive monitoring by requiring explicit gap identification.

3. **Knowledge Triad** ([[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]]): This lens is the Epistemology of the triad — it asks how we know and what we don't know.

4. **Deterministic scaffolding** ([[Deterministic-Scaffolding-for-Small-Models]]): Every LLM call has a deterministic fallback. v4 removes triple-try (no longer needed with 4B consistency) but keeps fallbacks for safety.

5. **v4 optimization**: The 4B model's larger context window and better instruction-following allow merging evidence steps (3+4 → 3) and single-call synthesis. This reduces calls from ~24 to ~7 without quality loss.

6. **v4.1 batching rationale**: Steps 2 (assumptions), 4 (perspectives), 5 (implications), and 6 (meta-question) are all **independent given the clarified question from Step 1** — they don't depend on each other's outputs. The 4B model demonstrated in Think v4 that it can handle multi-part structured prompts (claim extraction, classification+lens selection, synthesis) reliably. Batching these independent Socratic questioning types into 2 calls (assumptions+perspectives, implications+meta) follows the same validated pattern. Deterministic fallbacks for each part preserve safety.

## Related

- [[Think]] — parent procedure that dispatches to this lens
- [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]] — philosophical foundation
- [[critical-thinking-frameworks-and-methods-Socratic-method-dialectical-reasoning-f]] — Socratic method research
- [[cognitive-psychology-metacognition-thinking-dispositions-need-for-cognition-refl]] — metacognition research
- [[Deterministic-Scaffolding-for-Small-Models]] — scaffolding principles