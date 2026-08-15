---
type: procedure
status: experimental
baseline: true
created: 2026-08-10
summary: "Lens procedure for finding root causes of problems or failures. Based on abductive reasoning (Peirce) and the 5-Whys / fishbone tradition. v4.1: batched 5-Whys into single call (saves 3-4 LLM calls). ~4-5 LLM calls (down from 8-10)."
description: "Root cause analysis lens — abductive reasoning + 5-Whys causal chaining. Called by Think when problem type is debugging/root-cause."
allowed_tools:
  - vault_search
  - llm_generate
  - run_procedure
when_to_use: "When something is broken, failing, or behaving unexpectedly and you need to find WHY. When debugging a problem. When doing 5-Whys analysis. When finding the root cause of a failure. When asked 'why does X happen?' or 'what's causing this bug?'"
tags: [procedure, thinking-lens, root-cause, abductive-reasoning, causal-analysis, v4, qwen3.5-4b, batched]
depends_on:
  - "[[Think]]"
  - "[[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]]"
research_sources:
  - "[[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]]"
  - "[[psychology-of-analytical-thinking-methods-when-to-use-root-cause-analysis-vs-fir]]"
---

# Root-Cause-Analysis Lens (v4.1)

## When This Lens Is Called

Called by [[Think]] when the problem type is classified as `WHY` — i.e., something is broken, failing, or behaving unexpectedly, and the task is to find WHY.

## What Changed in v4.1

| Problem in v4 | Fix in v4.1 |
|---|---|
| Step 3: 5-Whys loop made up to 5 sequential LLM calls (one per "why") | **Batched into 1 call** — the 4B model can generate the full causal chain in one pass with structured output format |
| ~8-10 LLM calls total | **~4-5 LLM calls total** (saves 3-4 calls) |

## What Changed in v4 (from v3)

| Problem in v3 | Fix in v4 |
|---|---|
| Triple-try on 7 of 10 steps = 21 extra LLM calls | Single-call with deterministic fallback. The 4B is consistent. |
| Steps 2a+2b split (list causes, then give reasons) | Merged: 4B can list causes WITH reasons in one call |
| Steps 6a+6b split (root cause, then contributing+confidence) | Merged: 4B can state root cause + contributing factors + confidence in one call |
| Steps 7a+7b split (propose fix, then verify fix) | Merged: 4B can propose a fix AND say whether it addresses root cause in one call |
| 10 steps total | 6 steps total |
| ~34 LLM calls | ~8-10 LLM calls |

## Research Basis

This lens implements **abductive reasoning** (inference to the best explanation), formalized by Charles Sanders Peirce. The 5-Whys technique and fishbone (Ishikawa) diagrams are the operational forms of abductive reasoning in root cause analysis.

Dual-process theory research shows that System 1 (fast, intuitive) thinking tends to jump to the first plausible cause and stop — a classic error in root cause analysis. System 2 (slow, deliberate) thinking forces systematic causal chaining. This lens scaffolds System 2 by forcing each "why" to be answered explicitly and checked against evidence [sources: [[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]]].

**Why batching 5-Whys works with 4B models:** The qwen3.5:4b model has sufficient context window and instruction-following capability to generate a structured multi-level causal chain in a single call when given an explicit output format (LEVEL 1, LEVEL 2, etc. with ROOT CAUSE REACHED termination). This was validated in the Think v4 redesign where the 4B consistently handled multi-part structured outputs (claim extraction, classification+lens selection, synthesis) that the 0.8B model could not. The deterministic fallback (hardcoded chain) ensures safety if the model output is malformed.

## The Knowledge Triad in This Lens

| Triad Layer | Question | This Lens's Answer |
|---|---|---|
| **Ontology** | What exists? | An observed symptom and a chain of causes leading to it |
| **Epistemology** | How do we know? | Each causal link must be supported by evidence or a testable hypothesis |
| **Hermeneutics** | What does it mean? | The root cause is the interpretation that explains all observed symptoms |

---

### Step 1: State the observed symptom precisely

Extract the symptom in one sentence. The 4B can handle this in a single call with a richer prompt.

```python
problem = args.get("problem", "")

prompt = f"""Given this problem, state the observed symptom or failure in ONE sentence. Be specific: what is happening, when, and under what conditions.

Problem: {problem}

Format: SYMPTOM: [precise description]"""

resp = llm_generate(prompt).strip()

# Parse symptom
symptom = problem  # fallback
for line in resp.split('\n'):
    if line.startswith("SYMPTOM:"):
        s = line.replace("SYMPTOM:", "").strip()
        if 10 < len(s) < 500:
            symptom = s
            break

result = f"SYMPTOM: {symptom}"
print(result)
```

[validate: contains "SYMPTOM:"]

---

### Step 2: List possible causes with reasons

List 3-5 possible causes, each with a one-sentence reason explaining why it could cause the symptom. The 4B can handle both in one call — no need to split into 2a/2b.

```python
# Parse symptom from Step 1
lines = output.strip().split("\n")
symptom = ""
for line in lines:
    if line.startswith("SYMPTOM: "):
        symptom = line.replace("SYMPTOM: ", "").strip()
        break
if not symptom:
    symptom = args.get("problem", "")

prompt = f"""Given this symptom, list 3-5 possible causes. For each cause, give ONE sentence explaining why it could explain the symptom.

Symptom: {symptom}

Format:
CAUSE 1: [cause] — REASON: [why it explains the symptom]
CAUSE 2: [cause] — REASON: [why it explains the symptom]
CAUSE 3: [cause] — REASON: [why it explains the symptom]"""

response = llm_generate(prompt).strip()

# Parse causes and reasons
causes = []
reasons = []
for line in response.split("\n"):
    if line.startswith("CAUSE"):
        parts = line.split("—")
        if len(parts) >= 2:
            cause_part = parts[0].split(":", 1)
            if len(cause_part) > 1:
                causes.append(cause_part[1].strip())
            reason_part = parts[-1].replace("REASON:", "").strip()
            reasons.append(reason_part)

# Deterministic fallback
if len(causes) < 2:
    causes = [
        "Configuration error",
        "Dependency/version change",
        "Data corruption or stale state",
        "Network/connectivity issue"
    ]
    reasons = [
        "Misconfiguration could cause the observed behavior",
        "A changed dependency could introduce the symptom",
        "Corrupted or stale data could produce the failure",
        "Network issues could intermittently cause the symptom"
    ]

# Format as hypotheses
hypothesis_lines = []
for i, (cause, reason) in enumerate(zip(causes, reasons), 1):
    hypothesis_lines.append(f"HYPOTHESIS {i}: {cause} — REASON: {reason}")

result = "\n".join(hypothesis_lines) + f"\nSYMPTOM: {symptom}\nCAUSES: {'|'.join(causes)}"
print(result)
```

[validate: contains "HYPOTHESIS"]

---

### Step 3: Apply 5-Whys causal chaining (BATCHED — single LLM call)

Apply 5-Whys to the top hypothesis. **Single call** — the 4B generates the full causal chain in one pass with structured format. Stop when ROOT CAUSE REACHED or after 5 levels.

```python
# Parse hypotheses from Step 2
lines = output.strip().split("\n")
symptom = ""
causes_str = ""
hypotheses = []
for line in lines:
    if line.startswith("SYMPTOM: "):
        symptom = line.replace("SYMPTOM: ", "").strip()
    elif line.startswith("CAUSES: "):
        causes_str = line.replace("CAUSES: ", "").strip()
    elif line.startswith("HYPOTHESIS "):
        parts = line.split("—")
        cause = parts[0].replace("HYPOTHESIS", "").strip()
        if ":" in cause:
            cause = cause.split(":", 1)[1].strip()
        hypotheses.append(cause)

top_hypothesis = hypotheses[0] if hypotheses else symptom
causes = [c.strip() for c in causes_str.split("|") if c.strip()] if causes_str else []

# BATCHED 5-Whys: single call for the full causal chain
# Simplified prompt to prevent timeout - explicit format, short output constraint
prompt = f"""5-Whys chain for: {top_hypothesis}

Output exactly 1-5 lines, one per level. Stop at ROOT.
Format: LEVEL N: BECAUSE [cause]  OR  LEVEL N: ROOT [cause]

LEVEL 1: BECAUSE"""

resp = llm_generate(prompt).strip()

# Parse the batched response - more robust parsing
chain = []
for line in resp.split('\n'):
    line = line.strip()
    if not line:
        continue
    # Match LEVEL N: BECAUSE [cause] or LEVEL N: ROOT [cause]
    if line.upper().startswith("LEVEL"):
        # Extract level number and content after colon
        if ":" in line:
            content = line.split(":", 1)[1].strip()
            if content.upper().startswith("ROOT"):
                root = content[4:].strip()  # remove "ROOT"
                if 3 < len(root) < 300:
                    chain.append(f"Level {len(chain)+1}: ROOT -> {root}")
                    break
            elif content.upper().startswith("BECAUSE"):
                cause = content[7:].strip()  # remove "BECAUSE"
                if 3 < len(cause) < 300:
                    chain.append(f"Level {len(chain)+1}: BECAUSE {cause}")

# Deterministic fallback if parsing fails or chain too short
if len(chain) < 2:
    chain = [
        f"Level 1: BECAUSE {top_hypothesis}",
        "Level 2: ROOT -> Underlying systemic issue not fully identified"
    ]

# Build result
chain_text = "\n".join(chain)
result = f"CAUSAL_CHAIN:\n{chain_text}\nDEPTH: {len(chain)}\nSYMPTOM: {symptom}"
if causes:
    result += f"\nCAUSES: {'|'.join(causes)}"

print(result)
```

[validate: contains "CAUSAL_CHAIN"]

---

### Step 4: Check each causal link against evidence

For each link in the causal chain, determine if it's supported by evidence or is an assumption. The 4B can evaluate all links in one call. Also do a vault search for evidence.

```python
# Parse causal chain from Step 3
lines = output.strip().split("\n")
chain_lines = [l for l in lines if l.startswith("Level ")]
symptom = ""
for line in lines:
    if line.startswith("SYMPTOM: "):
        symptom = line.replace("SYMPTOM: ", "").strip()
        break

# Vault search for evidence
vault_results = vault_search(symptom, k=3)
vault_docs = []
for r in vault_results:
    title = r.get('name', r.get('file_path', ''))
    vault_docs.append(f"[[{title}]]")
vault_context = f"Vault docs: {' | '.join(vault_docs)}" if vault_docs else "(no vault docs found)"

chain_text = "\n".join(chain_lines)
prompt = f"""For each link in this causal chain, is there evidence supporting it, or is it an assumption?

Causal chain:
{chain_text}

{vault_context}

Format each as:
LINK 1: [supported|assumption] — EVIDENCE/TEST: [description]
LINK 2: [supported|assumption] — EVIDENCE/TEST: [description]"""

response = llm_generate(prompt).strip()

# Parse LINK lines
evidence_lines = []
for line in response.split("\n"):
    if line.startswith("LINK"):
        evidence_lines.append(line)

# Deterministic fallback
if len(evidence_lines) < len(chain_lines):
    evidence_lines = []
    for i, cl in enumerate(chain_lines, 1):
        evidence_lines.append(f"LINK {i}: assumption — EVIDENCE/TEST: Requires verification")

result = "\n".join(evidence_lines) + f"\nSYMPTOM: {symptom}"
if vault_docs:
    result += f"\nVAULT_DOCS: {' | '.join(vault_docs)}"

print(result)
```

[validate: contains "LINK"]

---

### Step 5: Test alternative hypotheses and identify root cause + confidence

Review alternative hypotheses (all causes except the top one). Could any of them ALSO explain the symptom? Then state the root cause, contributing factors, and confidence. The 4B can handle all of this in one call.

```python
# Parse from Step 4
lines = output.strip().split("\n")
symptom = ""
causes_str = ""
for line in lines:
    if line.startswith("SYMPTOM: "):
        symptom = line.replace("SYMPTOM: ", "").strip()
    elif line.startswith("CAUSES: "):
        causes_str = line.replace("CAUSES: ", "").strip()

causes = [c.strip() for c in causes_str.split("|") if c.strip()] if causes_str else []
alt_causes = causes[1:] if len(causes) > 1 else []

# Also extract chain for root cause reference
chain_lines = [l for l in lines if l.startswith("Level ")]
chain_text = "\n".join(chain_lines) if chain_lines else "No causal chain available"

hypotheses_text = "\n".join(f"- {h}" for h in alt_causes) if alt_causes else "No alternative hypotheses"

prompt = f"""Based on the causal chain and evidence checks, answer these three questions:

1. Could any alternative hypothesis ALSO explain the symptom?
2. What is the root cause (the fundamental cause at the bottom of the chain)?
3. What are contributing factors, and what is your confidence level?

Symptom: {symptom}
Causal chain:
{chain_text}

Alternative hypotheses:
{hypotheses_text}

Format:
ALTERNATIVE 1: [cause] — PLAUSIBILITY: [high|medium|low] — REASON: [why]
ROOT CAUSE: [the fundamental cause]
CONTRIBUTING FACTORS: [other causes that made it worse, or "none identified"]
CONFIDENCE: [high|medium|low]
CONFIDENCE_REASON: [why this confidence level]"""

response = llm_generate(prompt).strip()

# Parse all fields
root_cause = "Unable to determine"
contributing = "none identified"
confidence = "low"
confidence_reason = "not assessed"
alt_lines = []

for line in response.split("\n"):
    if line.startswith("ALTERNATIVE"):
        alt_lines.append(line)
    elif line.startswith("ROOT CAUSE:"):
        rc = line.replace("ROOT CAUSE:", "").strip()
        if 5 < len(rc) < 500:
            root_cause = rc
    elif line.startswith("CONTRIBUTING FACTORS:"):
        contributing = line.replace("CONTRIBUTING FACTORS:", "").strip()
    elif line.startswith("CONFIDENCE:"):
        conf = line.replace("CONFIDENCE:", "").strip().lower()
        if conf in ['high', 'medium', 'low']:
            confidence = conf
    elif line.startswith("CONFIDENCE_REASON:"):
        confidence_reason = line.replace("CONFIDENCE_REASON:", "").strip()

# Fallback for root cause from chain
if root_cause == "Unable to determine" and chain_lines:
    last = chain_lines[-1]
    if "ROOT ->" in last:
        root_cause = last.split("ROOT ->")[-1].strip()
    elif "BECAUSE" in last:
        root_cause = last.split("BECAUSE")[-1].strip()

# Fallback for alternatives
if not alt_lines and alt_causes:
    for i, h in enumerate(alt_causes, 1):
        alt_lines.append(f"ALTERNATIVE {i}: {h} — PLAUSIBILITY: medium — REASON: Listed as initial hypothesis")

if confidence_reason == "not assessed":
    confidence_reason = "Deterministic fallback (LLM output unclear)"

result = "\n".join(alt_lines) + f"\nROOT CAUSE: {root_cause}\nCONTRIBUTING FACTORS: {contributing}\nCONFIDENCE: {confidence}\nCONFIDENCE_REASON: {confidence_reason}\nSYMPTOM: {symptom}"
print(result)
```

[validate: contains "ROOT CAUSE:"]
[validate: contains "CONFIDENCE:"]

---

### Step 6: Propose and verify a fix

Propose a specific fix for the root cause, and check whether it addresses the root cause itself or just the symptom. The 4B can do both in one call.

```python
# Parse from Step 5
lines = output.strip().split("\n")
root_cause = ""
symptom = ""
for line in lines:
    if line.startswith("ROOT CAUSE: "):
        root_cause = line.replace("ROOT CAUSE: ", "").strip()
    elif line.startswith("SYMPTOM: "):
        symptom = line.replace("SYMPTOM: ", "").strip()

prompt = f"""Propose a specific fix for this root cause, and verify it addresses the root cause (not just the symptom).

Root cause: {root_cause}
Symptom: {symptom}

Format:
FIX: [proposed action]
ADDRESSES: [root cause | symptom only]
VERIFICATION: [how to confirm the fix works]"""

response = llm_generate(prompt).strip()

# Parse
fix = f"Investigate and address the root cause: {root_cause}"
addresses = "root cause"
verification = "Re-run the procedure after applying the fix and confirm the symptom no longer occurs"

for line in response.split("\n"):
    if line.startswith("FIX:"):
        f = line.replace("FIX:", "").strip()
        if 5 < len(f) < 500:
            fix = f
    elif line.startswith("ADDRESSES:"):
        addr = line.replace("ADDRESSES:", "").strip().lower()
        if addr in ['root cause', 'root cause only', 'root']:
            addresses = "root cause"
        elif addr in ['symptom only', 'symptom']:
            addresses = "symptom only"
    elif line.startswith("VERIFICATION:"):
        verification = line.replace("VERIFICATION:", "").strip()

result = f"FIX: {fix}\nADDRESSES: {addresses}\nVERIFICATION: {verification}\nSYMPTOM: {symptom}\nROOT_CAUSE: {root_cause}"
print(result)
```

[validate: contains "FIX:"]
[validate: contains "ADDRESSES:"]

---

## Research Justification

1. **Abductive reasoning** (Peirce): This lens implements inference to the best explanation — the core of root cause analysis. Each hypothesis is tested against evidence before acceptance.

2. **Dual-process theory** ([[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]]): System 1 jumps to the first plausible cause. The 5-Whys chain forces System 2 engagement by requiring explicit causal links.

3. **Knowledge Triad** ([[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]]): Ontology = symptom and causes (what exists). Epistemology = evidence checks (how we know). Hermeneutics = root cause interpretation (what it means).

4. **Deterministic scaffolding** ([[Deterministic-Scaffolding-for-Small-Models]]): Every LLM call has a deterministic fallback. The 4B model is consistent enough to skip triple-try — single calls with fallbacks are sufficient.

5. **Batching validation for 4B models** (this procedure's v4.1 change): The qwen3.5:4b model demonstrated consistent structured output generation in Think v4 (merged claim extraction, classification+lens selection, synthesis). The 5-Whys chain is a natural fit for the same pattern — explicit format constraints (LEVEL N: BECAUSE/ROOT CAUSE REACHED) guide the model to produce a complete chain in one pass. Deterministic fallback preserves safety.

## Related

- [[Think]] — parent procedure that dispatches to this lens
- [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]] — philosophical foundation
- [[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]] — research basis
- [[Deterministic-Scaffolding-for-Small-Models]] — deterministic fallback patterns