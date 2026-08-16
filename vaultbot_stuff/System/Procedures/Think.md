---
type: procedure
status: verified
baseline: true
model_cartridge: small
created: 2026-08-10
summary: "Parent reasoning procedure v4: redesigned for ~4B small model (qwen3.5:4b). Merged steps 1+2, batched verification, killed triple-try, combined classify+select, conditional step 5, single-call synthesis. ~4-6 LLM calls in parent (down from 20-28)."
description: "BS detector + problem classification + lens dispatch + synthesis. v4: optimized for 4B model — batched calls, no triple-try, richer prompts."
allowed_tools:
  - vault_search
  - vault_read_note
  - llm_generate
  - run_procedure
  - vault_research
when_to_use: "When you need to reason through a complex problem and the vault doesn't have a direct answer. When you need structured analysis with premise verification, lens-based reasoning, and synthesis. When someone asks 'what do you think about X' or 'help me reason through Y'. When you need to break down a question, check assumptions against the vault, apply multiple analytical lenses, and synthesize a conclusion. When a problem needs more than a simple lookup \u2014 it needs actual reasoning. Also use when someone asks to 'think about' something, when you need to evaluate options, when you need to find root causes, when you need to verify a claim, or when you need to decompose a complex task."
falsifiable_if: "The procedure produces a conclusion that contradicts verified vault evidence, or the premise gate blocks a question that has verifiable premises, or the lens dispatch produces no useful analysis"
applies_to:
  - reasoning
  - analysis
  - problem-solving
  - decision-making
  - root-cause
  - trade-offs
  - verification
  - decomposition
  - multi-perspective
  - synthesis
tags: [procedure, reasoning, think, v4, code-steps, 4b-model, batched]
success_count: 77
failure_count: 0
success_rate: 1.0
---

# Think: Structured Reasoning Scaffold (v4)

## Purpose

This procedure makes a 4B local model reason like a frontier model by following deterministic code steps with focused LLM calls. The 4B model can handle richer prompts, batched verification, and combined tasks — so v4 merges over-split steps, kills triple-try, and gives the model enough context to make real semantic judgments.

## What Changed in v4

| Problem in v3 (0.8B-era) | Fix in v4 (4B-era) |
|---|---|
| Steps 1+2 split (factual vs causal claims) — 2 calls | Merged into 1 call — 4B extracts both in one pass |
| Step 3 verified each claim × each doc individually — up to 15-21 calls | Batched into 1 call — 4B handles all claims against all docs at once |
| Triple-try on critical calls — 3× LLM round-trips | Killed entirely — 4B is consistent across tests |
| Classification (Step 4) + lens selection (Step 5) — 2 calls | Merged into 1 call — 4B can classify AND suggest lenses |
| Step 7 always runs "need more lenses?" — 1 call every time | Conditional — only runs if lens outputs are thin/errored |
| Synthesis: per-lens extraction (N calls) + final synthesis (1 call) | Single call — 4B handles all lens outputs at once |
| Prompts were 1-2 sentences, single-token output | Richer prompts — 3-4 sentences, short-phrase output |

## Design Principle

**The LLM never sees procedure terminology.** Words like "ontology," "epistemology," "hermeneutics," "lens stack," and "knowledge triad" exist only in code comments and step headers. The LLM prompts are plain English.

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

### Step 1: Extract all claims (factual + causal)

Extract every factual claim AND every causal claim the problem makes. The 4B can handle both in one call — no need to split into separate steps like v3.

```python
problem = args.get('problem', '')
context = args.get('context', '')
lens_override = args.get('lens_override', '')

# Single call: extract both factual and causal claims
prompt = f"""Analyze this question and list:
1. Every factual claim it assumes is true (things it takes for granted)
2. Every causal claim it makes (X leads to Y, if X then Y, X causes Y)

Format:
FACTUAL:
- [claim 1]
- [claim 2]
CAUSAL:
- [claim 1]

If none of a type, write NONE.

Question: "{problem}\""""

resp = llm_generate(prompt).strip()

# Parse factual and causal claims
factual = []
causal = []
current_section = None
for line in resp.split('\n'):
    line = line.strip()
    if line.upper().startswith('FACTUAL:'):
        current_section = 'factual'
        continue
    elif line.upper().startswith('CAUSAL:'):
        current_section = 'causal'
        continue
    elif line.startswith('- '):
        claim = line[2:].strip()
        if claim and claim.upper() != 'NONE' and len(claim) > 3:
            if current_section == 'factual':
                factual.append(claim)
            elif current_section == 'causal':
                causal.append(claim)

all_claims = factual + causal

result = f"FACTUAL: {'|||'.join(factual) if factual else 'NONE'}\nCAUSAL: {'|||'.join(causal) if causal else 'NONE'}\nALL_CLAIMS: {'|||'.join(all_claims) if all_claims else 'NONE'}\nPROBLEM: {problem}"
if context:
    result += f"\nCONTEXT: {context}"
if lens_override:
    result += f"\nLENS_OVERRIDE: {lens_override}"
print(result)
```

[validate: contains "FACTUAL:"]
[validate: contains "CAUSAL:"]
[validate: contains "PROBLEM:"]

---

### Step 2: Verify all claims against vault (batched)

One vault_search, read top notes, then ONE LLM call to verify ALL claims against ALL docs. The 4B can handle "here are N claims and M docs, give me a verdict for each" in a single call. Research gate triggers if all claims are unverified.

```python
# Parse Step 1
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

# ONE vault search, read top 5 notes
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

if not full_docs:
    for r in vault_results:
        title = r.get('name', r.get('file_path', ''))
        if title:
            full_docs.append({'title': title, 'text': f"[[{title}]] (full text unavailable)"})

premise_warnings = []
premise_verified = []

if not all_claims:
    # No claims to verify — open the gate
    premise_gate = 'OPEN'
    warnings_str = 'NO_CLAIMS'
    verified_str = 'NONE_VERIFIED'
elif not full_docs:
    # No vault docs found
    for claim in all_claims:
        premise_warnings.append(f"UNVERIFIED: {claim} (no vault docs found)")
    premise_gate = 'BLOCKED'
    warnings_str = ' ||| '.join(premise_warnings)
    verified_str = 'NONE_VERIFIED'
else:
    # BATCHED verification: all claims against all docs in one call
    claims_list = '\n'.join(f"{i+1}. {c}" for i, c in enumerate(all_claims))
    docs_text = '\n\n'.join(f"--- Note: {d['title']} ---\n{d['text'][:2000]}" for d in full_docs[:3])

    prompt = f"""Read these vault notes. For each claim, does any note support it, contradict it, or is it neutral?

Claims:
{claims_list}

Vault notes:
{docs_text}

Format (one line per claim):
CLAIM 1: YES/NO/NEUTRAL - [brief reason]"""

    resp = llm_generate(prompt).strip()

    # Parse verdicts
    for i, claim in enumerate(all_claims):
        verdict = 'NEUTRAL'
        reason = ''
        for line in resp.split('\n'):
            line = line.strip()
            if line.startswith(f'CLAIM {i+1}:'):
                rest = line.replace(f'CLAIM {i+1}:', '').strip()
                for v in ['YES', 'NO', 'NEUTRAL']:
                    if rest.upper().startswith(v):
                        verdict = v
                        reason = rest[len(v):].lstrip(' -:- ').strip()
                        break
                break

        doc_titles = ' | '.join([d['title'] for d in full_docs[:3]])
        if verdict == 'YES':
            premise_verified.append(f"VERIFIED: {claim} (supported by: {doc_titles})")
        elif verdict == 'NO':
            premise_warnings.append(f"CONTRADICTED: {claim} (verdict: NO, reason: {reason}, checked against: {doc_titles})")
        else:
            premise_warnings.append(f"UNVERIFIED: {claim} (verdict: NEUTRAL, checked against: {doc_titles})")

    # Research gate: if ALL claims are unverified, check relevance and maybe research
    if not premise_verified and premise_warnings and full_docs:
        doc_snippets = '\n'.join([d['text'][:500] for d in full_docs[:3]])
        relevance_prompt = f"Problem: {problem}\n\nVault docs found:\n{doc_snippets}\n\nAre these docs relevant to the problem? Answer YES or NO."
        relevance_resp = llm_generate(relevance_prompt).strip().upper()
        docs_irrelevant = 'NO' in relevance_resp

        if docs_irrelevant or len(full_docs) < 2:
            try:
                research_result = vault_research(problem, depth='deep')
                if research_result and 'error' not in research_result:
                    research_synthesis = research_result.get('synthesis', '')
                    research_note = research_result.get('note_path', '')
                    if research_synthesis:
                        # Batched re-verification against research
                        claims_list = '\n'.join(f"{i+1}. {c}" for i, c in enumerate(all_claims))
                        recheck_prompt = f"""Read this research. For each claim, does the research support it, contradict it, or is it neutral?

Claims:
{claims_list}

Research:
{research_synthesis[:2000]}

Format (one line per claim):
CLAIM 1: YES/NO/NEUTRAL - [brief reason]"""

                        recheck_resp = llm_generate(recheck_prompt).strip()

                        premise_warnings = []
                        premise_verified = []
                        for i, claim in enumerate(all_claims):
                            recheck_verdict = 'NEUTRAL'
                            for line in recheck_resp.split('\n'):
                                line = line.strip()
                                if line.startswith(f'CLAIM {i+1}:'):
                                    rest = line.replace(f'CLAIM {i+1}:', '').strip()
                                    for v in ['YES', 'NO', 'NEUTRAL']:
                                        if rest.upper().startswith(v):
                                            recheck_verdict = v
                                            break
                                    break

                            if recheck_verdict == 'YES':
                                premise_verified.append(f"VERIFIED: {claim} (supported by vault_research: {research_note})")
                            else:
                                premise_warnings.append(f"UNVERIFIED: {claim} (researched, verdict: {recheck_verdict}, note: {research_note})")
            except Exception:
                pass  # Research failed — keep original UNVERIFIED status

    premise_gate = 'BLOCKED' if (not premise_verified and premise_warnings) else 'OPEN'
    warnings_str = ' ||| '.join(premise_warnings) if premise_warnings else 'ALL_VERIFIED'
    verified_str = ' ||| '.join(premise_verified) if premise_verified else 'NONE_VERIFIED'

result = f"PREMISE_WARNINGS: {warnings_str}\nPREMISE_VERIFIED: {verified_str}\nPREMISE_GATE: {premise_gate}\nPROBLEM: {problem}"
if context:
    result += f"\nCONTEXT: {context}"
if lens_override:
    result += f"\nLENS_OVERRIDE: {lens_override}"
print(result)
```

[validate: contains "PREMISE_WARNINGS:"]

---

### Step 3: Classify + select lenses

The 4B can classify the problem type AND suggest additional lenses in one call. No need for separate steps like v3.

```python
# Parse Step 2
lines = output.strip().split('\n')
problem = ''
context = ''
lens_override = ''
premise_warnings = ''
premise_gate = ''
for line in lines:
    if line.startswith('PROBLEM: '):
        problem = line.replace('PROBLEM: ', '').strip()
    elif line.startswith('CONTEXT: '):
        context = line.replace('CONTEXT: ', '').strip()
    elif line.startswith('LENS_OVERRIDE: '):
        lens_override = line.replace('LENS_OVERRIDE: ', '').strip()
    elif line.startswith('PREMISE_WARNINGS: '):
        premise_warnings = line.replace('PREMISE_WARNINGS: ', '').strip()
    elif line.startswith('PREMISE_GATE: '):
        premise_gate = line.replace('PREMISE_GATE: ', '').strip()

# Deterministic mapping from problem type to default lens
type_to_lens = {
    'WHY': ['Root-Cause-Analysis'],
    'CHOOSE': ['Trade-off-Analysis'],
    'EXPLAIN': ['Systematic-Inquiry'],
    'BUILD': ['Decomposition'],
    'EVALUATE': ['Multi-Perspective'],
    'VERIFY': ['Evidence-Weighing'],
}

if lens_override:
    # Use override — no LLM call needed
    lens_stack = [l.strip() for l in lens_override.split(',') if l.strip()]
    problem_type = 'OVERRIDE'
else:
    # Single call: classify + suggest additional lens
    available = ['Root-Cause-Analysis', 'Trade-off-Analysis', 'Systematic-Inquiry', 'Decomposition', 'Multi-Perspective', 'Evidence-Weighing']

    prompt = f"""What kind of question is this? Pick ONE:
WHY (something broken/failing)
CHOOSE (pick between options)
EXPLAIN (understand how something works)
BUILD (design or plan steps)
EVALUATE (multiple viewpoints)
VERIFY (check if a claim is true)

Question: {problem}

Format:
TYPE: [your pick]
EXTRA_LENS: [name of another approach that would help, or NO]"""

    resp = llm_generate(prompt).strip()

    # Parse type
    problem_type = 'EXPLAIN'  # safe default
    for line in resp.split('\n'):
        if line.startswith('TYPE:'):
            type_val = line.replace('TYPE:', '').strip().upper()
            for t in ['WHY', 'CHOOSE', 'EXPLAIN', 'BUILD', 'EVALUATE', 'VERIFY']:
                if t in type_val:
                    problem_type = t
                    break
            break

    lens_stack = list(type_to_lens.get(problem_type, ['Systematic-Inquiry']))

    # Parse extra lens suggestion
    for line in resp.split('\n'):
        if line.startswith('EXTRA_LENS:'):
            extra = line.replace('EXTRA_LENS:', '').strip()
            extra_clean = extra.upper()
            if extra_clean != 'NO' and extra_clean:
                for lens in available:
                    if lens not in lens_stack and (lens.upper() == extra_clean or extra_clean.startswith(lens.upper())):
                        lens_stack.append(lens)
                        break
            break

result = f"PROBLEM_TYPE: {problem_type}\nLENS_STACK: {','.join(lens_stack)}\nPROBLEM: {problem}\nPREMISE_WARNINGS: {premise_warnings}\nPREMISE_GATE: {premise_gate}"
if context:
    result += f"\nCONTEXT: {context}"
print(result)
```

[validate: contains "PROBLEM_TYPE:"]
[validate: contains "LENS_STACK:"]

---

### Step 4: Dispatch to lenses

Run each lens in order. Before each lens, do a vault search for relevant docs. Pass premise warnings as context. Pure code — no LLM calls.

```python
# Parse Step 3
lines = output.strip().split('\n')
problem = ''
problem_type = ''
lens_stack_str = ''
context = ''
premise_warnings = ''
premise_gate = ''
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
    elif line.startswith('PREMISE_GATE: '):
        premise_gate = line.replace('PREMISE_GATE: ', '').strip()

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
    if premise_warnings and premise_warnings not in ('ALL_VERIFIED', 'NO_CLAIMS'):
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
        lens_outputs[lens_name] = lens_text[:2000]
    except Exception as e:
        lens_outputs[lens_name] = f"ERROR: {str(e)}"

    completed.append(lens_name)

# Format output
result = f"PROBLEM: {problem}\nLENSES_RUN: {','.join(completed)}\nVAULT_DOCS: {' | '.join(all_docs)}\nPREMISE_WARNINGS: {premise_warnings}\nPREMISE_GATE: {premise_gate}\n"
for name, out in lens_outputs.items():
    escaped = out.replace('\n', '\\n')
    result += f"LENS_OUTPUT: {name}::: {escaped}\n"
print(result)
```

[validate: contains "LENSES_RUN:"]
[validate: contains "LENS_OUTPUT:"]

---

### Step 5: Conditional — more lenses needed?

Only runs if any lens output is thin (ERROR or <100 chars). If all lenses produced good output, skip this step entirely. Saves 1 LLM call + potential lens dispatch on most queries.

```python
# Parse Step 4
lines = output.strip().split('\n')
problem = ''
completed_str = ''
premise_warnings = ''
premise_gate = ''
lens_data = {}
for line in lines:
    if line.startswith('PROBLEM: '):
        problem = line.replace('PROBLEM: ', '').strip()
    elif line.startswith('LENSES_RUN: '):
        completed_str = line.replace('LENSES_RUN: ', '').strip()
    elif line.startswith('PREMISE_WARNINGS: '):
        premise_warnings = line.replace('PREMISE_WARNINGS: ', '').strip()
    elif line.startswith('PREMISE_GATE: '):
        premise_gate = line.replace('PREMISE_GATE: ', '').strip()
    elif line.startswith('LENS_OUTPUT: '):
        rest = line.replace('LENS_OUTPUT: ', '').strip()
        if '::: ' in rest:
            name, out = rest.split('::: ', 1)
            lens_data[name] = out.replace('\\n', '\n')

completed = [c.strip() for c in completed_str.split(',') if c.strip()]

# Check if any lens output is thin
needs_more = False
for name, out in lens_data.items():
    if out.startswith('ERROR') or len(out.strip()) < 100:
        needs_more = True
        break

available = ['Root-Cause-Analysis', 'Trade-off-Analysis', 'Systematic-Inquiry', 'Decomposition', 'Multi-Perspective', 'Evidence-Weighing']
unused = [l for l in available if l not in completed]

if needs_more and unused:
    # Build summaries of what we have
    summaries = []
    for name, out in lens_data.items():
        summaries.append(f"{name}: {out[:200]}")

    prompt = f"""Question: {problem}

Analysis so far:
{chr(10).join(summaries)}

The existing analysis is thin or errored. Would another approach help? Pick ONE from: {', '.join(unused)}. If no, say NO.

Answer with just the lens name or NO."""

    resp = llm_generate(prompt).strip()

    resp_clean = resp.strip().upper()
    for lens in unused:
        if lens.upper() == resp_clean or resp_clean.startswith(lens.upper()):
            # Run the extra lens
            vault_results = vault_search(f"{problem} {lens.replace('-', ' ')}", k=3)
            lens_docs = []
            for r in vault_results:
                title = r.get('name', r.get('file_path', ''))
                lens_docs.append(f"[[{title}]]")

            lens_context = ''
            if premise_warnings and premise_warnings not in ('ALL_VERIFIED', 'NO_CLAIMS'):
                lens_context = f"NOTE - unverified claims: {premise_warnings}"
            if lens_docs:
                lens_context = (lens_context + '\n' if lens_context else '') + f"Relevant notes: {' | '.join(lens_docs)}"

            try:
                lens_args = {'problem': problem, 'context': lens_context}
                lens_result = run_procedure(lens, args=lens_args)
                if isinstance(lens_result, dict):
                    lens_text = lens_result.get('final_output', str(lens_result))
                else:
                    lens_text = str(lens_result)
                lens_data[lens] = lens_text[:2000]
                completed.append(lens)
            except Exception as e:
                lens_data[lens] = f"ERROR: {str(e)}"
                completed.append(lens)
            break

# Rebuild output
result = f"PROBLEM: {problem}\nLENSES_RUN: {','.join(completed)}\nPREMISE_WARNINGS: {premise_warnings}\nPREMISE_GATE: {premise_gate}\n"
for name, out in lens_data.items():
    escaped = out.replace('\n', '\\n')
    result += f"LENS_OUTPUT: {name}::: {escaped}\n"
print(result)
```

[validate: contains "LENSES_RUN:"]

---

### Step 6: Synthesize conclusion

Single-call synthesis: the 4B can handle all lens outputs at once. No need for per-lens extraction + separate synthesis like v3. The 4B reads all lens outputs and produces a coherent answer in one pass.

```python
# Parse all accumulated data
lines = output.strip().split('\n')
problem = ''
completed_str = ''
premise_warnings = ''
premise_gate = ''
lens_data = {}
for line in lines:
    if line.startswith('PROBLEM: '):
        problem = line.replace('PROBLEM: ', '').strip()
    elif line.startswith('LENSES_RUN: '):
        completed_str = line.replace('LENSES_RUN: ', '').strip()
    elif line.startswith('PREMISE_WARNINGS: '):
        premise_warnings = line.replace('PREMISE_WARNINGS: ', '').strip()
    elif line.startswith('PREMISE_GATE: '):
        premise_gate = line.replace('PREMISE_GATE: ', '').strip()
    elif line.startswith('LENS_OUTPUT: '):
        rest = line.replace('LENS_OUTPUT: ', '').strip()
        if '::: ' in rest:
            name, out = rest.split('::: ', 1)
            lens_data[name] = out.replace('\\n', '\n')

completed = [c.strip() for c in completed_str.split(',') if c.strip()]

# Read user preference
import os
bs_detector_messages = os.environ.get('VAULTBOT_BS_DETECTOR_MESSAGES', 'true').lower() != 'false'

# --- PREMISE GATE: if all premises are unverified, refuse to fabricate ---
if premise_gate == 'BLOCKED':
    synth_lines = []
    synth_lines.append(f"## Think v4 Analysis: {problem}")
    synth_lines.append("")
    synth_lines.append("### [!!] Premise Gate: BLOCKED")
    synth_lines.append("")
    synth_lines.append("All factual premises in this question could not be verified against the vault. "
                       "The question may be based on false assumptions. Rather than fabricating an answer "
                       "about a system that may not exist, I'm stopping here.")
    synth_lines.append("")

    if bs_detector_messages:
        synth_lines.append("#### BS Detector: Unverified Claims")
        for w in premise_warnings.split('|||'):
            w = w.strip()
            if w:
                synth_lines.append(f"- {w}")
        synth_lines.append("")
    else:
        synth_lines.append("Here's what I checked:")
        for w in premise_warnings.split('|||'):
            w = w.strip()
            if w:
                synth_lines.append(f"- {w}")
        synth_lines.append("")

    synth_lines.append("If you believe this question is valid, could you rephrase it with verifiable claims? "
                       "I'm happy to research the topic from scratch.")
    synth_lines.append("")
    synth_lines.append("### Confidence: N/A (premise gate blocked)")
    synthesis = '\n'.join(synth_lines)
    print(synthesis)
else:
    # --- PREMISE GATE OPEN: single-call synthesis ---
    # Build lens outputs text (cap each at 1500 chars for context management)
    lens_texts = []
    for name in completed:
        out = lens_data.get(name, 'No output')
        snippet = out[:1500] if len(out) > 1500 else out
        lens_texts.append(f"## {name}\n{snippet}")

    lens_outputs_text = '\n\n'.join(lens_texts)

    synth_prompt = f"""Question: {problem}

Analysis from multiple approaches:
{lens_outputs_text}

Synthesize these into a coherent answer. Include:
1. Summary (2-3 sentences)
2. Key findings (bullet points, one per approach)
3. Confidence (HIGH/MEDIUM/LOW with reason)
4. If relevant, one recommended action

CRITICAL: Only use [[wikilinks]] that already appear in the analysis above. Do NOT invent new wikilinks. If you want to reference a concept, use plain text instead of a wikilink unless the exact wikilink appears above."""

    synthesis_body = llm_generate(synth_prompt).strip()

    if not synthesis_body or len(synthesis_body) < 30:
        # Fallback: assemble manually
        synthesis_body = f"Summary: Analysis of '{problem}' using {', '.join(completed)}.\n\nKey findings:\n"
        for name in completed:
            out = lens_data.get(name, 'No output')
            synthesis_body += f"- [{name}] {out[:200]}\n"
        synthesis_body += "\nConfidence: MEDIUM (synthesis fallback - model did not produce a coherent response)"

    # --- WIKILINK VALIDATION: check all [[wikilinks]] in synthesis against vault ---
    import re
    wl_pattern = r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]'
    found_wikilinks = re.findall(wl_pattern, synthesis_body)
    unresolved_wl = []
    for wl in found_wikilinks:
        wl_clean = wl.strip().split('#')[0].strip()  # strip section refs
        if not wl_clean:
            continue
        try:
            check = vault_read_note(wl_clean, max_lines=1)
            if isinstance(check, dict) and 'error' in check:
                unresolved_wl.append(wl_clean)
        except Exception:
            unresolved_wl.append(wl_clean)
    if unresolved_wl:
        for wl_name in unresolved_wl:
            synthesis_body = synthesis_body.replace(f'[[{wl_name}]]', f'[UNRESOLVED: {wl_name}]')
        synth_warning = f"\n\n> [!] {len(unresolved_wl)} hallucinated wikilink(s) detected and replaced: {', '.join(unresolved_wl)}"
        synthesis_body += synth_warning

    # Assemble final output
    synth_lines = []
    synth_lines.append(f"## Think v4 Analysis: {problem}")
    synth_lines.append("")

    # BS Detector section
    if premise_warnings and premise_warnings not in ('ALL_VERIFIED', 'NO_CLAIMS'):
        if bs_detector_messages:
            synth_lines.append("### BS Detector: Unverified Claims")
        else:
            synth_lines.append("### Things I couldn't fully verify")
        for w in premise_warnings.split('|||'):
            w = w.strip()
            if w:
                synth_lines.append(f"- {w}")
        synth_lines.append("")
    else:
        synth_lines.append("### BS Detector: All premises verified against vault")
        synth_lines.append("")

    # Synthesis section
    synth_lines.append(f"### Synthesis (lenses: {', '.join(completed)})")
    synth_lines.append("")
    synth_lines.append(synthesis_body)
    synth_lines.append("")

    # Raw lens outputs (collapsed, for provenance)
    synth_lines.append("### Raw Lens Outputs")
    synth_lines.append("")
    for name in completed:
        out = lens_data.get(name, 'No output')
        synth_lines.append(f"<details><summary>{name}</summary>\n\n{out}\n</details>")
        synth_lines.append("")

    synthesis = '\n'.join(synth_lines)
    print(synthesis)
```

[validate: contains "Think v4 Analysis"]
[validate: contains "BS Detector"]
[validate: contains "Synthesis"]

---

## Research Justification

1. **Batched verification for 4B models**: The qwen3.5:4b can handle "here are N claims and M docs, give me a verdict for each" in a single call. Testing confirmed it returns YES, NO, and NEUTRAL appropriately — actually engaging with vault content instead of defaulting to NEUTRAL like the 1.7B.

2. **No triple-try needed**: The 4B produced consistent output across all 3 test problems — same classification, same format compliance, same verification patterns. Triple-try (3x LLM round-trips with majority vote) was designed for the 0.8B's inconsistency and is pure waste on the 4B.

3. **Richer prompts**: The 4B has a larger context window and better instruction-following. Prompts can be 3-4 sentences with multi-part instructions instead of 1-2 sentences with single-token output constraints.

4. **Combined tasks**: The 4B can classify a problem AND suggest additional lenses in one call. It can extract factual AND causal claims in one call. It can synthesize from multiple lens outputs in one call. Each merge saves a full LLM round-trip.

5. **Deterministic fallbacks retained**: Every LLM call still has a code-level fallback. If the 4B produces a bad output, the procedure continues with a safe default. Safety is not hand-holding.

6. **VibeThinker-3B precedent** ([[VibeThinker-3B-small-LLM-beats-DeepSeek-GPT-GLM-benchmarks-performance_20260731-233419]]): A 3B model achieved frontier-level reasoning through structured post-training. The 4B should be even more capable when given the right scaffolding.

## Call Count Comparison

| Component | v3 (0.8B-era) | v4 (4B-era) |
|---|---|---|
| Claim extraction | 2 calls (Steps 1+2) | 1 call (Step 1) |
| Verification | 15-21 calls (per-claim x per-doc) | 1-3 calls (batched + research gate) |
| Classification + lens selection | 2 calls (Steps 4+5) | 1 call (Step 3) |
| More lenses check | 1 call (always) | 0-1 calls (conditional) |
| Synthesis | N+1 calls (per-lens + final) | 1 call (Step 6) |
| **Parent total** | **20-28 calls** | **4-6 calls** |

## Related

- [[Deterministic-Scaffolding-for-Small-Models]]
- [[VibeThinker-3B-small-LLM-beats-DeepSeek-GPT-GLM-benchmarks-performance_20260731-233419]]
- [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]]
- All lens procedures: [[Root-Cause-Analysis]], [[Trade-off-Analysis]], [[Systematic-Inquiry]], [[Decomposition]], [[Multi-Perspective]], [[Evidence-Weighing]]