---
type: procedure
status: verified
baseline: true
created: 2026-08-10
summary: "Lens procedure for evaluating claims by weighing evidence for and against. v4: redesigned for qwen3.5:4b — killed triple-try, merged over-split steps, richer prompts. Based on epistemological justification theory and Bayesian reasoning. ~4 LLM calls (down from ~24). Already optimal — no v4.1 changes needed."
description: "Weigh evidence for and against a claim using structured justification analysis. Called by Think when problem type is VERIFY."
when_to_use: "When evaluating whether a claim is true by weighing evidence for and against. When verifying a factual claim. When assessing evidence quality. When asked 'is this claim true?' or 'what evidence supports this?' or 'evaluate this claim'. When doing fact-checking or verification."
tags: [procedure, thinking, lens, evidence, epistemology, reasoning, v4, qwen3.5-4b, optimal]
allowed_tools:
  - vault_search
  - llm_generate
  - run_procedure
depends_on:
  - "[[Think]]"
  - "[[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]]"
research_sources:
  - "[[epistemology-theory-of-knowledge-how-knowledge-is-justified-and-validated-especi]]"
  - "[[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]]"
  - "[[cognitive-psychology-metacognition-thinking-dispositions-need-for-cognition-refl]]"
success_count: 36
failure_count: 0
success_rate: 1.0
---

# Evidence-Weighing Lens (v4)

**Part of the [[Think]] procedure system.** Called when the problem is a factual or empirical claim that needs verification.

**Research basis:** Epistemological justification theory (foundationalism vs coherentism) from [[epistemology-theory-of-knowledge-how-knowledge-is-justified-and-validated-especi]]; dual-process theory from [[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]]; metacognitive self-questioning from [[cognitive-psychology-metacognition-thinking-dispositions-need-for-cognition-refl]].

**Knowledge Triad mapping:**
- **Ontology**: What is the claim? What entities does it involve?
- **Epistemology**: What evidence supports it? What evidence contradicts it?
- **Hermeneutics**: What should we conclude given the weight of evidence?

## What Changed in v4

| Problem in v3 | Fix in v4 |
|---|---|
| Triple-try on every LLM call (3x cost) | Single calls — 4B is consistent enough |
| Steps 4+5 split (supporting points, contradicting points separately) | Merged into one call — 4B can handle both sides at once |
| Step 6 was score+verdict alone | Merged with falsifiability into one assessment call |
| 8 steps, ~24 LLM calls | 5 steps, ~5-7 LLM calls |

## v4.1 Audit Result: **Already Optimal**

This lens was audited for further simplification opportunities. **No changes needed** — it's already at its optimal call count:

- **Step 1**: Classify the claim type (1 LLM call)
- **Step 2**: Gather supporting and contradicting evidence from vault (1 LLM call for negation + 2 vault searches, deterministic)
- **Step 3**: Extract supporting and contradicting points (1 LLM call) — merged from v3 steps 4+5
- **Step 4**: Score evidence, determine verdict, and state falsifiability (1 LLM call) — merged from v3 steps 6+7
- **Step 5**: Output structured conclusion — **deterministic, zero LLM cost**
- **Total: 4 LLM calls**

All four LLM steps are necessary and cannot be further batched without losing the structured justification analysis. Step 5 being deterministic is a key architectural win.

---

### Step 1: Classify the claim type

Classify the claim type. Single call — the 4B handles this reliably.

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

resp = llm_generate(prompt).strip().lower()

# Parse — 4B follows format instructions reliably
valid_types = ['definitional', 'causal', 'normative', 'predictive', 'comparative']
claim_type = 'unknown'
resp_clean = resp.strip().lower().rstrip('.').rstrip(',')
if resp_clean in valid_types:
    claim_type = resp_clean
else:
    # Check if a valid type appears as a word in the response
    for w in resp_clean.split():
        w = w.rstrip('.').rstrip(',')
        if w in valid_types:
            claim_type = w
            break

result = f"CLAIM: {claim}\nCLAIM_TYPE: {claim_type}"
print(result)
```

[validate: contains "CLAIM:"]
[validate: contains "CLAIM_TYPE:"]

---

### Step 2: Gather supporting and contradicting evidence from vault

Search the vault for notes relevant to the claim AND its negation. The 4B can generate a good negation in one call. Two vault searches, one LLM call for negation.

```python
claim = args.get('claim', args.get('problem', ''))

# Parse claim type from Step 1
lines = output.strip().split('\n')
claim_type = ''
for line in lines:
    if line.startswith('CLAIM_TYPE: '):
        claim_type = line.replace('CLAIM_TYPE: ', '').strip()

# Generate semantic negation (single call — 4B is reliable)
negation_prompt = f'Given this claim: "{claim}"\n\nWrite the OPPOSITE claim — the strongest version that contradicts it. Keep it to one sentence.\n\nRespond with ONLY the negated claim, nothing else.'
negated = llm_generate(negation_prompt).strip()

if len(negated) < 5 or len(negated) > 300:
    negated = f"It is not the case that: {claim}"

# Search vault for both supporting and contradicting evidence
supporting_results = vault_search(claim, k=5)
contradicting_results = vault_search(negated, k=5)

supporting = []
for r in supporting_results:
    supporting.append({
        'title': r.get('title', r.get('file_path', '')),
        'snippet': r.get('snippet', '')[:200]
    })

contradicting = []
for r in contradicting_results:
    contradicting.append({
        'title': r.get('title', r.get('file_path', '')),
        'snippet': r.get('snippet', '')[:200]
    })

result = f"CLAIM: {claim}\nCLAIM_TYPE: {claim_type}\nNEGATION_USED: {negated}\nSUPPORTING_EVIDENCE: {len(supporting)} notes found\n"
for s in supporting:
    result += f"  - {s['title']}: {s['snippet']}\n"
result += f"CONTRADICTING_EVIDENCE: {len(contradicting)} notes found\n"
for c in contradicting:
    result += f"  - {c['title']}: {c['snippet']}\n"
print(result)
```

[validate: contains "SUPPORTING_EVIDENCE:"]
[validate: contains "CONTRADICTING_EVIDENCE:"]

---

### Step 3: Extract supporting and contradicting points (merged)

The 4B can extract both supporting AND contradicting points in a single call. This was 2 separate steps in v3 (each with triple-try = 6 calls). Now it's 1 call.

```python
claim = args.get('claim', args.get('problem', ''))

# Parse evidence from Step 2
lines = output.strip().split('\n')
supporting_text = ""
contradicting_text = ""
capture = None
for line in lines:
    if line.startswith("SUPPORTING_EVIDENCE:"):
        capture = 'supporting'
        supporting_text += line + "\n"
    elif line.startswith("CONTRADICTING_EVIDENCE:"):
        capture = 'contradicting'
        contradicting_text += line + "\n"
    elif line.startswith("  - "):
        if capture == 'supporting':
            supporting_text += line + "\n"
        elif capture == 'contradicting':
            contradicting_text += line + "\n"

prompt = f"""Given the claim "{claim}" and the evidence from vault notes:

Supporting evidence:
{supporting_text}

Contradicting evidence:
{contradicting_text}

List the 1-3 strongest points that SUPPORT this claim AND the 1-3 strongest points that CONTRADICT this claim. Use ONLY the evidence provided — do not use your own knowledge.

Format:
SUPPORTING_POINTS:
- [point 1]
- [point 2 if available]

CONTRADICTING_POINTS:
- [point 1]
- [point 2 if available]

If no evidence was found on a side, respond with "(no evidence found)" for that side."""

resp = llm_generate(prompt).strip()

if "SUPPORTING_POINTS:" not in resp:
    resp = "SUPPORTING_POINTS:\n- (extraction failed)\n\nCONTRADICTING_POINTS:\n- (extraction failed)"

print(resp)
```

[validate: contains "SUPPORTING_POINTS:"]
[validate: contains "CONTRADICTING_POINTS:"]

---

### Step 4: Score evidence, determine verdict, and state falsifiability (merged)

The 4B can weigh evidence, state a verdict, AND provide a falsifiability condition in one call. This was 2 separate steps in v3 (each with triple-try = 6 calls). Now it's 1 call.

```python
claim = args.get('claim', args.get('problem', ''))

# Parse supporting and contradicting points from Step 3
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
FALSIFIABILITY: [what single observation or evidence would prove this claim FALSE? One sentence.]

Do not use your own knowledge. Only use the points provided. If no evidence was found on either side, output VERDICT: inconclusive."""

resp = llm_generate(prompt).strip()

# Validate verdict
valid_verdicts = ['supported', 'partially_supported', 'unsupported', 'inconclusive']
has_valid_verdict = False
for line in resp.split('\n'):
    if line.startswith('VERDICT:'):
        v = line.replace('VERDICT:', '').strip().lower()
        if v in valid_verdicts:
            has_valid_verdict = True
            break

if not has_valid_verdict:
    resp = "EVIDENCE_SCORE: 50\nKEY_UNCERTAINTY: Evidence assessment failed\nVERDICT: inconclusive\nFALSIFIABILITY: The claim would be disproven if evidence directly contradicts it."

print(resp)
```

[validate: contains "VERDICT:"]
[validate: contains "FALSIFIABILITY:"]

---

### Step 5: Output structured conclusion

Synthesize all steps into a structured conclusion. Pure code assembly — no LLM needed.

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

1. **Epistemological justification** ([[epistemology-theory-of-knowledge-how-knowledge-is-justified-and-validated-especi]]): Claims require justification through evidence. This lens implements coherentist justification — the claim is evaluated by how well it fits with existing evidence.

2. **Dual-process theory** ([[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]]): System 1 intuitively accepts claims. System 2 deliberately evaluates evidence. This lens forces System 2 by requiring explicit evidence gathering and weighing.

3. **Metacognitive self-questioning** ([[cognitive-psychology-metacognition-thinking-dispositions-need-for-cognition-refl]]): Actively seeking contradicting evidence (Step 2) is metacognitive self-questioning — the procedure questions its own assumptions by searching for negations.

4. **4B model capability**: The qwen3.5:4b follows format instructions reliably, handles multi-part prompts (supporting + contradicting in one call), and produces consistent verdicts without triple-try. This eliminates ~18 LLM calls per run.

## Related

- [[Think]] — parent procedure that dispatches to this lens
- [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]] — philosophical foundation
- [[epistemology-theory-of-knowledge-how-knowledge-is-justified-and-validated-especi]] — research basis
- [[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]] — research basis
- [[Deterministic-Scaffolding-for-Small-Models]] — scaffolding principles (v3 era, now updated for 4B)