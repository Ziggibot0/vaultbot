---
type: procedure
status: experimental
created: 2026-08-10
summary: "Lens procedure for exploring open questions through structured Socratic questioning. Based on the Socratic method's six operations and dialectical reasoning. Used for research/exploration problems where the question is more important than the answer."
tags: [procedure, think, lens, systematic-inquiry, socratic, research, exploration]
allowed_tools:
  - vault_search
  - llm_generate
  - run_procedure
description: "Explore open questions through structured Socratic questioning. Use for research/exploration problems."
---

# Systematic-Inquiry Lens

## Research Basis

This lens implements the **Socratic method** — structured questioning that probes assumptions, clarifies meaning, and exposes gaps in understanding. Research shows this is the most effective approach for open-ended exploration problems where the goal is not to find a single answer but to map the question space.

The Socratic method operates through six types of questioning: clarification, probing assumptions, probing evidence/reasons, examining perspectives, exploring implications, and questioning the question itself [sources: [[critical-thinking-frameworks-and-methods-Socratic-method-dialectical-reasoning-f]]]. This systematic questioning maps the full space of an open question rather than jumping to a conclusion.

**Metacognitive monitoring** — the ability to track one's own understanding and identify gaps — is the cognitive foundation. Research shows that structured self-questioning protocols significantly improve reasoning quality compared to free-form thinking [sources: [[cognitive-psychology-metacognition-thinking-dispositions-need-for-cognition-refl]]]. This lens forces metacognitive monitoring by requiring explicit gap identification at each step.

## Connection to the Knowledge Triad

This lens is the **Epistemology** of the triad applied within a lens: it asks "how do we know what we know?" and "what don't we know?" — the fundamental epistemological questions.

## Hardening Strategy

Each step uses a **deterministic fallback** when the LLM output fails validation. The fallback provides a structurally correct but generic response that preserves the inquiry trajectory even when the model produces garbage. The cumulative context pattern ensures each step has access to all prior steps' outputs.

**Triple-try consistency** is applied on key LLM calls: the same prompt is run 3 times, and majority vote (by structural similarity) picks the most consistent output. This catches the small model's inconsistency without bespoke heuristics — just run it 3 times and pick the most common answer.

---

### Step 1: Clarify the question

State the question precisely and identify what kind of answer it seeks. Uses triple-try for consistency.

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

# Triple-try: run 3 times, pick most consistent
results = []
for i in range(3):
    r = llm_generate(prompt)
    if "QUESTION:" in r:
        results.append(r)

if len(results) >= 2:
    # Pick the result that appears most (or first if all different)
    from collections import Counter
    # Compare by the QUESTION line only (structural similarity)
    question_lines = [r.split("QUESTION:")[1].split("\n")[0].strip() if "QUESTION:" in r else "" for r in results]
    most_common = Counter(question_lines).most_common(1)[0]
    # Find the result with the most common question line
    for r in results:
        if "QUESTION:" in r and r.split("QUESTION:")[1].split("\n")[0].strip() == most_common[0]:
            result = r
            break
    else:
        result = results[0]
elif len(results) == 1:
    result = results[0]
else:
    # All 3 tries failed validation — deterministic fallback
    result = f"QUESTION: {problem}\nQUESTION_TYPE: exploratory\nSCOPE: full question as stated"

# Set result with cumulative context tag for downstream steps
result = f"STEP1_START\n{result}\nSTEP1_END"
print(result)
```

[validate: contains "QUESTION:"]
[validate: contains "QUESTION_TYPE:"]

### Step 2: Probe assumptions

What unstated assumptions does the question carry? Uses triple-try for consistency.

```python
# Parse Step 1's clarified question from output
step1_text = output
question = problem = args.get("problem", "")
for line in step1_text.split('\n'):
    if line.startswith('QUESTION: '):
        question = line.replace('QUESTION: ', '').strip()
        break

prompt = f"""Given this clarified question: {question}

What unstated assumptions does this question carry? What does it take for granted?

List 3-5 assumptions:
ASSUMPTION_1: [assumption] — [what if this is wrong?]
ASSUMPTION_2: [assumption] — [what if this is wrong?]
ASSUMPTION_3: [assumption] — [what if this is wrong?]
"""

# Triple-try
results = []
for i in range(3):
    r = llm_generate(prompt)
    if "ASSUMPTION_" in r:
        results.append(r)

if len(results) >= 2:
    # Pick result with most ASSUMPTION_ entries (most complete answer wins)
    result = max(results, key=lambda r: r.count("ASSUMPTION_"))
elif len(results) == 1:
    result = results[0]
else:
    result = f"ASSUMPTION_1: The question assumes the problem is well-defined — what if it's ambiguous?\nASSUMPTION_2: The question assumes a single answer exists — what if multiple valid answers exist?\nASSUMPTION_3: The question assumes current understanding is sufficient — what if we lack key context?"

# Carry forward cumulative context
result = f"{output}\nSTEP2_START\n{result}\nSTEP2_END"
print(result)
```

[validate: contains "ASSUMPTION_"]

### Step 3: Probe evidence and reasons — what evidence exists?

This is a bite-sized step: only identify EXISTING evidence. The next step handles missing evidence separately. Uses vault_search for provenance.

```python
# Parse question from accumulated context
question = args.get("problem", "")
for line in output.split('\n'):
    if line.startswith('QUESTION: '):
        question = line.replace('QUESTION: ', '').strip()
        break

# Search vault for evidence related to this question
vault_evidence = ""
try:
    search_results = vault_search(question, k=5)
    vault_evidence = "\n".join([f"- [[{r.get('title', 'unknown')}]]: {r.get('summary', '')[:100]}" for r in search_results[:5]])
except:
    vault_evidence = "- No vault results found"

prompt = f"""Question: {question}

Vault evidence found:
{vault_evidence}

What evidence EXISTS to answer this question? List evidence from the vault results above and from the problem context.

Format:
EXISTING_EVIDENCE:
- [evidence 1 with source if known]
- [evidence 2 with source if known]
- [evidence 3 if any]
"""

# Triple-try
results = []
for i in range(3):
    r = llm_generate(prompt)
    if "EXISTING_EVIDENCE:" in r:
        results.append(r)

if len(results) >= 2:
    result = max(results, key=lambda r: r.count("- "))
elif len(results) == 1:
    result = results[0]
else:
    result = f"EXISTING_EVIDENCE:\n{vault_evidence}"

result = f"{output}\nSTEP3A_START\n{result}\nSTEP3A_END"
print(result)
```

[validate: contains "EXISTING_EVIDENCE:"]

### Step 4: Probe evidence — what evidence is MISSING?

Bite-sized: only identify what's missing. This is the gap-assessment step.

```python
# Parse existing evidence from Step 3A
existing = ""
in_step3a = False
for line in output.split('\n'):
    if 'STEP3A_START' in line:
        in_step3a = True
    elif 'STEP3A_END' in line:
        in_step3a = False
    elif in_step3a:
        existing += line + '\n'

prompt = f"""Existing evidence identified:
{existing}

What evidence is MISSING to fully answer this question? What would you need to know that you don't?

Format:
MISSING_EVIDENCE:
- [missing evidence 1]
- [missing evidence 2]
- [missing evidence 3 if any]
"""

# Triple-try
results = []
for i in range(3):
    r = llm_generate(prompt)
    if "MISSING_EVIDENCE:" in r:
        results.append(r)

if len(results) >= 2:
    result = max(results, key=lambda r: r.count("- "))
elif len(results) == 1:
    result = results[0]
else:
    result = f"MISSING_EVIDENCE:\n- Empirical data directly addressing this question\n- Expert analysis from domain specialists\n- Comparative studies or benchmarks"

result = f"{output}\nSTEP3B_START\n{result}\nSTEP3B_END"
print(result)
```

[validate: contains "MISSING_EVIDENCE:"]

### Step 5: Examine alternative perspectives

What perspectives are not represented? Uses triple-try.

```python
# Parse question from accumulated context
question = args.get("problem", "")
for line in output.split('\n'):
    if line.startswith('QUESTION: '):
        question = line.replace('QUESTION: ', '').strip()
        break

prompt = f"""Question: {question}

What perspectives or viewpoints are NOT represented in the current analysis? Whose voice is missing?

Format:
PERSPECTIVE_1: [perspective] — [what would they say?]
PERSPECTIVE_2: [perspective] — [what would they say?]
PERSPECTIVE_3: [perspective] — [what would they say?]
"""

# Triple-try
results = []
for i in range(3):
    r = llm_generate(prompt)
    if "PERSPECTIVE_" in r:
        results.append(r)

if len(results) >= 2:
    result = max(results, key=lambda r: r.count("PERSPECTIVE_"))
elif len(results) == 1:
    result = results[0]
else:
    result = f"PERSPECTIVE_1: Skeptic — questions whether the question itself is well-formed\nPERSPECTIVE_2: Practitioner — focuses on practical implications and real-world constraints\nPERSPECTIVE_3: Theorist — looks for underlying principles and generalizable patterns"

result = f"{output}\nSTEP4_START\n{result}\nSTEP4_END"
print(result)
```

[validate: contains "PERSPECTIVE_"]

### Step 6: Explore implications

What follows from each possible answer? Uses triple-try.

```python
# Parse question from accumulated context
question = args.get("problem", "")
for line in output.split('\n'):
    if line.startswith('QUESTION: '):
        question = line.replace('QUESTION: ', '').strip()
        break

prompt = f"""Question: {question}

For each possible answer to this question, what are the implications?

Format:
IF_ANSWER_A: [answer] -> [implication 1], [implication 2]
IF_ANSWER_B: [answer] -> [implication 1], [implication 2]
IF_ANSWER_C: [answer] -> [implication 1], [implication 2]
"""

# Triple-try
results = []
for i in range(3):
    r = llm_generate(prompt)
    if "IF_ANSWER_" in r:
        results.append(r)

if len(results) >= 2:
    result = max(results, key=lambda r: r.count("IF_ANSWER_"))
elif len(results) == 1:
    result = results[0]
else:
    result = f"IF_ANSWER_A: Yes -> action required, resources needed\nIF_ANSWER_B: No -> current approach is adequate, monitor for changes\nIF_ANSWER_C: Partially -> nuanced response needed, further investigation required"

result = f"{output}\nSTEP5_START\n{result}\nSTEP5_END"
print(result)
```

[validate: contains "IF_ANSWER_"]

### Step 7: Question the question

Meta-question: is this even the right question? Uses triple-try.

```python
# Parse question from accumulated context
question = args.get("problem", "")
for line in output.split('\n'):
    if line.startswith('QUESTION: '):
        question = line.replace('QUESTION: ', '').strip()
        break

prompt = f"""Original question: {question}

Step back. Is this the RIGHT question to ask? Or is there a deeper question that this one points toward?

Format:
META_QUESTION: [the deeper question, if any]
WHY_DEEPER: [why this is more fundamental than the original]
REFRAMED: [how should the original question be reframed?]
"""

# Triple-try
results = []
for i in range(3):
    r = llm_generate(prompt)
    if "META_QUESTION:" in r:
        results.append(r)

if len(results) >= 2:
    # Pick the one with the most structural completeness
    result = max(results, key=lambda r: sum(1 for tag in ["META_QUESTION:", "WHY_DEEPER:", "REFRAMED:"] if tag in r))
elif len(results) == 1:
    result = results[0]
else:
    result = f"META_QUESTION: What underlying need prompted this question?\nWHY_DEEPER: Understanding the motivation reveals whether the surface question is the right one to ask\nREFRAMED: Consider what goal the question serves and whether a different question would better serve that goal"

result = f"{output}\nSTEP6_START\n{result}\nSTEP6_END"
print(result)
```

[validate: contains "META_QUESTION:"]
[validate: contains "REFRAMED:"]

### Step 8: Synthesize inquiry results

```python
# Parse all accumulated context from output
question = args.get("problem", "")
for line in output.split('\n'):
    if line.startswith('QUESTION: '):
        question = line.replace('QUESTION: ', '').strip()
        break

# Extract each step's content using the STEP markers
steps = {}
current_step = None
for line in output.split('\n'):
    for tag, step_num in [("STEP1", 1), ("STEP2", 2), ("STEP3A", 3), ("STEP3B", 3), ("STEP4", 4), ("STEP5", 5), ("STEP6", 6)]:
        if f'{tag}_START' in line:
            current_step = step_num
            steps[current_step] = ""
            break
        elif f'{tag}_END' in line:
            current_step = None
            break
    if current_step and f'STEP' not in line:
        steps[current_step] = steps.get(current_step, "") + line + '\n'

assumptions = steps.get(2, "not available")
evidence = steps.get(3, "not available")
perspectives = steps.get(4, "not available")
implications = steps.get(5, "not available")
meta = steps.get(6, "not available")

prompt = f"""Synthesize the Socratic inquiry into this question: {question}

Assumptions probed: {assumptions}
Evidence assessed: {evidence}
Perspectives examined: {perspectives}
Implications explored: {implications}
Meta-question: {meta}

Produce a structured inquiry report:

INQUIRY_SUMMARY: [2-3 sentences summarizing what the inquiry revealed]
KEY_FINDING: [the most important insight from this inquiry]
KNOWLEDGE_GAPS: [what remains unknown and should be researched next]
REFRAMED_QUESTION: [the question we SHOULD be asking, if different from the original]
NEXT_STEPS: [concrete next steps to fill the knowledge gaps]
"""

# Triple-try
results = []
for i in range(3):
    r = llm_generate(prompt)
    if "INQUIRY_SUMMARY:" in r:
        results.append(r)

if len(results) >= 2:
    result = max(results, key=lambda r: sum(1 for tag in ["INQUIRY_SUMMARY:", "KEY_FINDING:", "KNOWLEDGE_GAPS:", "REFRAMED_QUESTION:", "NEXT_STEPS:"] if tag in r))
elif len(results) == 1:
    result = results[0]
else:
    result = f"""INQUIRY_SUMMARY: The inquiry explored the question from multiple angles, identifying key assumptions and evidence gaps.
KEY_FINDING: The question requires further research to answer definitively; current evidence is insufficient.
KNOWLEDGE_GAPS: {steps.get(3, 'Evidence assessment not available')}
REFRAMED_QUESTION: {steps.get(6, 'Meta-question not available')}
NEXT_STEPS: Research the missing evidence identified in Step 4; consult the perspectives identified in Step 5."""

result = f"{output}\nSTEP7_START\n{result}\nSTEP7_END"
print(result)
```

[validate: contains "INQUIRY_SUMMARY:"]
[validate: contains "KNOWLEDGE_GAPS:"]
[validate: contains "NEXT_STEPS:"]