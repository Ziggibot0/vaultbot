---
type: procedure
status: experimental
created: 2026-08-10
summary: "Lens procedure for finding root causes of problems or failures. Based on abductive reasoning (Peirce) and the 5-Whys / fishbone tradition. Uses systematic causal chaining with deterministic stopping conditions. Updated with triple-try consistency and bite-sized steps for small model reliability."
description: "Root cause analysis lens — abductive reasoning + 5-Whys causal chaining with triple-try. Called by Think when problem type is debugging/root-cause."
allowed_tools:
  - vault_search
  - llm_generate
  - run_procedure
tags: [procedure, thinking-lens, root-cause, abductive-reasoning, causal-analysis, triple-try, bite-sized]
depends_on:
  - "[[Think]]"
  - "[[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]]"
research_sources:
  - "[[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]]"
  - "[[psychology-of-analytical-thinking-methods-when-to-use-root-cause-analysis-vs-fir]]"
---

# Root-Cause-Analysis Lens

## When This Lens Is Called

Called by [[Think]] when the problem type is classified as `debugging` or `root-cause` — i.e., something is broken, failing, or behaving unexpectedly, and the task is to find WHY.

## Research Basis

This lens implements **abductive reasoning** (inference to the best explanation), formalized by Charles Sanders Peirce. The 5-Whys technique and fishbone (Ishikawa) diagrams are the operational forms of abductive reasoning in root cause analysis.

Dual-process theory research shows that System 1 (fast, intuitive) thinking tends to jump to the first plausible cause and stop — a classic error in root cause analysis. System 2 (slow, deliberate) thinking forces systematic causal chaining. This lens scaffolds System 2 by forcing each "why" to be answered explicitly and checked against evidence [sources: [[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]]].

## v2 Updates: Triple-Try + Bite-Sized Steps

Following [[Deterministic-Scaffolding-for-Small-Models]]: critical LLM calls run **three times** with majority vote. Steps that asked the small model for too much at once are broken into **bite-sized pieces** — one question per call, one answer per call.

## The Knowledge Triad in This Lens

| Triad Layer | Question | This Lens's Answer |
|---|---|---|
| **Ontology** | What exists? | An observed symptom and a chain of causes leading to it |
| **Epistemology** | How do we know? | Each causal link must be supported by evidence or a testable hypothesis |
| **Hermeneutics** | What does it mean? | The root cause is the interpretation that explains all observed symptoms |

---

### Step 1: State the observed symptom precisely (triple-try)

Extract the symptom in one sentence. Run the LLM **three times** and use majority vote to select the best symptom statement. This is critical — the symptom anchors the entire causal chain.

```python
problem = args.get("problem", "")

prompt = f"""Given this problem, state the observed symptom or failure in ONE sentence. Be specific: what is happening, when, and under what conditions.

Problem: {problem}

Format: SYMPTOM: [precise description]"""

# Triple-try
responses = []
for i in range(3):
    resp = llm_generate(prompt).strip()
    responses.append(resp)

# Parse symptom from each response
def parse_symptom(text):
    for line in text.split('\n'):
        if line.startswith("SYMPTOM:"):
            s = line.replace("SYMPTOM:", "").strip()
            if 10 < len(s) < 500:
                return s
    return None

symptoms = [parse_symptom(r) for r in responses]
symptoms = [s for s in symptoms if s]  # filter None

if symptoms:
    from collections import Counter
    vote = Counter(symptoms)
    symptom, count = vote.most_common(1)[0]
    method = f"triple-try-majority ({count}/3 agree)" if count >= 2 else "triple-try-divergent (using first)"
else:
    symptom = problem
    method = "fallback (all three failed format)"

result = f"SYMPTOM: {symptom}\nMETHOD: {method}"
print(result)
```

[validate: contains "SYMPTOM:"]

---

### Step 2a: List possible causes (bite-sized)

List 3-5 possible causes WITHOUT reasons. This is a simpler task — just brainstorming causes. The reasons come in Step 2b.

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

prompt = f"""Given this symptom, list 3-5 possible causes. Just the cause name, one per line.

Symptom: {symptom}

Format:
CAUSE 1: [cause]
CAUSE 2: [cause]
CAUSE 3: [cause]"""

response = llm_generate(prompt).strip()

# Parse causes
causes = []
for line in response.split("\n"):
    if line.startswith("CAUSE"):
        parts = line.split(":", 1)
        if len(parts) > 1:
            causes.append(parts[1].strip())

# Deterministic fallback
if len(causes) < 2:
    causes = [
        "Configuration error",
        "Dependency/version change",
        "Data corruption or stale state",
        "Network/connectivity issue"
    ]

result = f"SYMPTOM: {symptom}\nCAUSES: {'|'.join(causes)}"
print(result)
```

[validate: contains "CAUSES:"]

---

### Step 2b: Give reasons for each cause (bite-sized)

For each cause listed in Step 2a, explain why it could explain the symptom. One cause at a time — bite-sized for the small model.

```python
# Parse from Step 2a
lines = output.strip().split("\n")
symptom = ""
causes_str = ""
for line in lines:
    if line.startswith("SYMPTOM: "):
        symptom = line.replace("SYMPTOM: ", "").strip()
    elif line.startswith("CAUSES: "):
        causes_str = line.replace("CAUSES: ", "").strip()

causes = [c.strip() for c in causes_str.split("|") if c.strip()] if causes_str else []

# Ask LLM for reasons — one per cause, but in a single bounded call
# (listing reasons for 3-5 causes is still manageable if each is one sentence)
prompt = f"""For each possible cause, explain in ONE sentence why it could explain the symptom.

Symptom: {symptom}

Causes:
{chr(10).join(f"- {c}" for c in causes)}

Format:
REASON 1: [why cause 1 explains the symptom]
REASON 2: [why cause 2 explains the symptom]
..."""

response = llm_generate(prompt).strip()

# Parse reasons
reasons = []
for line in response.split("\n"):
    if line.startswith("REASON"):
        parts = line.split(":", 1)
        if len(parts) > 1:
            reasons.append(parts[1].strip())

# Deterministic fallback: if reasons don't parse, use generic
if len(reasons) < len(causes):
    reasons = [f"Could explain: {c}" for c in causes]

# Format as hypotheses with reasons
hypothesis_lines = []
for i, (cause, reason) in enumerate(zip(causes, reasons), 1):
    hypothesis_lines.append(f"HYPOTHESIS {i}: {cause} — REASON: {reason}")

result = "\n".join(hypothesis_lines) + f"\nSYMPTOM: {symptom}\nCAUSES: {'|'.join(causes)}"
print(result)
```

[validate: contains "HYPOTHESIS"]

---

### Step 3: Apply 5-Whys causal chaining with triple-try on each "why"

Apply 5-Whys to the top hypothesis. Each "why" is already bite-sized (one question, one answer). Now each "why" runs **three times** with majority vote — critical because a bad answer at any level cascades down the chain.

```python
# Parse hypotheses from Step 2b
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

if hypotheses:
    top_hypothesis = hypotheses[0]
if not top_hypothesis:
    top_hypothesis = symptom

causes = [c.strip() for c in causes_str.split("|") if c.strip()] if causes_str else []

# 5-Whys: ask "why" up to 5 times, triple-try on each
chain = []
current = top_hypothesis

for i in range(5):
    prompt = f"""Why does this happen?

Current statement: {current}

Answer in ONE sentence starting with "BECAUSE: ". If you have reached a fundamental cause that cannot be decomposed further, answer "ROOT CAUSE REACHED: [cause]" instead."""

    # Triple-try on each "why"
    responses = []
    for _ in range(3):
        resp = llm_generate(prompt).strip()
        responses.append(resp)

    # Parse each response
    def parse_why(text):
        if "ROOT CAUSE REACHED:" in text:
            root = text.replace("ROOT CAUSE REACHED:", "").strip()
            if 5 < len(root) < 500:
                return ("root", root)
        if "BECAUSE" in text:
            answer = text.split("BECAUSE", 1)[1].lstrip(": ").strip()
            if 5 < len(answer) < 500:
                return ("because", answer)
        return None

    parsed = [parse_why(r) for r in responses]
    parsed = [p for p in parsed if p]  # filter None

    if not parsed:
        chain.append(f"Level {i+1}: BECAUSE {current} (unresolved)")
        break

    # Majority vote on type
    types = [p[0] for p in parsed]
    from collections import Counter
    type_vote = Counter(types)
    winning_type, type_count = type_vote.most_common(1)[0]

    if winning_type == "root":
        # Pick the most common root cause
        roots = [p[1] for p in parsed if p[0] == "root"]
        root_vote = Counter(roots)
        root, root_count = root_vote.most_common(1)[0]
        chain.append(f"Level {i+1}: ROOT -> {root}")
        break
    else:
        # Pick the most common "because" answer
        answers = [p[1] for p in parsed if p[0] == "because"]
        ans_vote = Counter(answers)
        answer, ans_count = ans_vote.most_common(1)[0]
        chain.append(f"Level {i+1}: BECAUSE {answer}")
        current = answer

# Build result
chain_text = "\n".join(chain)
result = f"CAUSAL_CHAIN:\n{chain_text}\nDEPTH: {len(chain)}\nSYMPTOM: {symptom}"
if causes:
    result += f"\nCAUSES: {'|'.join(causes)}"

print(result)
```

[validate: contains "CAUSAL_CHAIN"]

---

### Step 4: Check each causal link against evidence (triple-try)

For each link in the causal chain, determine if it's supported by evidence or is an assumption. Run the LLM **three times** and use majority vote per link.

```python
# Parse causal chain from Step 3
lines = output.strip().split("\n")
chain_lines = [l for l in lines if l.startswith("Level ")]
symptom = ""
for line in lines:
    if line.startswith("SYMPTOM: "):
        symptom = line.replace("SYMPTOM: ", "").strip()
        break

# Also do a vault search for evidence related to the symptom
vault_results = vault_search(symptom, k=3)
vault_docs = []
for r in vault_results:
    title = r.get('name', r.get('file_path', ''))
    vault_docs.append(f"[[{title}]]")
vault_context = f"Vault docs: {' | '.join(vault_docs)}" if vault_docs else "(no vault docs found)"

# Triple-try evidence check
chain_text = "\n".join(chain_lines)
prompt = f"""For each link in this causal chain, is there evidence supporting it, or is it an assumption?

Causal chain:
{chain_text}

{vault_context}

Format each as:
LINK 1: [supported|assumption] — EVIDENCE/TEST: [description]"""

responses = []
for _ in range(3):
    resp = llm_generate(prompt).strip()
    responses.append(resp)

# Parse LINK lines from each response
def parse_links(text):
    links = {}
    for line in text.split("\n"):
        if line.startswith("LINK"):
            parts = line.split(":", 1)
            if len(parts) > 1:
                link_num = parts[0].replace("LINK", "").strip()
                rest = parts[1].strip()
                if "supported" in rest.lower():
                    links[link_num] = ("supported", rest)
                elif "assumption" in rest.lower():
                    links[link_num] = ("assumption", rest)
                else:
                    links[link_num] = ("unknown", rest)
    return links

all_link_parses = [parse_links(r) for r in responses]

# Majority vote per link number
from collections import Counter
final_links = {}
all_link_nums = set()
for lp in all_link_parses:
    all_link_nums.update(lp.keys())

for num in sorted(all_link_nums):
    types = []
    for lp in all_link_parses:
        if num in lp:
            types.append(lp[num][0])
    if types:
        type_vote = Counter(types)
        winning_type, _ = type_vote.most_common(1)[0]
        # Get the description from the first response that has this type
        for lp in all_link_parses:
            if num in lp and lp[num][0] == winning_type:
                final_links[num] = lp[num][1]
                break

# Build result
if not final_links or len(final_links) < len(chain_lines):
    # Fallback: label all as assumptions
    evidence_lines = []
    for i, cl in enumerate(chain_lines, 1):
        evidence_lines.append(f"LINK {i}: assumption — EVIDENCE/TEST: Requires verification")
    result = "\n".join(evidence_lines) + f"\nSYMPTOM: {symptom}"
else:
    result = "\n".join(f"LINK {num}: {final_links[num]}" for num in sorted(final_links.keys(), key=lambda x: int(x) if x.isdigit() else 0))
    result += f"\nSYMPTOM: {symptom}"

if vault_docs:
    result += f"\nVAULT_DOCS: {' | '.join(vault_docs)}"

print(result)
```

[validate: contains "LINK"]

---

### Step 5: Test alternative hypotheses (triple-try)

Review alternative hypotheses (all causes except the top one). Could any of them ALSO explain the symptom?

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

# Triple-try
hypotheses_text = "\n".join(f"- {h}" for h in alt_causes) if alt_causes else "No alternative hypotheses"
prompt = f"""Review these alternative hypotheses. Could any of them ALSO explain the symptom?

Symptom: {symptom}
Alternative hypotheses:
{hypotheses_text}

For each, state:
ALTERNATIVE N: [cause] — PLAUSIBILITY: [high|medium|low] — REASON: [why]"""

responses = []
for _ in range(3):
    resp = llm_generate(prompt).strip()
    responses.append(resp)

# Parse ALTERNATIVE lines and majority vote on plausibility
def parse_alternatives(text):
    alts = {}
    for line in text.split("\n"):
        if line.startswith("ALTERNATIVE"):
            parts = line.split("—")
            if len(parts) >= 3:
                cause = parts[0].split(":", 1)[-1].strip()
                plausibility = "medium"
                for p in parts[1:]:
                    if "high" in p.lower():
                        plausibility = "high"
                    elif "low" in p.lower():
                        plausibility = "low"
                    elif "medium" in p.lower():
                        plausibility = "medium"
                reason = parts[-1].replace("REASON:", "").strip()
                alts[cause] = (plausibility, reason)
    return alts

all_alt_parses = [parse_alternatives(r) for r in responses]

# Majority vote on plausibility per cause
from collections import Counter
final_alts = {}
all_causes = set()
for ap in all_alt_parses:
    all_causes.update(ap.keys())

for cause in all_causes:
    plausibilities = []
    for ap in all_alt_parses:
        if cause in ap:
            plausibilities.append(ap[cause][0])
    if plausibilities:
        vote = Counter(plausibilities)
        winning_plaus, _ = vote.most_common(1)[0]
        # Get reason from first matching response
        for ap in all_alt_parses:
            if cause in ap and ap[cause][0] == winning_plaus:
                final_alts[cause] = (winning_plaus, ap[cause][1])
                break

# Build result
if not final_alts:
    if alt_causes:
        alt_lines = []
        for i, h in enumerate(alt_causes, 1):
            alt_lines.append(f"ALTERNATIVE {i}: {h} — PLAUSIBILITY: medium — REASON: Listed as initial hypothesis")
        result = "\n".join(alt_lines) + f"\nSYMPTOM: {symptom}"
    else:
        result = f"ALTERNATIVE 1: None — PLAUSIBILITY: low — REASON: No alternative hypotheses generated\nSYMPTOM: {symptom}"
else:
    alt_lines = []
    for i, (cause, (plaus, reason)) in enumerate(final_alts.items(), 1):
        alt_lines.append(f"ALTERNATIVE {i}: {cause} — PLAUSIBILITY: {plaus} — REASON: {reason}")
    result = "\n".join(alt_lines) + f"\nSYMPTOM: {symptom}"

print(result)
```

[validate: contains "ALTERNATIVE"]

---

### Step 6a: Identify the root cause (bite-sized + triple-try)

State the root cause from the causal chain. This is a focused question — just the root cause, not contributing factors or confidence yet.

```python
# Parse from Steps 3-5
lines = output.strip().split("\n")
symptom = ""
for line in lines:
    if line.startswith("SYMPTOM: "):
        symptom = line.replace("SYMPTOM: ", "").strip()
        break

# Extract the deepest causal chain link as fallback
chain_lines = [l for l in lines if l.startswith("Level ")]

prompt = f"""Based on the causal chain and evidence checks above, state the root cause — the fundamental cause at the bottom of the chain.

Format:
ROOT CAUSE: [the fundamental cause]"""

# Triple-try
responses = []
for _ in range(3):
    resp = llm_generate(prompt).strip()
    responses.append(resp)

# Parse root cause from each
def parse_root(text):
    for line in text.split("\n"):
        if line.startswith("ROOT CAUSE:"):
            root = line.replace("ROOT CAUSE:", "").strip()
            if 5 < len(root) < 500:
                return root
    return None

roots = [parse_root(r) for r in responses]
roots = [r for r in roots if r]

if roots:
    from collections import Counter
    vote = Counter(roots)
    root_cause, count = vote.most_common(1)[0]
else:
    # Fallback: extract from chain
    if chain_lines:
        last = chain_lines[-1]
        if "ROOT ->" in last:
            root_cause = last.split("ROOT ->")[-1].strip()
        elif "BECAUSE" in last:
            root_cause = last.split("BECAUSE")[-1].strip()
        else:
            root_cause = "Unable to determine — causal chain incomplete"
    else:
        root_cause = "Unable to determine — no causal chain available"

result = f"ROOT CAUSE: {root_cause}\nSYMPTOM: {symptom}"
print(result)
```

[validate: contains "ROOT CAUSE:"]

---

### Step 6b: Identify contributing factors and confidence (bite-sized)

Now that the root cause is identified, list contributing factors and state confidence. Separate from Step 6a so the small model focuses on one thing at a time.

```python
# Parse from Step 6a
lines = output.strip().split("\n")
root_cause = ""
symptom = ""
for line in lines:
    if line.startswith("ROOT CAUSE: "):
        root_cause = line.replace("ROOT CAUSE: ", "").strip()
    elif line.startswith("SYMPTOM: "):
        symptom = line.replace("SYMPTOM: ", "").strip()

prompt = f"""Given the root cause, list contributing factors and state confidence.

Root cause: {root_cause}
Symptom: {symptom}

Format:
CONTRIBUTING FACTORS: [list any other causes that made the problem worse, or "none identified"]
CONFIDENCE: [high|medium|low]
CONFIDENCE_REASON: [why this confidence level]"""

response = llm_generate(prompt).strip()

# Parse
contributing = "none identified"
confidence = "low"
confidence_reason = "not assessed"

for line in response.split("\n"):
    if line.startswith("CONTRIBUTING FACTORS:"):
        contributing = line.replace("CONTRIBUTING FACTORS:", "").strip()
    elif line.startswith("CONFIDENCE:"):
        conf = line.replace("CONFIDENCE:", "").strip().lower()
        if conf in ['high', 'medium', 'low']:
            confidence = conf
    elif line.startswith("CONFIDENCE_REASON:"):
        confidence_reason = line.replace("CONFIDENCE_REASON:", "").strip()

# Deterministic fallback
if confidence == "low" and confidence_reason == "not assessed":
    confidence_reason = "Deterministic fallback (LLM output unclear)"

result = f"ROOT CAUSE: {root_cause}\nCONTRIBUTING FACTORS: {contributing}\nCONFIDENCE: {confidence}\nCONFIDENCE_REASON: {confidence_reason}\nSYMPTOM: {symptom}"
print(result)
```

[validate: contains "CONFIDENCE:"]

---

### Step 7a: Propose a fix (bite-sized)

Propose a specific fix for the root cause. Just the fix — verification comes in Step 7b.

```python
# Parse from Step 6b
lines = output.strip().split("\n")
root_cause = ""
symptom = ""
for line in lines:
    if line.startswith("ROOT CAUSE: "):
        root_cause = line.replace("ROOT CAUSE: ", "").strip()
    elif line.startswith("SYMPTOM: "):
        symptom = line.replace("SYMPTOM: ", "").strip()

prompt = f"""Propose a specific fix for this root cause.

Root cause: {root_cause}
Symptom: {symptom}

Format:
FIX: [proposed action]"""

# Triple-try
responses = []
for _ in range(3):
    resp = llm_generate(prompt).strip()
    responses.append(resp)

def parse_fix(text):
    for line in text.split("\n"):
        if line.startswith("FIX:"):
            fix = line.replace("FIX:", "").strip()
            if 5 < len(fix) < 500:
                return fix
    return None

fixes = [parse_fix(r) for r in responses]
fixes = [f for f in fixes if f]

if fixes:
    from collections import Counter
    vote = Counter(fixes)
    fix, count = vote.most_common(1)[0]
else:
    fix = f"Investigate and address the root cause: {root_cause}"

result = f"FIX: {fix}\nROOT_CAUSE: {root_cause}\nSYMPTOM: {symptom}"
print(result)
```

[validate: contains "FIX:"]

---

### Step 7b: Verify the fix addresses the root cause (bite-sized)

Check whether the proposed fix addresses the root cause itself or just the symptom, and how to verify it works.

```python
# Parse from Step 7a
lines = output.strip().split("\n")
fix = ""
root_cause = ""
symptom = ""
for line in lines:
    if line.startswith("FIX: "):
        fix = line.replace("FIX: ", "").strip()
    elif line.startswith("ROOT_CAUSE: "):
        root_cause = line.replace("ROOT_CAUSE: ", "").strip()
    elif line.startswith("SYMPTOM: "):
        symptom = line.replace("SYMPTOM: ", "").strip()

prompt = f"""Does this fix address the root cause itself, or just the symptom? And how would you verify it works?

Fix: {fix}
Root cause: {root_cause}
Symptom: {symptom}

Format:
ADDRESSES: [root cause | symptom only]
VERIFICATION: [how to confirm the fix works]"""

response = llm_generate(prompt).strip()

addresses = "root cause"
verification = "Re-run the procedure after applying the fix and confirm the symptom no longer occurs"

for line in response.split("\n"):
    if line.startswith("ADDRESSES:"):
        addr = line.replace("ADDRESSES:", "").strip().lower()
        if "symptom" in addr and "root" not in addr:
            addresses = "symptom only"
        elif "root" in addr:
            addresses = "root cause"
    elif line.startswith("VERIFICATION:"):
        verification = line.replace("VERIFICATION:", "").strip()

result = f"FIX: {fix}\nADDRESSES: {addresses}\nVERIFICATION: {verification}\nSYMPTOM: {symptom}\nROOT_CAUSE: {root_cause}"
print(result)
```

[validate: contains "ADDRESSES:"]

---

## Research Justification

1. **Abductive reasoning** (Peirce): This lens implements inference to the best explanation — the core of root cause analysis. Each hypothesis is tested against evidence before acceptance.

2. **Dual-process theory** ([[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]]): System 1 jumps to the first plausible cause. The 5-Whys chain forces System 2 engagement by requiring explicit causal links.

3. **Knowledge Triad** ([[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]]): Ontology = symptom and causes (what exists). Epistemology = evidence checks (how we know). Hermeneutics = root cause interpretation (what it means).

4. **Deterministic scaffolding** ([[Deterministic-Scaffolding-for-Small-Models]]): Every LLM call has a deterministic fallback. Triple-try with majority vote on critical steps. Bite-sized steps so the small model gets one question at a time.

## Related

- [[Think]] — parent procedure that dispatches to this lens
- [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]] — philosophical foundation
- [[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]] — research basis
- [[Deterministic-Scaffolding-for-Small-Models]] — triple-try and bite-sized step patterns