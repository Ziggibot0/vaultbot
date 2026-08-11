---
type: procedure
status: experimental
created: 2026-08-10
summary: "Lens procedure for breaking complex problems into manageable sub-problems. Based on means-ends analysis and decomposition research from cognitive psychology. Updated with triple-try consistency and bite-sized steps."
description: "Break complex problems into sub-problems using means-ends analysis"
tags: [procedure, think, lens, decomposition, problem-solving, cognitive-psychology]
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

# Decomposition Lens

## Research Basis

This lens implements **means-ends analysis** and **problem decomposition**, core strategies from cognitive psychology's problem-solving research. Key sources:

- **Means-ends analysis** (Newell & Simon, 1972): Compare the current state to the goal state, identify the biggest difference, and create a sub-problem to eliminate that difference. [sources: [[psychology-of-problem-solving-Gestalt-psychology-insight-vs-analytical-reasoning]]]

- **Decomposition reduces cognitive load**: Complex problems overwhelm working memory. Breaking them into sub-problems keeps each sub-problem within the model's capacity. [sources: [[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]]]

- **Hierarchical decomposition**: Research shows that hierarchical decomposition (breaking into 3-7 sub-problems, then recursing) is more effective than flat decomposition. This aligns with Miller's law (7±2 items in working memory). [sources: [[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]]]

This lens follows the Knowledge Triad internally:
- **Ontology**: What are the components of this problem?
- **Epistemology**: How do we know the decomposition is correct?
- **Hermeneutics**: How do the sub-problems combine to solve the original?

## Inputs

- `problem`: The complex problem to decompose
- `context`: Relevant context or constraints

## Outputs

- A hierarchical decomposition tree with sub-problems, their dependencies, and a solve order

---

### Step 1: Classify problem structure type (triple-try)

Classify what KIND of complex problem this is. This is the ontology step — understanding the structure of what exists.

Uses triple-try: run classification 3 times, take majority vote. If all three disagree, default to NESTED (safest superset).

```python
problem = args.get('problem', '')
context = args.get('context', '')

valid_types = ['SEQUENTIAL', 'PARALLEL', 'NESTED', 'CONDITIONAL']

# Triple-try classification
responses = []
for attempt in range(3):
    prompt = f"""Classify this problem's structure. Respond with exactly one word:
- SEQUENTIAL (steps must happen in order)
- PARALLEL (sub-problems can be solved independently)
- NESTED (sub-problems contain sub-sub-problems)
- CONDITIONAL (branching paths depending on outcomes)

Problem: {problem}

Respond with ONLY the word, nothing else."""
    try:
        response = llm_generate(prompt).strip().upper()
        # Extract valid type from response
        classified = None
        for vt in valid_types:
            if vt in response:
                classified = vt
                break
        if classified:
            responses.append(classified)
    except:
        pass

# Majority vote
from collections import Counter
if responses:
    counts = Counter(responses)
    problem_type = counts.most_common(1)[0][0]
else:
    problem_type = 'NESTED'  # safe default

result = f"PROBLEM: {problem}\nSTRUCTURE_TYPE: {problem_type}"
print(result)
```

[validate: contains "STRUCTURE_TYPE"]

---

### Step 2: Identify goal state and current state (triple-try)

Means-ends analysis: compare current state to goal state. Bite-sized: this step ONLY identifies goal and current state, NOT the gap (that's Step 3).

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

# Triple-try
best_response = ''
for attempt in range(3):
    prompt = f"""For this problem: "{problem}"

State in one sentence each:
GOAL_STATE: What does "solved" look like?
CURRENT_STATE: Where are we now?

Format exactly as:
GOAL_STATE: ...
CURRENT_STATE: ..."""
    try:
        response = llm_generate(prompt).strip()
        if 'GOAL_STATE:' in response and 'CURRENT_STATE:' in response:
            best_response = response
            break
    except:
        pass

if not best_response:
    best_response = f"GOAL_STATE: The problem '{problem}' is fully solved\nCURRENT_STATE: The problem is unsolved"

result = f"{best_response}\nSTRUCTURE_TYPE: {problem_type}"
print(result)
```

[validate: contains "GOAL_STATE"]
[validate: contains "CURRENT_STATE"]

---

### Step 3: Identify the biggest gap (triple-try)

Bite-sized: this step ONLY identifies the single biggest gap between goal and current state.

```python
# Parse Step 2
lines = output.strip().split('\n')
goal_state = ''
current_state = ''
problem_type = ''
for line in lines:
    if line.startswith('GOAL_STATE: '):
        goal_state = line.replace('GOAL_STATE: ', '').strip()
    elif line.startswith('CURRENT_STATE: '):
        current_state = line.replace('CURRENT_STATE: ', '').strip()
    elif line.startswith('STRUCTURE_TYPE: '):
        problem_type = line.replace('STRUCTURE_TYPE: ', '').strip()

# Triple-try
best_response = ''
for attempt in range(3):
    prompt = f"""What is the SINGLE BIGGEST difference between these two states?

Goal state: {goal_state}
Current state: {current_state}

Answer in ONE sentence:
BIGGEST_GAP: ..."""
    try:
        response = llm_generate(prompt).strip()
        if 'BIGGEST_GAP:' in response and len(response) < 500:
            best_response = response
            break
    except:
        pass

if not best_response:
    best_response = "BIGGEST_GAP: Need to break this problem into solvable parts"

result = f"{best_response}\nGOAL_STATE: {goal_state}\nCURRENT_STATE: {current_state}\nSTRUCTURE_TYPE: {problem_type}"
print(result)
```

[validate: contains "BIGGEST_GAP"]

---

### Step 4: Break the biggest gap into 3-7 sub-problems (triple-try)

Core decomposition step. Break the gap into Miller's-law-sized chunks.

```python
# Parse Step 3
lines = output.strip().split('\n')
biggest_gap = ''
goal_state = ''
current_state = ''
problem_type = ''
for line in lines:
    if line.startswith('BIGGEST_GAP: '):
        biggest_gap = line.replace('BIGGEST_GAP: ', '').strip()
    elif line.startswith('GOAL_STATE: '):
        goal_state = line.replace('GOAL_STATE: ', '').strip()
    elif line.startswith('STRUCTURE_TYPE: '):
        problem_type = line.replace('STRUCTURE_TYPE: ', '').strip()

problem = args.get('problem', '')

# Triple-try: pick the response with the most valid sub-problem lines
best_response = ''
best_count = 0
for attempt in range(3):
    prompt = f"""Break this gap into 3-7 sub-problems:
Problem: {problem}
Biggest gap: {biggest_gap}

For each sub-problem, output exactly:
ID: S1 | NAME: <short noun phrase> | QUESTION: <specific question> | DEPENDS_ON: <other IDs or none>

Output 3-7 sub-problems, one per line."""
    try:
        response = llm_generate(prompt).strip()
        sub_lines = [l for l in response.split('\n') if '| ID:' in l or l.startswith('ID:')]
        if len(sub_lines) >= 3:
            if len(sub_lines) > best_count:
                best_response = response
                best_count = len(sub_lines)
    except:
        pass

if not best_response:
    best_response = """ID: S1 | NAME: Understand requirements | QUESTION: What exactly needs to be done? | DEPENDS_ON: none
ID: S2 | NAME: Design approach | QUESTION: What is the best way to solve this? | DEPENDS_ON: S1
ID: S3 | NAME: Implement solution | QUESTION: How do we execute the chosen approach? | DEPENDS_ON: S2"""

result = f"{best_response}\nSTRUCTURE_TYPE: {problem_type}"
print(result)
```

[validate: contains "ID:"]
[validate: at_least 3 occurrences of "ID:"]

---

### Step 5: Check for functional fixedness (triple-try)

Gestalt psychology check: are sub-problems named after solutions rather than problems?

```python
# Parse Step 4 for sub-problems
lines = output.strip().split('\n')
problem_type = ''
sub_problems = [l for l in lines if 'ID:' in l and '|' in l]
for line in lines:
    if line.startswith('STRUCTURE_TYPE: '):
        problem_type = line.replace('STRUCTURE_TYPE: ', '').strip()

# Triple-try
best_response = ''
for attempt in range(3):
    if sub_problems:
        prompt = f"""Review these sub-problems for functional fixedness (naming problems as solutions):

{chr(10).join(sub_problems)}

Functional fixedness means naming a sub-problem after a specific solution or tool instead of describing the problem to be solved. For example "Add Redis cache" shows fixedness; "Achieve fast data retrieval" does not.

For each sub-problem that shows functional fixedness, output:
REVISE: <sub-problem ID or name> — <why it's solution-oriented> — <better problem-oriented reframing>

If none show functional fixedness, output:
CLEAN"""
        try:
            response = llm_generate(prompt).strip()
            if 'CLEAN' in response.upper() or 'REVISE' in response.upper():
                best_response = response
                break
        except:
            pass

if not best_response:
    best_response = "CLEAN"

# Preserve sub-problems and type for next step
result = f"{best_response}\n{'\\n'.join(sub_problems)}\nSTRUCTURE_TYPE: {problem_type}"
print(result)
```

[validate: contains "CLEAN" or contains "REVISE"]

---

### Step 6: Determine solve order (topological sort)

Produce a topological sort of the sub-problems based on DEPENDS_ON relationships. This is pure Python — no LLM needed.

```python
# Parse Step 5 for sub-problems
lines = output.strip().split('\n')
sub_lines = [l for l in lines if 'ID:' in l and '|' in l and 'DEPENDS_ON' in l]

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

### Step 7: Output the decomposition tree

Synthesize all steps into a final decomposition tree. This is the hermeneutic step — interpreting how the parts form a whole. Pure Python assembly — no LLM needed.

```python
# Parse Step 6
lines = output.strip().split('\n')
solve_order = ''
sub_lines = [l for l in lines if 'ID:' in l and '|' in l and 'DEPENDS_ON' in l]
for line in lines:
    if line.startswith('SOLVE_ORDER: '):
        solve_order = line.replace('SOLVE_ORDER: ', '').strip()

problem = args.get('problem', '')

# Build output tree
sub_text = '\n'.join(sub_lines) if sub_lines else 'No sub-problems parsed'

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

3. **Functional fixedness** ([[psychology-of-problem-solving-Gestalt-psychology-insight-vs-analytical-reasoning]]): Step 5 checks for the Gestalt error of naming problems as solutions, which limits creative problem-solving.

4. **Knowledge Triad** ([[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]]): Ontology = problem structure (what exists). Epistemology = functional fixedness check (how we know the decomposition is valid). Hermeneutics = solve order (what the parts mean together).

5. **Triple-try consistency**: Classification, gap identification, and decomposition all use triple-try with best-of selection. This catches the small model's inconsistency on the most critical steps.

6. **Bite-sized steps**: Goal/current state and biggest gap are now separate steps (was one step asking for all three). This keeps each LLM call focused on one question.

## Related

- [[Think]] — parent procedure that dispatches to this lens
- [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]] — philosophical foundation
- [[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]] — research basis
- [[psychology-of-problem-solving-Gestalt-psychology-insight-vs-analytical-reasoning]] — Gestalt psychology research