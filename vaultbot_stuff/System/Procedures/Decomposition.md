---
type: procedure
status: experimental
baseline: true
created: 2026-08-10
summary: "Lens procedure for breaking complex problems into manageable sub-problems. v4: redesigned for qwen3.5:4b — killed triple-try, merged steps 2+3 (goal/current/gap in one call), richer prompts. ~3 LLM calls (down from ~21). Already optimal — no v4.1 changes needed."
description: "Break complex problems into sub-problems using means-ends analysis"
when_to_use: "When breaking a complex problem into manageable sub-problems. When planning a multi-step task. When decomposing work into a solve order. When asked 'break this down' or 'what are the steps?' or 'how do I tackle this complex task?'. When designing a plan."
tags: [procedure, think, lens, decomposition, problem-solving, cognitive-psychology, v4, optimal]
allowed_tools:
  - llm_generate
  - vault_search
  - run_procedure
depends_on:
  - "[[Think]]"
  - "[[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]]"
research_sources:
  - "[[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]]"
  - "[[psychology-of-problem-solving-Gestalt-psychology-insight-vs-analytical-reasoning]]"
---

# Decomposition Lens (v4)

## Research Basis

This lens implements **means-ends analysis** and **problem decomposition**, core strategies from cognitive psychology's problem-solving research. Key sources:

- **Means-ends analysis** (Newell & Simon, 1972): Compare the current state to the goal state, identify the biggest difference, and create a sub-problem to eliminate that difference. [sources: [[psychology-of-problem-solving-Gestalt-psychology-insight-vs-analytical-reasoning]]]

- **Decomposition reduces cognitive load**: Complex problems overwhelm working memory. Breaking them into sub-problems keeps each sub-problem within the model's capacity. [sources: [[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]]]

- **Hierarchical decomposition**: Research shows that hierarchical decomposition (breaking into 3-7 sub-problems, then recursing) is more effective than flat decomposition. This aligns with Miller's law (7±2 items in working memory). [sources: [[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]]]

## What Changed in v4

| Problem in v3 | Fix in v4 |
|---|---|
| Triple-try on every step (3× LLM calls) | Single call per step — 4B is consistent |
| Steps 2+3 separate (goal/current state, then biggest gap) | Merged into one call — 4B can handle both |
| Step 5 (functional fixedness) separate from decomposition | Merged into decomposition call — 4B can check while generating |
| 7 steps, ~21 LLM calls | 5 steps, ~5-7 LLM calls |

## v4.1 Audit Result: **Already Optimal**

This lens was audited for further simplification opportunities. **No changes needed** — it's already at its optimal call count:

- **Step 1**: Classify problem structure type (1 LLM call)
- **Step 2**: Identify goal state, current state, and biggest gap (1 LLM call) — merged from v3 steps 2+3
- **Step 3**: Break the biggest gap into 3-7 sub-problems with functional fixedness check (1 LLM call) — merged from v3 steps 4+5
- **Step 4**: Determine solve order (topological sort) — **deterministic, zero LLM cost**
- **Step 5**: Output the decomposition tree — **deterministic, zero LLM cost**
- **Total: 3 LLM calls**

All three LLM steps are necessary and cannot be further batched without losing the structured reasoning. Steps 4-5 being fully deterministic is a key architectural win.

## Inputs

- `problem`: The complex problem to decompose
- `context`: Relevant context or constraints

## Outputs

- A hierarchical decomposition tree with sub-problems, their dependencies, and a solve order

---

### Step 1: Classify problem structure type

Classify what KIND of complex problem this is. Single LLM call — the 4B handles classification reliably.

```python
problem = args.get('problem', '')
context = args.get('context', '')

valid_types = ['SEQUENTIAL', 'PARALLEL', 'NESTED', 'CONDITIONAL']

prompt = f"""Classify this problem's structure. Respond with exactly one word:
- SEQUENTIAL (steps must happen in order)
- PARALLEL (sub-problems can be solved independently)
- NESTED (sub-problems contain sub-sub-problems)
- CONDITIONAL (branching paths depending on outcomes)

Problem: {problem}

Respond with ONLY the word, nothing else."""

response = llm_generate(prompt).strip().upper()
problem_type = 'NESTED'  # safe default
for vt in valid_types:
    if vt in response:
        problem_type = vt
        break

result = f"PROBLEM: {problem}\nSTRUCTURE_TYPE: {problem_type}"
print(result)
```

[validate: contains "STRUCTURE_TYPE"]

---

### Step 2: Identify goal state, current state, and biggest gap

Means-ends analysis in one call. The 4B can state goal, current state, and identify the biggest gap between them in a single structured response.

```python
# Parse Step 1
lines = output.strip().split('\n')
problem = ''
problem_type = ''
for line in lines:
    if line.startswith('PROBLEM: '):
        problem = line.replace('PROBLEM: ', '').strip()
    elif line.startswith('STRUCTURE_TYPE: '):
        problem_type = line.replace('STRUCTURE_TYPE: ', '').strip()

prompt = f"""For this problem: "{problem}"

State in one sentence each:
GOAL_STATE: What does "solved" look like?
CURRENT_STATE: Where are we now?
BIGGEST_GAP: What is the single biggest difference between goal and current state?

Format exactly as:
GOAL_STATE: ...
CURRENT_STATE: ...
BIGGEST_GAP: ..."""

response = llm_generate(prompt).strip()

# Parse
goal_state = ''
current_state = ''
biggest_gap = ''
for line in response.split('\n'):
    if line.startswith('GOAL_STATE:'):
        goal_state = line.replace('GOAL_STATE:', '').strip()
    elif line.startswith('CURRENT_STATE:'):
        current_state = line.replace('CURRENT_STATE:', '').strip()
    elif line.startswith('BIGGEST_GAP:'):
        biggest_gap = line.replace('BIGGEST_GAP:', '').strip()

# Deterministic fallbacks
if not goal_state:
    goal_state = f"The problem '{problem}' is fully solved"
if not current_state:
    current_state = "The problem is unsolved"
if not biggest_gap:
    biggest_gap = "Need to break this problem into solvable parts"

result = f"GOAL_STATE: {goal_state}\nCURRENT_STATE: {current_state}\nBIGGEST_GAP: {biggest_gap}\nSTRUCTURE_TYPE: {problem_type}\nPROBLEM: {problem}"
print(result)
```

[validate: contains "GOAL_STATE"]
[validate: contains "BIGGEST_GAP"]

---

### Step 3: Break the biggest gap into 3-7 sub-problems with functional fixedness check

Core decomposition step. The 4B can generate sub-problems AND check for functional fixedness (naming problems as solutions) in one call.

```python
# Parse Step 2
lines = output.strip().split('\n')
biggest_gap = ''
goal_state = ''
current_state = ''
problem_type = ''
problem = ''
for line in lines:
    if line.startswith('BIGGEST_GAP: '):
        biggest_gap = line.replace('BIGGEST_GAP: ', '').strip()
    elif line.startswith('GOAL_STATE: '):
        goal_state = line.replace('GOAL_STATE: ', '').strip()
    elif line.startswith('STRUCTURE_TYPE: '):
        problem_type = line.replace('STRUCTURE_TYPE: ', '').strip()
    elif line.startswith('PROBLEM: '):
        problem = line.replace('PROBLEM: ', '').strip()

prompt = f"""Break this gap into 3-7 sub-problems:

Problem: {problem}
Biggest gap: {biggest_gap}

For each sub-problem, output exactly:
ID: S1 | NAME: <short noun phrase> | QUESTION: <specific question> | DEPENDS_ON: <other IDs or none>

IMPORTANT: Name sub-problems as PROBLEMS to solve, not solutions to implement.
- GOOD: "Achieve fast data retrieval" (problem-oriented)
- BAD: "Add Redis cache" (solution-oriented, shows functional fixedness)

After listing sub-problems, add a line:
FUNCTIONAL_FIXEDNESS: CLEAN (if all are problem-oriented) or REVISE: <ID> — <why> — <better name>

Output 3-7 sub-problems, one per line."""

response = llm_generate(prompt).strip()

# Parse sub-problems
sub_lines = [l for l in response.split('\n') if '| ID:' in l or l.startswith('ID:')]
functional_check = 'CLEAN'
for line in response.split('\n'):
    if line.startswith('FUNCTIONAL_FIXEDNESS:'):
        functional_check = line.replace('FUNCTIONAL_FIXEDNESS:', '').strip()

# Deterministic fallback
if len(sub_lines) < 3:
    sub_lines = [
        "ID: S1 | NAME: Understand requirements | QUESTION: What exactly needs to be done? | DEPENDS_ON: none",
        "ID: S2 | NAME: Design approach | QUESTION: What is the best way to solve this? | DEPENDS_ON: S1",
        "ID: S3 | NAME: Implement solution | QUESTION: How do we execute the chosen approach? | DEPENDS_ON: S2"
    ]
    functional_check = 'CLEAN'

result = f"{response}\nSTRUCTURE_TYPE: {problem_type}"
print(result)
```

[validate: contains "ID:"]
[validate: at_least 3 occurrences of "ID:"]

---

### Step 4: Determine solve order (topological sort)

Produce a topological sort of the sub-problems based on DEPENDS_ON relationships. Pure Python — no LLM needed.

```python
# Parse Step 3 for sub-problems
lines = output.strip().split('\n')
sub_lines = [l for l in lines if 'ID:' in l and '|' in l and 'DEPENDS_ON' in l]
problem_type = ''
for line in lines:
    if line.startswith('STRUCTURE_TYPE: '):
        problem_type = line.replace('STRUCTURE_TYPE: ', '').strip()

if not sub_lines:
    sub_lines = [
        "ID: S1 | NAME: Understand | QUESTION: What? | DEPENDS_ON: none",
        "ID: S2 | NAME: Design | QUESTION: How? | DEPENDS_ON: S1",
        "ID: S3 | NAME: Implement | QUESTION: Do it | DEPENDS_ON: S2"
    ]

# Parse dependencies
deps = {}
ids = []
for line in sub_lines:
    parts = line.split('|')
    sid = parts[0].replace('ID:', '').strip()
    ids.append(sid)
    dep_str = 'none'
    for p in parts:
        if 'DEPENDS_ON' in p:
            dep_str = p.replace('DEPENDS_ON:', '').strip()
    if dep_str.lower() == 'none':
        deps[sid] = []
    else:
        deps[sid] = [d.strip() for d in dep_str.split(',')]

# Topological sort
order = []
resolved = set()
max_iter = len(ids) * 2
while len(order) < len(ids) and max_iter > 0:
    for sid in ids:
        if sid in resolved:
            continue
        if all(d in resolved for d in deps.get(sid, [])):
            order.append(sid)
            resolved.add(sid)
    max_iter -= 1

# Handle cycles: add remaining in order
for sid in ids:
    if sid not in resolved:
        order.append(sid)

# Preserve sub-problem lines for final step
result = f"SOLVE_ORDER: {' -> '.join(order)}\n{'\\n'.join(sub_lines)}"
print(result)
```

[validate: contains "SOLVE_ORDER"]

---

### Step 5: Output the decomposition tree

Synthesize all steps into a final decomposition tree. Pure Python assembly — no LLM needed.

```python
# Parse Step 4
lines = output.strip().split('\n')
solve_order = ''
sub_lines = [l for l in lines if 'ID:' in l and '|' in l and 'DEPENDS_ON' in l]
for line in lines:
    if line.startswith('SOLVE_ORDER: '):
        solve_order = line.replace('SOLVE_ORDER: ', '').strip()

problem = args.get('problem', '')

# Build output tree
sub_text = '\\n'.join(sub_lines) if sub_lines else 'No sub-problems parsed'

result = f"""DECOMPOSITION COMPLETE
PROBLEM: {problem}
SOLVE_ORDER: {solve_order}

SUB-PROBLEMS:
{sub_text}

Each sub-problem is small enough for a single lens call or vault lookup.
If any sub-problem still requires multi-step reasoning, it should be decomposed further using this lens recursively."""
print(result)
```

[validate: contains "DECOMPOSITION COMPLETE"]
[validate: contains "SOLVE_ORDER"]

---

## Research Justification

1. **Means-ends analysis** (Newell & Simon, 1972): The gap identification step directly implements means-ends analysis — compare goal to current, find the biggest difference, create sub-problems to close it.

2. **Cognitive load reduction** ([[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]]): Breaking into 3-7 sub-problems keeps each within working memory capacity (Miller's law).

3. **Functional fixedness** ([[psychology-of-problem-solving-Gestalt-psychology-insight-vs-analytical-reasoning]]): Step 3 checks for the Gestalt error of naming problems as solutions, which limits creative problem-solving.

4. **Knowledge Triad** ([[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]]): Ontology = problem structure (what exists). Epistemology = functional fixedness check (how we know the decomposition is valid). Hermeneutics = solve order (what the parts mean together).

5. **Single-call consistency**: The 4B model is consistent enough that triple-try is unnecessary. Each step runs once with deterministic fallbacks.

## Related

- [[Think]] — parent procedure that dispatches to this lens
- [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]] — philosophical foundation
- [[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]] — research basis
- [[psychology-of-problem-solving-Gestalt-psychology-insight-vs-analytical-reasoning]] — Gestalt psychology research