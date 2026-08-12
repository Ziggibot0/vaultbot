---
type: procedure
status: experimental
created: 2026-08-10
summary: "Parent reasoning procedure: extracts premises, validates them against the vault (BS detector), classifies the problem, picks lenses, dispatches them, and synthesizes results. v3: fully v2 code-step format with bite-sized LLM calls designed for 0.8B models. Procedure terminology never appears in LLM prompts."
description: "BS detector + problem classification + lens dispatch + synthesis. v3 code steps for small-model reliability."
allowed_tools:
  - vault_search
  - vault_read_note
  - llm_generate
  - run_procedure
tags: [procedure, reasoning, think, v3, code-steps, bite-sized, small-model]
---

# Think: Structured Reasoning Scaffold (v3)

## Purpose

This procedure makes a small local model reason like a frontier model by following deterministic code steps with tiny, focused LLM calls. The LLM only does semantic judgment — one question per call, one answer per call. Everything else is code.

## What Changed in v3

| Problem in v2 | Fix in v3 |
|---|---|
| Compiled as text steps — entire 940-line procedure sent to 0.8B model as one prompt | v2 code steps — Python runs deterministically, LLM calls are isolated `[llm:]` tags |
| LLM prompts contained procedure terminology (ontology, epistemology, lens stack) — model pattern-matched on its own description | LLM prompts are 1-3 sentences with no procedure jargon — the model only sees the problem |
| Premise validation buried inside a 400-word classification prompt | Premise extraction is Step 1 (one focused call), causal claim extraction is Step 2, verification against full vault note texts is Step 3 |
| Classification asked for 6 lens descriptions + gaps + reasoning in one call | Classification is Step 4 (one word output), lens selection is Step 5 (comma-separated list) |
| Synthesis used big LLM cartridge | Synthesis is pure code assembly from lens outputs — no LLM needed |
| Per-premise keyword vault_search missed semantic matches (e.g. "Python backend" couldn't find main.py) | Step 3 does ONE vault_search for the problem, vault_read_note for full text, then LLM matches claims against full note texts — LLM is pattern matcher, vault is knowledge |
| Causal chains (slippery slopes, false causes) not detected | Step 2 extracts "X leads to Y" / "if X then Y" claims alongside factual premises |

## Design Principle

**The LLM never sees procedure terminology.** Words like "ontology," "epistemology," "hermeneutics," "lens stack," and "knowledge triad" exist only in code comments and step headers. The LLM prompts are plain English: "What kind of problem is this?" not "Classify this problem according to the Knowledge Triad ontology layer."

## Inputs

- `problem`: The problem or question to reason about
- `context`: Additional context (optional)
- `lens_override`: Explicit lens override (optional, comma-separated)

## Outputs

- Premise warnings (BS detector results)
- Problem classification
- Lens analyses with vault provenance
- Synthesized conclusion

---

### Step 1: Extract factual premises (BS detector — part 1)

Extract every factual claim the problem assumes is true. This is the BS detector's first pass — it identifies what the problem is asserting without evidence. The LLM gets ONE job: list the factual claims. Triple-try for consistency.

```python
problem = args.get('problem', '')
context = args.get('context', '')
lens_override = args.get('lens_override', '')

# Triple-try premise extraction — one focused question
premises_all = []
for _ in range(3):
    prompt = f"List every factual claim this sentence assumes is true. One per line. If none, say NONE.\n\n\"{problem}\""
    resp = llm_generate(prompt).strip()
    premises_all.append(resp)

# Parse premises from each response — take union of claims found in >=2 responses
from collections import Counter
premise_votes = Counter()
for resp in premises_all:
    for line in resp.split('\n'):
        line = line.strip()
        if line and line.upper() != 'NONE' and len(line) > 5:
            premise_votes[line.lower()] += 1

premises = [p for p, count in premise_votes.items() if count >= 2]
if not premises:
    # Fallback: take all unique from first response
    seen = set()
    for line in premises_all[0].split('\n'):
        line = line.strip()
        if line and line.upper() != 'NONE' and len(line) > 5:
            key = line.lower()
            if key not in seen:
                seen.add(key)
                premises.append(line)

result = f"PREMISES: {'|||'.join(premises) if premises else 'NONE'}\nPROBLEM: {problem}"
if context:
    result += f"\nCONTEXT: {context}"
if lens_override:
    result += f"\nLENS_OVERRIDE: {lens_override}"
print(result)
```

[validate: contains "PREMISES:"]
[validate: contains "PROBLEM:"]

---

### Step 2: Extract causal claims (BS detector — part 2)

Extract any causal claims the problem makes — "X leads to Y," "if X then Y," "X causes Y." These are structural claims about relationships, not factual claims about entities. The LLM gets ONE job: list the causal claims. Triple-try for consistency.

```python
# Parse Step 1
lines = output.strip().split('\n')
problem = ''
premises_str = ''
context = ''
lens_override = ''
for line in lines:
    if line.startswith('PROBLEM: '):
        problem = line.replace('PROBLEM: ', '').strip()
    elif line.startswith('PREMISES: '):
        premises_str = line.replace('PREMISES: ', '').strip()
    elif line.startswith('CONTEXT: '):
        context = line.replace('CONTEXT: ', '').strip()
    elif line.startswith('LENS_OVERRIDE: '):
        lens_override = line.replace('LENS_OVERRIDE: ', '').strip()

# Triple-try causal claim extraction
causal_all = []
for _ in range(3):
    prompt = f"Does this sentence claim that one thing causes or leads to another? List any 'if X then Y' or 'X leads to Y' claims. One per line. If none, say NONE.\n\n\"{problem}\""
    resp = llm_generate(prompt).strip()
    causal_all.append(resp)

# Parse causal claims — take union found in >=2 responses
from collections import Counter
causal_votes = Counter()
for resp in causal_all:
    for line in resp.split('\n'):
        line = line.strip()
        if line and line.upper() != 'NONE' and len(line) > 5:
            causal_votes[line.lower()] += 1

causal_claims = [c for c, count in causal_votes.items() if count >= 2]
if not causal_claims:
    seen = set()
    for line in causal_all[0].split('\n'):
        line = line.strip()
        if line and line.upper() != 'NONE' and len(line) > 5:
            key = line.lower()
            if key not in seen:
                seen.add(key)
                causal_claims.append(line)

# Merge causal claims into premises for verification
factual_premises = [p.strip() for p in premises_str.split('|||') if p.strip() and p.strip() != 'NONE']
all_claims = factual_premises + causal_claims

result = f"PREMISES: {'|||'.join(factual_premises) if factual_premises else 'NONE'}\nCAUSAL_CLAIMS: {'|||'.join(causal_claims) if causal_claims else 'NONE'}\nALL_CLAIMS: {'|||'.join(all_claims)}\nPROBLEM: {problem}"
if context:
    result += f"\nCONTEXT: {context}"
if lens_override:
    result += f"\nLENS_OVERRIDE: {lens_override}"
print(result)
```

[validate: contains "PREMISES:"]
[validate: contains "CAUSAL_CLAIMS:"]

---

### Step 3: Verify all claims against full vault note texts (BS detector — part 3)

Instead of per-claim keyword search (which fails on semantic matches), do ONE good vault_search for the problem, vault_read_note the top results to get FULL text, then ask the LLM to check each claim against those full note texts. The LLM does semantic MATCHING (does this text support this claim?) — the knowledge is IN the vault note, not in the LLM's weights. The LLM is a pattern matcher, not a knowledge base.

```python
# Parse Step 2
lines = output.strip().split('\n')
problem = ''
all_claims_str = ''
context = ''
lens_override = ''
for line in lines:
    if line.startswith('PROBLEM: '):
        problem = line.replace('PROBLEM: ', '').strip()
    elif line.startswith('ALL_CLAIMS: '):
        all_claims_str = line.replace('ALL_CLAIMS: ', '').strip()
    elif line.startswith('CONTEXT: '):
        context = line.replace('CONTEXT: ', '').strip()
    elif line.startswith('LENS_OVERRIDE: '):
        lens_override = line.replace('LENS_OVERRIDE: ', '').strip()

all_claims = [c.strip() for c in all_claims_str.split('|||') if c.strip() and c.strip() != 'NONE']

# ONE good vault search for the problem, then read full note texts
vault_results = vault_search(problem, k=5)
full_docs = []
for r in vault_results:
    title = r.get('name', r.get('file_path', ''))
    if not title:
        continue
    try:
        doc_text = vault_read_note(title, max_lines=0)
        if isinstance(doc_text, dict):
            doc_text = doc_text.get('content', '')
        if doc_text and len(str(doc_text)) > 50:
            full_docs.append({'title': title, 'text': str(doc_text)[:3000]})
    except Exception:
        pass

# If vault_read_note found nothing, fall back to vault_search titles only
if not full_docs:
    for r in vault_results:
        title = r.get('name', r.get('file_path', ''))
        if title:
            full_docs.append({'title': title, 'text': f"[[{title}]] (full text unavailable)"})

premise_warnings = []
premise_verified = []

for claim in all_claims:
    if not full_docs:
        premise_warnings.append(f"UNVERIFIED: {claim} (no vault docs found for problem)")
        continue

    # Ask LLM to check claim against each full doc text
    # The LLM reads the vault note and checks if it supports the claim.
    # This is semantic MATCHING, not knowledge retrieval.
    from collections import Counter
    all_votes = []
    for doc in full_docs[:3]:  # check against top 3 docs
        for _ in range(3):  # triple-try per doc
            prompt = f"Read this vault note. Does it support this claim? Answer YES, NO, or NEUTRAL.\n\nClaim: {claim}\n\nVault note:\n{doc['text'][:2000]}"
            resp = llm_generate(prompt).strip().upper()
            for word in resp.split():
                word = word.rstrip('.,!;')
                if word in ('YES', 'NO', 'NEUTRAL'):
                    all_votes.append(word)
                    break

    if all_votes:
        verdict = Counter(all_votes).most_common(1)[0][0]
    else:
        verdict = 'NEUTRAL'

    doc_titles = ' | '.join([d['title'] for d in full_docs[:3]])
    if verdict in ('NO', 'NEUTRAL'):
        premise_warnings.append(f"UNVERIFIED: {claim} (verdict: {verdict}, checked against: {doc_titles})")
    else:
        premise_verified.append(f"VERIFIED: {claim} (supported by: {doc_titles})")

warnings_str = ' ||| '.join(premise_warnings) if premise_warnings else 'ALL_VERIFIED'
verified_str = ' ||| '.join(premise_verified) if premise_verified else 'NONE_VERIFIED'

result = f"PREMISE_WARNINGS: {warnings_str}\nPREMISE_VERIFIED: {verified_str}\nPROBLEM: {problem}"
if context:
    result += f"\nCONTEXT: {context}"
if lens_override:
    result += f"\nLENS_OVERRIDE: {lens_override}"
print(result)
```

[validate: contains "PREMISE_WARNINGS:"]

---

### Step 4: Classify the problem type

Ask the LLM one simple question: what kind of problem is this? Single word output from a short list. Triple-try with majority vote.

```python
# Parse Step 3
lines = output.strip().split('\n')
problem = ''
context = ''
lens_override = ''
premise_warnings = ''
for line in lines:
    if line.startswith('PROBLEM: '):
        problem = line.replace('PROBLEM: ', '').strip()
    elif line.startswith('CONTEXT: '):
        context = line.replace('CONTEXT: ', '').strip()
    elif line.startswith('LENS_OVERRIDE: '):
        lens_override = line.replace('LENS_OVERRIDE: ', '').strip()
    elif line.startswith('PREMISE_WARNINGS: '):
        premise_warnings = line.replace('PREMISE_WARNINGS: ', '').strip()

# Triple-try classification — one word from a short list
valid_types = ['WHY', 'CHOOSE', 'EXPLAIN', 'BUILD', 'EVALUATE', 'VERIFY']
from collections import Counter
votes = []
for _ in range(3):
    prompt = f"What kind of question is this? Pick ONE word:\nWHY (something broken/failing)\nCHOOSE (pick between options)\nEXPLAIN (understand how something works)\nBUILD (design or plan steps)\nEVALUATE (multiple viewpoints)\nVERIFY (check if a claim is true)\n\nQuestion: {problem}"
    resp = llm_generate(prompt).strip().upper()
    # Extract first matching word
    for word in resp.split():
        word = word.rstrip('.,!;')
        if word in valid_types:
            votes.append(word)
            break

if votes:
    problem_type = Counter(votes).most_common(1)[0][0]
else:
    problem_type = 'EXPLAIN'  # safe default

result = f"PROBLEM_TYPE: {problem_type}\nPROBLEM: {problem}\nPREMISE_WARNINGS: {premise_warnings}"
if context:
    result += f"\nCONTEXT: {context}"
if lens_override:
    result += f"\nLENS_OVERRIDE: {lens_override}"
print(result)
```

[validate: contains "PROBLEM_TYPE:"]

---

### Step 5: Select lenses

Map the problem type to a default lens stack, then ask the LLM if any additional lenses would help. The LLM only suggests additions — the code handles the mapping.

```python
# Parse Step 4
lines = output.strip().split('\n')
problem = ''
problem_type = ''
context = ''
lens_override = ''
premise_warnings = ''
for line in lines:
    if line.startswith('PROBLEM: '):
        problem = line.replace('PROBLEM: ', '').strip()
    elif line.startswith('PROBLEM_TYPE: '):
        problem_type = line.replace('PROBLEM_TYPE: ', '').strip()
    elif line.startswith('CONTEXT: '):
        context = line.replace('CONTEXT: ', '').strip()
    elif line.startswith('LENS_OVERRIDE: '):
        lens_override = line.replace('LENS_OVERRIDE: ', '').strip()
    elif line.startswith('PREMISE_WARNINGS: '):
        premise_warnings = line.replace('PREMISE_WARNINGS: ', '').strip()

# Deterministic mapping from problem type to default lens
type_to_lens = {
    'WHY': ['Root-Cause-Analysis'],
    'CHOOSE': ['Trade-off-Analysis'],
    'EXPLAIN': ['Systematic-Inquiry'],
    'BUILD': ['Decomposition'],
    'EVALUATE': ['Multi-Perspective'],
    'VERIFY': ['Evidence-Weighing'],
}
default_lenses = type_to_lens.get(problem_type, ['Systematic-Inquiry'])

# If override provided, use it
if lens_override:
    lens_stack = [l.strip() for l in lens_override.split(',') if l.strip()]
else:
    lens_stack = list(default_lenses)

    # Ask LLM: should we add another lens? Simple yes/no + which one
    available = ['Root-Cause-Analysis', 'Trade-off-Analysis', 'Systematic-Inquiry', 'Decomposition', 'Multi-Perspective', 'Evidence-Weighing']
    unused = [l for l in available if l not in lens_stack]

    if unused:
        prompt = f"Question: {problem}\n\nAlready using: {', '.join(lens_stack)}\n\nWould another approach help? If yes, which ONE from: {', '.join(unused)}. If no, say NO.\n\nAnswer with just the lens name or NO."
        resp = llm_generate(prompt).strip()

        # Check if response matches an unused lens name
        resp_clean = resp.strip().upper()
        for lens in unused:
            if lens.upper() == resp_clean or resp_clean.startswith(lens.upper()):
                lens_stack.append(lens)
                break

result = f"LENS_STACK: {','.join(lens_stack)}\nPROBLEM_TYPE: {problem_type}\nPROBLEM: {problem}\nPREMISE_WARNINGS: {premise_warnings}"
if context:
    result += f"\nCONTEXT: {context}"
print(result)
```

[validate: contains "LENS_STACK:"]

---

### Step 6: Dispatch to lenses

Run each lens in order. Before each lens, do a vault search for relevant docs. Pass premise warnings as context so lenses know which claims are unverified. This is pure code — no LLM calls.

```python
# Parse Step 5
lines = output.strip().split('\n')
problem = ''
problem_type = ''
lens_stack_str = ''
context = ''
premise_warnings = ''
for line in lines:
    if line.startswith('PROBLEM: '):
        problem = line.replace('PROBLEM: ', '').strip()
    elif line.startswith('PROBLEM_TYPE: '):
        problem_type = line.replace('PROBLEM_TYPE: ', '').strip()
    elif line.startswith('LENS_STACK: '):
        lens_stack_str = line.replace('LENS_STACK: ', '').strip()
    elif line.startswith('CONTEXT: '):
        context = line.replace('CONTEXT: ', '').strip()
    elif line.startswith('PREMISE_WARNINGS: '):
        premise_warnings = line.replace('PREMISE_WARNINGS: ', '').strip()

lens_queue = [l.strip() for l in lens_stack_str.split(',') if l.strip()]
completed = []
lens_outputs = {}
all_docs = []

for lens_name in lens_queue:
    # Vault retrieval for this lens
    search_query = f"{problem} {lens_name.replace('-', ' ')}"
    vault_results = vault_search(search_query, k=3)
    lens_docs = []
    for r in vault_results:
        title = r.get('name', r.get('file_path', ''))
        doc_ref = f"[[{title}]]"
        if doc_ref not in all_docs:
            all_docs.append(doc_ref)
        lens_docs.append(doc_ref)

    # Build context for the lens
    lens_context = context or ''
    if premise_warnings and premise_warnings != 'ALL_VERIFIED':
        lens_context = (lens_context + '\n' if lens_context else '') + f"NOTE - unverified claims in the problem: {premise_warnings}"
    if lens_docs:
        lens_context = (lens_context + '\n' if lens_context else '') + f"Relevant vault notes: {' | '.join(lens_docs)}"

    # Run the lens
    try:
        lens_args = {'problem': problem, 'context': lens_context}
        lens_result = run_procedure(lens_name, args=lens_args)
        if isinstance(lens_result, dict):
            lens_text = lens_result.get('final_output', str(lens_result))
        else:
            lens_text = str(lens_result)
        lens_outputs[lens_name] = lens_text[:2000]  # cap for memory
    except Exception as e:
        lens_outputs[lens_name] = f"ERROR: {str(e)}"

    completed.append(lens_name)

# Format output
result = f"PROBLEM: {problem}\nLENSES_RUN: {','.join(completed)}\nVAULT_DOCS: {' | '.join(all_docs)}\nPREMISE_WARNINGS: {premise_warnings}\n"
for name, out in lens_outputs.items():
    # Escape newlines in lens output for single-line storage
    escaped = out.replace('\n', '\\n')
    result += f"LENS_OUTPUT: {name}::: {escaped}\n"
print(result)
```

[validate: contains "LENSES_RUN:"]
[validate: contains "LENS_OUTPUT:"]

---

### Step 7: Check if more lenses are needed

After all lenses run, ask the LLM one question: does the analysis need another pass? Simple yes/no. If yes, which lens? Then dispatch it.

```python
# Parse Step 6
lines = output.strip().split('\n')
problem = ''
completed_str = ''
premise_warnings = ''
lens_data = {}
for line in lines:
    if line.startswith('PROBLEM: '):
        problem = line.replace('PROBLEM: ', '').strip()
    elif line.startswith('LENSES_RUN: '):
        completed_str = line.replace('LENSES_RUN: ', '').strip()
    elif line.startswith('PREMISE_WARNINGS: '):
        premise_warnings = line.replace('PREMISE_WARNINGS: ', '').strip()
    elif line.startswith('LENS_OUTPUT: '):
        rest = line.replace('LENS_OUTPUT: ', '').strip()
        if '::: ' in rest:
            name, out = rest.split('::: ', 1)
            lens_data[name] = out.replace('\\n', '\n')

completed = [c.strip() for c in completed_str.split(',') if c.strip()]
available = ['Root-Cause-Analysis', 'Trade-off-Analysis', 'Systematic-Inquiry', 'Decomposition', 'Multi-Perspective', 'Evidence-Weighing']
unused = [l for l in available if l not in completed]

extra_lenses = []
if unused:
    # Build a short summary of what each lens found
    summaries = []
    for name, out in lens_data.items():
        # Take first 200 chars as summary
        summaries.append(f"{name}: {out[:200]}")

    prompt = f"Question: {problem}\n\nAnalysis so far:\n{chr(10).join(summaries)}\n\nNeed another approach? If yes, pick ONE from: {', '.join(unused)}. If no, say NO.\n\nAnswer with just the lens name or NO."
    resp = llm_generate(prompt).strip()

    resp_clean = resp.strip().upper()
    for lens in unused:
        if lens.upper() == resp_clean or resp_clean.startswith(lens.upper()):
            extra_lenses.append(lens)
            break

# Run extra lenses
for lens_name in extra_lenses:
    vault_results = vault_search(f"{problem} {lens_name.replace('-', ' ')}", k=3)
    lens_docs = []
    for r in vault_results:
        title = r.get('name', r.get('file_path', ''))
        lens_docs.append(f"[[{title}]]")

    lens_context = ''
    if premise_warnings and premise_warnings != 'ALL_VERIFIED':
        lens_context = f"NOTE - unverified claims: {premise_warnings}"
    if lens_docs:
        lens_context = (lens_context + '\n' if lens_context else '') + f"Relevant notes: {' | '.join(lens_docs)}"

    try:
        lens_args = {'problem': problem, 'context': lens_context}
        lens_result = run_procedure(lens_name, args=lens_args)
        if isinstance(lens_result, dict):
            lens_text = lens_result.get('final_output', str(lens_result))
        else:
            lens_text = str(lens_result)
        lens_data[lens_name] = lens_text[:2000]
        completed.append(lens_name)
    except Exception as e:
        lens_data[lens_name] = f"ERROR: {str(e)}"
        completed.append(lens_name)

# Rebuild output with all lens data
result = f"PROBLEM: {problem}\nLENSES_RUN: {','.join(completed)}\nPREMISE_WARNINGS: {premise_warnings}\n"
for name, out in lens_data.items():
    escaped = out.replace('\n', '\\n')
    result += f"LENS_OUTPUT: {name}::: {escaped}\n"
print(result)
```

[validate: contains "LENSES_RUN:"]

---

### Step 8: Synthesize conclusion

Pure code assembly — no LLM needed. Extract key findings from each lens output, list premise warnings, state what lenses were applied, and provide the raw lens outputs for the caller to use.

```python
# Parse all accumulated data
lines = output.strip().split('\n')
problem = ''
completed_str = ''
premise_warnings = ''
lens_data = {}
for line in lines:
    if line.startswith('PROBLEM: '):
        problem = line.replace('PROBLEM: ', '').strip()
    elif line.startswith('LENSES_RUN: '):
        completed_str = line.replace('LENSES_RUN: ', '').strip()
    elif line.startswith('PREMISE_WARNINGS: '):
        premise_warnings = line.replace('PREMISE_WARNINGS: ', '').strip()
    elif line.startswith('LENS_OUTPUT: '):
        rest = line.replace('LENS_OUTPUT: ', '').strip()
        if '::: ' in rest:
            name, out = rest.split('::: ', 1)
            lens_data[name] = out.replace('\\n', '\n')

completed = [c.strip() for c in completed_str.split(',') if c.strip()]

# Build synthesis
synth_lines = []
synth_lines.append(f"## Think v3 Analysis: {problem}")
synth_lines.append("")

# Premise warnings first — BS detector results
if premise_warnings and premise_warnings != 'ALL_VERIFIED':
    synth_lines.append("### BS Detector: Unverified Claims")
    for w in premise_warnings.split('|||'):
        w = w.strip()
        if w:
            synth_lines.append(f"- {w}")
    synth_lines.append("")
else:
    synth_lines.append("### BS Detector: All premises verified against vault")
    synth_lines.append("")

# Lens results
synth_lines.append(f"### Analysis (lenses applied: {', '.join(completed)})")
synth_lines.append("")
for name in completed:
    out = lens_data.get(name, 'No output')
    synth_lines.append(f"#### {name}")
    synth_lines.append(out)
    synth_lines.append("")

# Confidence note
if premise_warnings and premise_warnings != 'ALL_VERIFIED':
    synth_lines.append("### Confidence: MEDIUM")
    synth_lines.append("Some premises could not be verified against vault documents. Findings may rest on unverified assumptions.")
else:
    synth_lines.append("### Confidence: HIGH")
    synth_lines.append("All premises verified against vault documents.")

synthesis = '\n'.join(synth_lines)
print(synthesis)
```

[validate: contains "Think v3 Analysis"]
[validate: contains "BS Detector"]

---

## Research Justification

1. **Bite-sized prompts for small models**: Research shows small models (<3B parameters) perform dramatically better when each LLM call asks exactly one question with constrained output format. The v2 Think procedure asked the 0.8B model to process 400+ word prompts with 6 lens descriptions, triple-try formatting, and premise extraction all at once — the model couldn't hold it in working attention. v3 breaks every LLM call into 1-3 sentence prompts with single-word or short-list outputs.

2. **Procedure terminology isolation**: When the LLM sees its own procedure description in the prompt, it pattern-matches on that description rather than processing the actual problem. v3 ensures the LLM never sees words like "ontology," "epistemology," "hermeneutics," or "lens stack" — those are code-level concepts only.

3. **Triple-try consistency** ([[Deterministic-Scaffolding-for-Small-Models]]): Critical LLM calls (premise extraction, classification, lens selection) run 3 times with majority vote. This catches the small model's inconsistency without complex heuristics.

4. **Deterministic fallbacks**: Every LLM call has a code-level fallback. If the model produces garbage, the procedure continues with a safe default rather than hallucinating.

5. **VibeThinker-3B precedent** ([[VibeThinker-3B-small-LLM-beats-DeepSeek-GPT-GLM-benchmarks-performance_20260731-233419]]): A 3B model achieved frontier-level reasoning through structured post-training. The Parametric Compression-Coverage Hypothesis suggests verifiable reasoning can be compressed into small models when the reasoning structure is provided externally — which is exactly what this procedure does.

## Related

- [[Deterministic-Scaffolding-for-Small-Models]]
- [[VibeThinker-3B-small-LLM-beats-DeepSeek-GPT-GLM-benchmarks-performance_20260731-233419]]
- [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]]
- All lens procedures: [[Root-Cause-Analysis]], [[Trade-off-Analysis]], [[Systematic-Inquiry]], [[Decomposition]], [[Multi-Perspective]], [[Evidence-Weighing]]
