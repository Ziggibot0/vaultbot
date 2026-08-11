---
type: procedure
status: experimental
created: 2026-08-10
summary: "Lens procedure for evaluating claims by weighing evidence for and against. Based on epistemological justification theory and Bayesian reasoning. Called by Think when problem type is factual/empirical. Uses triple-try consistency and bite-sized steps for small model reliability."
tags: [procedure, thinking, lens, evidence, epistemology, reasoning, v2]
allowed_tools:
  - vault_search
  - llm_generate
  - run_procedure
description: "Weigh evidence for and against a claim using structured justification analysis with triple-try consistency"
---

# Evidence-Weighing Lens (v2)

**Part of the [[Think]] procedure system.** Called when the problem is a factual or empirical claim that needs verification.

**Research basis:** Epistemological justification theory (foundationalism vs coherentism) from [[epistemology-theory-of-knowledge-how-knowledge-is-justified-and-validated-especi]]; dual-process theory from [[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]] (System 2 deliberate evaluation overrides System 1 intuitive acceptance); metacognitive self-questioning from [[cognitive-psychology-metacognition-thinking-dispositions-need-for-cognition-refl]].

**Knowledge Triad mapping:**
- **Ontology**: What is the claim? What entities does it involve?
- **Epistemology**: What evidence supports it? What evidence contradicts it?
- **Hermeneutics**: What should we conclude given the weight of evidence?

**Design principle:** Classification and negation are *semantic* tasks — the LLM handles them. Format validation and evidence counting are *structural* tasks — deterministic code handles them. No keyword regex for understanding meaning.

**v2 changes:** Triple-try consistency on all LLM calls. Step 4 (formerly one big LLM call asking for score + points + verdict) broken into three bite-sized steps that a small model can actually succeed at.

---

### Step 1: State the claim and classify its type (triple-try)

Classify the claim type using a bounded LLM call (multiple choice). Run **three times** and use majority vote — if the small model gives a different answer on one try, the majority catches it.

```python
claim = args.get('claim', args.get('problem', ''))

prompt = f"""Classify this claim into exactly one type:

- definitional: defines what something is
- causal: claims X causes/creates/produces Y
- normative: claims something should/ought to be done
- predictive: claims something will happen or is likely
- comparative: claims something is better/worse/best/optimal

Claim: "{claim}"

Respond with ONLY the type name, nothing else."""

valid_types = ['definitional', 'causal', 'normative', 'predictive', 'comparative']

# Triple-try classification
responses = []
for i in range(3):
    resp = llm_generate(prompt).strip().lower()
    responses.append(resp)

# Parse each response to a valid type
parsed = []
for resp in responses:
    if resp in valid_types:
        parsed.append(resp)
    else:
        found = [t for t in valid_types if t in resp]
        parsed.append(found[0] if found else 'unknown')

# Majority vote
from collections import Counter
vote = Counter(parsed)
claim_type, count = vote.most_common(1)[0]

if count >= 2:
    method = 'triple-try-majority'
else:
    # All three diverged — use first, flag low confidence
    claim_type = parsed[0] if parsed[0] != 'unknown' else 'unknown'
    method = 'triple-try-divergent'

result = f"CLAIM: {claim}\nCLAIM_TYPE: {claim_type}\nCLASSIFICATION_METHOD: {method}"
print(result)
```

[validate: contains "CLAIM:"]
[validate: contains "CLAIM_TYPE:"]

---

### Step 2: Gather supporting evidence from vault

Search the vault for notes relevant to the claim. This is deterministic — vault_search is a structural operation. No LLM needed.

```python
claim = args.get('claim', args.get('problem', ''))
results = vault_search(claim, k=5)
supporting = []
for r in results:
    supporting.append({
        'title': r.get('title', r.get('file_path', '')),
        'relevance': r.get('score', 0),
        'snippet': r.get('snippet', '')[:200]
    })
result = f"SUPPORTING_EVIDENCE: {len(supporting)} notes found\n"
for s in supporting:
    result += f"  - {s['title']}: {s['snippet']}\n"
print(result)
```

[validate: contains "SUPPORTING_EVIDENCE:"]

---

### Step 3: Gather contradicting evidence (triple-try negation)

Generate the semantic opposite of the claim using a bounded LLM call, then search for that. Triple-try on the negation: run 3 times, pick the shortest non-garbage response (negation should be concise). If all three are garbage, use deterministic fallback.

```python
claim = args.get('claim', args.get('problem', ''))

negation_prompt = f"""Given this claim: "{claim}"

Write the OPPOSITE claim — the strongest version that contradicts it. Keep it to one sentence.

Respond with ONLY the negated claim, nothing else."""

# Triple-try negation
negations = []
for i in range(3):
    neg = llm_generate(negation_prompt).strip()
    negations.append(neg)

# Pick the best negation: shortest valid one (5-200 chars = reasonable)
valid_negations = [n for n in negations if 5 <= len(n) <= 200]
if valid_negations:
    negated = min(valid_negations, key=len)  # shortest = most concise
    method = 'triple-try-best'
else:
    # All garbage — deterministic fallback
    negated = f"It is not the case that: {claim}"
    method = 'triple-try-fallback'

results = vault_search(negated, k=5)
contradicting = []
for r in results:
    contradicting.append({
        'title': r.get('title', r.get('file_path', '')),
        'relevance': r.get('score', 0),
        'snippet': r.get('snippet', '')[:200]
    })
result = f"CONTRADICTING_EVIDENCE: {len(contradicting)} notes found\n"
result += f"NEGATION_USED: {negated}\n"
result += f"NEGATION_METHOD: {method}\n"
for c in contradicting:
    result += f"  - {c['title']}: {c['snippet']}\n"
print(result)
```

[validate: contains "CONTRADICTING_EVIDENCE:"]

---

### Step 4: Extract supporting points (triple-try, bite-sized)

**Bite-sized step:** Only ask the small model to do ONE thing — list the supporting points from the evidence. Don't ask for score, verdict, or contradicting points in the same call. Triple-try for consistency.

```python
claim = args.get('claim', args.get('problem', ''))

# Parse supporting evidence from Step 2 output
supporting_text = ""
for line in output.strip().split('\n'):
    if line.startswith("SUPPORTING_EVIDENCE:") or line.startswith("  - "):
        supporting_text += line + "\n"

prompt = f"""Given the claim "{claim}" and the supporting evidence from vault notes:

{supporting_text}

List the 1-3 strongest points that SUPPORT this claim. Use ONLY the evidence provided — do not use your own knowledge.

Format:
SUPPORTING_POINTS:
- [point 1]
- [point 2]
- [point 3 if available]

If no supporting evidence was found, respond with:
SUPPORTING_POINTS:
- (no supporting evidence found)"""

# Triple-try
responses = []
for i in range(3):
    resp = llm_generate(prompt).strip()
    responses.append(resp)

# Pick the response with the most bullet points (most complete extraction)
def count_bullets(text):
    return text.count('\n- ')

best = max(responses, key=count_bullets) if responses else ""
if "SUPPORTING_POINTS:" not in best:
    best = "SUPPORTING_POINTS:\n- (extraction failed)"

print(best)
```

[validate: contains "SUPPORTING_POINTS:"]

---

### Step 5: Extract contradicting points (triple-try, bite-sized)

**Bite-sized step:** Only ask for contradicting points. Same pattern as Step 4 but for the negation evidence from Step 3.

```python
claim = args.get('claim', args.get('problem', ''))

# Parse contradicting evidence from Step 3 output
contradicting_text = ""
for line in output.strip().split('\n'):
    if line.startswith("CONTRADICTING_EVIDENCE:") or line.startswith("NEGATION_USED:") or line.startswith("  - "):
        contradicting_text += line + "\n"

prompt = f"""Given the claim "{claim}" and the contradicting evidence from vault notes:

{contradicting_text}

List the 1-3 strongest points that CONTRADICT this claim. Use ONLY the evidence provided — do not use your own knowledge.

Format:
CONTRADICTING_POINTS:
- [point 1]
- [point 2]
- [point 3 if available]

If no contradicting evidence was found, respond with:
CONTRADICTING_POINTS:
- (no contradicting evidence found)"""

# Triple-try
responses = []
for i in range(3):
    resp = llm_generate(prompt).strip()
    responses.append(resp)

# Pick the response with the most bullet points
def count_bullets(text):
    return text.count('\n- ')

best = max(responses, key=count_bullets) if responses else ""
if "CONTRADICTING_POINTS:" not in best:
    best = "CONTRADICTING_POINTS:\n- (extraction failed)"

print(best)
```

[validate: contains "CONTRADICTING_POINTS:"]

---

### Step 6: Score evidence and determine verdict (triple-try, bite-sized)

**Bite-sized step:** Only ask for a score and verdict. The supporting and contradicting points are already extracted — the model just needs to weigh them, which is a simpler judgment task than extracting + scoring + concluding all at once.

```python
claim = args.get('claim', args.get('problem', ''))

# Parse supporting and contradicting points from Steps 4-5
supporting_points = ""
contradicting_points = ""
capture = None
for line in output.strip().split('\n'):
    if line.startswith("SUPPORTING_POINTS:"):
        capture = 'supporting'
        supporting_points += line + "\n"
    elif line.startswith("CONTRADICTING_POINTS:"):
        capture = 'contradicting'
        contradicting_points += line + "\n"
    elif line.startswith("- "):
        if capture == 'supporting':
            supporting_points += line + "\n"
        elif capture == 'contradicting':
            contradicting_points += line + "\n"

prompt = f"""Given the claim "{claim}":

Supporting points:
{supporting_points}

Contradicting points:
{contradicting_points}

Output a structured assessment in this exact format:

EVIDENCE_SCORE: [0-100, where 0=no support, 100=fully supported]
KEY_UNCERTAINTY: [the single most important unknown, in one sentence]
VERDICT: [supported | partially_supported | unsupported | inconclusive]

Do not use your own knowledge. Only use the points provided. If no evidence was found on either side, output VERDICT: inconclusive."""

# Triple-try
responses = []
for i in range(3):
    resp = llm_generate(prompt).strip()
    responses.append(resp)

# Validate and pick best
valid_verdicts = ['supported', 'partially_supported', 'unsupported', 'inconclusive']

def parse_verdict(text):
    for line in text.split('\n'):
        if line.startswith('VERDICT:'):
            v = line.replace('VERDICT:', '').strip().lower()
            if v in valid_verdicts:
                return v
    return None

# Pick the first response with a valid verdict
best = None
for resp in responses:
    if parse_verdict(resp):
        best = resp
        break

if not best:
    # All failed — deterministic fallback
    best = "EVIDENCE_SCORE: 50\nKEY_UNCERTAINTY: Evidence assessment failed\nVERDICT: inconclusive"

print(best)
```

[validate: contains "VERDICT:"]

---

### Step 7: State falsifiability condition (triple-try)

Every claim must have a falsifiability condition. Bounded LLM call — one sentence. Triple-try for consistency.

```python
claim = args.get('claim', args.get('problem', ''))

prompt = f"""For this claim: "{claim}"

What single observation or evidence would prove this claim FALSE? State it in one sentence.

Format: FALSIFIABILITY: [what would disprove this]"""

# Triple-try
responses = []
for i in range(3):
    resp = llm_generate(prompt).strip()
    responses.append(resp)

# Pick the shortest valid response (most concise falsifiability condition)
valid = [r for r in responses if "FALSIFIABILITY:" in r and 15 <= len(r) <= 300]
if valid:
    result = min(valid, key=len)
else:
    # Fallback
    result = f"FALSIFIABILITY: The claim would be disproven if evidence directly contradicts it."

print(result)
```

[validate: contains "FALSIFIABILITY:"]

---

### Step 8: Output structured conclusion

Synthesize all steps into a structured conclusion. This is pure code — no LLM needed. The conclusion is assembled from the validated outputs of prior steps.

```python
# Parse all prior outputs from accumulated context
claim = args.get('claim', args.get('problem', ''))
claim_type = "unknown"
verdict = "inconclusive"
evidence_score = "50"
key_uncertainty = "not assessed"
falsifiability = "not assessed"
supporting_points = []
contradicting_points = []

capture = None
for line in output.strip().split('\n'):
    if line.startswith("CLAIM_TYPE: "):
        claim_type = line.replace("CLAIM_TYPE: ", "").strip()
    elif line.startswith("VERDICT: "):
        verdict = line.replace("VERDICT: ", "").strip()
    elif line.startswith("EVIDENCE_SCORE: "):
        evidence_score = line.replace("EVIDENCE_SCORE: ", "").strip()
    elif line.startswith("KEY_UNCERTAINTY: "):
        key_uncertainty = line.replace("KEY_UNCERTAINTY: ", "").strip()
    elif line.startswith("FALSIFIABILITY: "):
        falsifiability = line.replace("FALSIFIABILITY: ", "").strip()
    elif line.startswith("SUPPORTING_POINTS:"):
        capture = 'supporting'
    elif line.startswith("CONTRADICTING_POINTS:"):
        capture = 'contradicting'
    elif line.startswith("- ") and capture:
        if capture == 'supporting':
            supporting_points.append(line.replace("- ", "").strip())
        elif capture == 'contradicting':
            contradicting_points.append(line.replace("- ", "").strip())

result = f"""CONCLUSION:
  CLAIM: {claim}
  TYPE: {claim_type}
  VERDICT: {verdict}
  EVIDENCE_SCORE: {evidence_score}
  SUPPORTING_POINTS: {'; '.join(supporting_points) if supporting_points else 'none extracted'}
  CONTRADICTING_POINTS: {'; '.join(contradicting_points) if contradicting_points else 'none extracted'}
  KEY_UNCERTAINTY: {key_uncertainty}
  FALSIFIABILITY: {falsifiability}
"""
print(result)
```

[validate: contains "CONCLUSION:"]

---

## Research Justification

1. **Epistemological justification** ([[epistemology-theory-of-knowledge-how-knowledge-is-justified-and-validated-especi]]): Claims require justification through evidence. Foundationalism (basic beliefs) vs coherentism (systemic consistency). This lens implements coherentist justification — the claim is evaluated by how well it fits with existing evidence.

2. **Dual-process theory** ([[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]]): System 1 intuitively accepts claims. System 2 deliberately evaluates evidence. This lens forces System 2 by requiring explicit evidence gathering and weighing.

3. **Metacognitive self-questioning** ([[cognitive-psychology-metacognition-thinking-dispositions-need-for-cognition-refl]]): Actively seeking contradicting evidence (Step 3) is metacognitive self-questioning — the procedure questions its own assumptions by searching for negations.

4. **Triple-try consistency** ([[Deterministic-Scaffolding-for-Small-Models]]): "Triple-process for consistency — run the same input through the AI multiple times, flag divergent outputs." All LLM calls in this lens now run 3 times with majority vote or best-of selection.

5. **Bite-sized steps** ([[Structured-reasoning-formats-for-small-language-models-chain-of-thought-promptin]]): Small models under-think when asked to do too much at once. Breaking Step 4 (formerly score + supporting + contradicting + uncertainty + verdict in one call) into three separate steps (supporting points, contradicting points, score+verdict) gives the small model one task at a time.

6. **Design principle**: Classification and negation are semantic tasks handled by the LLM. Evidence search, counting, and format validation are structural tasks handled by deterministic code. No keyword regex for understanding meaning.

## Related

- [[Think]] — parent procedure that dispatches to this lens
- [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]] — philosophical foundation
- [[epistemology-theory-of-knowledge-how-knowledge-is-justified-and-validated-especi]] — research basis
- [[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]] — research basis
- [[Deterministic-Scaffolding-for-Small-Models]] — triple-try and scaffolding principles