---
type: procedure
status: experimental
created: 2026-08-10
summary: "Parent reasoning procedure that assesses knowledge gaps, builds a lens stack, dispatches lenses with per-step vault retrieval for provenance, iterates with exit guarantees, and synthesizes via the big LLM with structured validation. Pluggable into any procedure that needs structured reasoning without a frontier model."
description: "Gap assessment + lens stack + queued dispatch with vault retrieval + iterative refinement + big-LLM synthesis with validation. Centered on Knowledge-Triad-Ontology-Epistemology-Hermeneutics."
allowed_tools:
  - vault_search
  - vault_read_note
  - llm_generate
  - run_procedure
tags: [procedure, reasoning, think, knowledge-triad, ontology, epistemology, hermeneutics, chain-of-thought, small-model-scaffolding, v2]
---

# Think: Knowledge-Triad Reasoning Scaffold (v2)

## Purpose

This procedure templates structured reasoning so a small model can think like a frontier model by following deterministic steps. It is the **pluggable reasoning engine** — any procedure that needs the model to reason about something calls `run_procedure('Think', args={'problem': '...'})` instead of relying on the model's weights for reasoning.

The architecture follows the [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]]:

| Triad Layer | Question | Think Phase | What Happens |
|---|---|---|---|
| **Ontology** | What kind of problem exists? What don't we know? | Gap Assessment + Lens Stack | LLM assesses knowledge gaps and selects a stack of 1-3 lenses, ordered by priority |
| **Epistemology** | How do we gather/validate knowledge? | Queued Lens Dispatch | Lenses execute in order, each with vault retrieval for provenance. New lenses can be queued based on findings. |
| **Hermeneutics** | How do we interpret the results? | Synthesis | Big LLM synthesizes the full thought chain (with inherited provenance) into a structured, validated conclusion |

## What Changed in v2

| Problem in v1 | Fix in v2 |
|---|---|
| Single-lens dispatch | Step 0: gap assessment builds a lens **stack** (1-3 lenses). Lenses can be **queued** as the stack executes. |
| Synthesis is open-ended generation (small model weakness) | Step 3 uses the **big LLM cartridge** explicitly. The thought chain with provenance is the input — the big model inherits all provenance. |
| No vault provenance in reasoning | Each step does `vault_search` to bring in relevant docs. Conclusions inherit provenance via wikilinks to retrieved notes. |
| No iterative refinement | Step 2 implements the **hermeneutic circle**: after each lens, check if gaps remain. Loop back with new retrieval. **Exit guarantee**: max 3 iterations + convergence check. |
| No consistency checking | **Triple-try** on classification (Step 0) and synthesis (Step 3). Run 3 times, flag divergent outputs, use majority vote. |
| Lens steps too big for small models | Lens procedures updated with **bite-sized steps** and triple-try on key LLM calls. |
| Weak synthesis validation | Step 3 validates: contains required sections, contains wikilinks to vault sources, contains confidence level. Fails and retries if missing. |

## Design Principle: LLM for Semantics, Code for Structure

Classification is a **semantic task** — understanding what someone means even when they use slang, devowel, or unusual phrasing. The LLM handles that. Validation is **structural** — does the response contain a valid lens name? Python handles that. No keyword regex trying to understand meaning. This follows [[Deterministic-Scaffolding-for-Small-Models]]: "The AI proposes; the scaffolding disposes."

## Triple-Try Consistency Pattern

For critical LLM calls (classification and synthesis), the procedure runs the LLM **three times** and uses majority vote. If all three agree, confidence is high. If two agree and one diverges, use the majority. If all three diverge, flag as low-confidence and use the first response. This is the "triple-process for consistency" pattern from [[Deterministic-Scaffolding-for-Small-Models]].

## Inputs

- `problem`: The problem or question to reason about
- `context`: Additional context (optional)
- `lens_override`: Explicit lens stack override (optional, comma-separated)

## Outputs

- Gap assessment (what the vaultbot doesn't know yet)
- Lens stack (ordered list of lenses to apply)
- Per-lens analysis with vault provenance (wikilinks to retrieved notes)
- Iterative refinement log (what gaps were found and filled)
- Synthesized conclusion with wikilinks to vault sources
- Confidence level and key assumptions

---

### Step 1: Ontology — Gap Assessment and Lens Stack Selection

Assess what the vaultbot DOESN'T know yet, then choose a stack of 1-3 lenses ordered by priority. This is the most important step: understanding what angles of viewing things might help, even for just a small portion of the overall problem.

First, do a vault search to see what's already in the vault. Then run the LLM classification **three times** (triple-try) and use majority vote to determine the lens stack.

```python
problem = args.get('problem', '')
context = args.get('context', '')
lens_override = args.get('lens_override', '')

# Explicit override — not a heuristic, an instruction
if lens_override:
    lens_stack = [l.strip() for l in lens_override.split(',') if l.strip()]
    result = f"GAP_ASSESSMENT: override\nLENS_STACK: {','.join(lens_stack)}\nVAULT_DOCS: (skipped — override)\nPROBLEM: {problem}"
    if context:
        result += f"\nCONTEXT: {context}"
    print(result)
else:
    # 1. Vault retrieval — what do we already know?
    vault_results = vault_search(problem, k=5)
    vault_docs = []
    for r in vault_results:
        title = r.get('name', r.get('file_path', ''))
        vault_docs.append(f"[[{title}]]")
    vault_docs_str = ' | '.join(vault_docs) if vault_docs else '(no relevant notes found)'

    # 2. Triple-try classification
    classification_prompt = f"""You are assessing a problem to choose thinking approaches. Read the problem and pick 1-3 best-matching lenses, ordered by priority (most relevant first).

Problem: {problem}

Existing vault notes found: {vault_docs_str}

Available lenses (pick 1-3, comma-separated, most relevant first):
1. Root-Cause-Analysis — the problem asks WHY something is broken, failing, or unexpected
2. Trade-off-Analysis — the problem asks to CHOOSE between options or weigh alternatives
3. Systematic-Inquiry — the problem asks to EXPLAIN, understand, or investigate how something works
4. Decomposition — the problem asks to BUILD, design, or break down a plan into steps
5. Multi-Perspective — the problem asks to EVALUATE from multiple viewpoints or consider implications
6. Evidence-Weighing — the problem asks to VERIFY a claim or weigh evidence for/against

Also identify: what DON'T we know yet? What gaps exist in the vault coverage?

Respond in this exact format:
LENSES: <comma-separated lens names, most relevant first>
GAPS: <what we don't know yet that might help>
REASONING: <1-2 sentences why these lenses and what angles might help>"""

    responses = []
    for i in range(3):
        resp = llm_generate(classification_prompt)
        responses.append(resp.strip())

    # 3. Majority vote on lenses — parse lens list from each response
    valid_lenses = [
        'Root-Cause-Analysis', 'Trade-off-Analysis', 'Systematic-Inquiry',
        'Decomposition', 'Multi-Perspective', 'Evidence-Weighing'
    ]

    def parse_lenses(text):
        for line in text.split('\n'):
            if line.startswith('LENSES:'):
                lens_str = line.replace('LENSES:', '').strip()
                found = []
                for name in valid_lenses:
                    if name.lower() in lens_str.lower():
                        found.append(name)
                return found if found else ['Systematic-Inquiry']
        return ['Systematic-Inquiry']

    def parse_gaps(text):
        for line in text.split('\n'):
            if line.startswith('GAPS:'):
                return line.replace('GAPS:', '').strip()
        return 'unknown gaps'

    lens_lists = [parse_lenses(r) for r in responses]
    gap_lists = [parse_gaps(r) for r in responses]

    # Majority vote: pick the lens list that appears most often
    # If all differ, use the first (most likely to be correct since it's the first try)
    from collections import Counter
    list_tuples = [tuple(l) for l in lens_lists]
    vote_counts = Counter(list_tuples)
    winner, count = vote_counts.most_common(1)[0]

    if count >= 2:
        lens_stack = list(winner)
        method = 'triple-try-majority'
    else:
        # All three diverged — use the first response, flag as low-confidence
        lens_stack = lens_lists[0]
        method = 'triple-try-divergent'

    # Use the gaps from the response that matched the winning lens list
    for i, lt in enumerate(list_tuples):
        if lt == winner:
            gaps = gap_lists[i]
            break
    else:
        gaps = gap_lists[0]

    result = f"GAP_ASSESSMENT: {gaps}\nLENS_STACK: {','.join(lens_stack)}\nMETHOD: {method}\nVAULT_DOCS: {vault_docs_str}\nPROBLEM: {problem}"
    if context:
        result += f"\nCONTEXT: {context}"
    print(result)
```

[validate: contains "GAP_ASSESSMENT"]
[validate: contains "LENS_STACK"]

---

### Step 2: Epistemology — Queued Lens Dispatch with Per-Step Vault Retrieval

Dispatch to lenses from the stack in order. Before each lens, do a vault search to bring in relevant docs for that lens's specific angle. Pass the retrieved docs as context to the lens. After each lens completes, check if the lens output revealed new gaps that warrant queuing an additional lens.

This is the core epistemological step — gathering evidence through structured methods, with provenance from vault docs at each step.

```python
# Parse Step 1 output
lines = output.strip().split('\n')
problem = ''
context = ''
lens_stack_str = ''
vault_docs_str = ''
gaps = ''

for line in lines:
    if line.startswith('PROBLEM: '):
        problem = line.replace('PROBLEM: ', '').strip()
    elif line.startswith('CONTEXT: '):
        context = line.replace('CONTEXT: ', '').strip()
    elif line.startswith('LENS_STACK: '):
        lens_stack_str = line.replace('LENS_STACK: ', '').strip()
    elif line.startswith('VAULT_DOCS: '):
        vault_docs_str = line.replace('VAULT_DOCS: ', '').strip()
    elif line.startswith('GAP_ASSESSMENT: '):
        gaps = line.replace('GAP_ASSESSMENT: ', '').strip()

lens_queue = [l.strip() for l in lens_stack_str.split(',') if l.strip()]
completed_lenses = []
lens_results = {}
all_vault_docs = []

# Process the lens queue — new lenses may be added during processing
max_lenses = 5  # safety cap to prevent runaway
iteration = 0

while lens_queue and iteration < max_lenses:
    iteration += 1
    current_lens = lens_queue.pop(0)

    # Per-step vault retrieval — bring in docs relevant to this lens's angle
    lens_search_query = f"{problem} {current_lens.replace('-', ' ')}"
    lens_vault_results = vault_search(lens_search_query, k=3)
    lens_vault_docs = []
    for r in lens_vault_results:
        title = r.get('name', r.get('file_path', ''))
        doc_ref = f"[[{title}]]"
        if doc_ref not in all_vault_docs:
            all_vault_docs.append(doc_ref)
        lens_vault_docs.append(doc_ref)

    lens_context = context
    if lens_vault_docs:
        lens_context = (context + '\n' if context else '') + f"Vault docs for this lens: {' | '.join(lens_vault_docs)}"

    # Dispatch to the lens procedure
    try:
        lens_args = {'problem': problem, 'context': lens_context or ''}
        lens_output = run_procedure(current_lens, args=lens_args)
        if isinstance(lens_output, dict):
            lens_text = lens_output.get('final_output', str(lens_output))
        else:
            lens_text = str(lens_output)
        lens_results[current_lens] = lens_text
    except Exception as e:
        lens_results[current_lens] = f"ERROR: {str(e)}"

    completed_lenses.append(current_lens)

    # Check if the lens output reveals new gaps that warrant another lens
    # Use a bounded LLM call — small model can do this classification
    if lens_queue:  # only check if there are already more lenses queued
        pass  # existing queue will be processed next
    else:
        # No more lenses in queue — check if we need to queue more
        queue_prompt = f"""Based on the lens analysis so far, does the problem need another lens from this list?

Problem: {problem}

Completed lenses: {', '.join(completed_lenses)}
Latest lens output: {lens_results[current_lens][:500]}

Available lenses not yet applied: {[l for l in ['Root-Cause-Analysis', 'Trade-off-Analysis', 'Systematic-Inquiry', 'Decomposition', 'Multi-Perspective', 'Evidence-Weighing'] if l not in completed_lenses]}

If another lens would help with a SPECIFIC part of the problem that the completed lenses didn't cover, respond with:
QUEUE: <lens name>

If the completed lenses have covered the problem adequately, respond with:
QUEUE: none

Respond with ONLY the QUEUE line, nothing else."""

        # Triple-try for consistency on lens queue decision
        from collections import Counter
        queue_responses = []
        for _ in range(3):
            resp = llm_generate(queue_prompt).strip()
            queue_responses.append(resp)

        # Parse each response for a valid QUEUE: line
        valid_lenses = ['Root-Cause-Analysis', 'Trade-off-Analysis', 'Systematic-Inquiry', 'Decomposition', 'Multi-Perspective', 'Evidence-Weighing']
        parsed_queues = []
        for resp in queue_responses:
            found = None
            for line in resp.split('\n'):
                if line.startswith('QUEUE:'):
                    queued = line.replace('QUEUE:', '').strip()
                    if queued.lower() == 'none':
                        found = 'none'
                    elif queued in valid_lenses:
                        found = queued
                    break
            parsed_queues.append(found if found else 'none')

        # Majority vote on queue decision
        vote = Counter(parsed_queues)
        winner, count = vote.most_common(1)[0]
        queued_lens = winner if winner != 'none' else None

        if queued_lens:
            lens_queue.append(queued_lens)

# Format results for Step 3
result_lines = [f"PROBLEM: {problem}"]
result_lines.append(f"GAPS_IDENTIFIED: {gaps}")
result_lines.append(f"VAULT_DOCS_CITED: {' | '.join(all_vault_docs)}")
result_lines.append(f"LENSES_APPLIED: {', '.join(completed_lenses)}")
for lens, res in lens_results.items():
    result_lines.append(f"LENS: {lens}")
    result_lines.append(f"OUTPUT: {res}")
result = '\n'.join(result_lines)
print(result)
```

[validate: contains "LENS"]
[validate: contains "VAULT_DOCS_CITED"]

---

### Step 3: Hermeneutics — Iterative Refinement Loop

After the lens stack completes, check whether the analysis has covered the problem or if gaps remain. If gaps remain, loop back to retrieve more vault docs and re-run relevant lenses. The hermeneutic circle: understanding the whole requires understanding the parts, and understanding the parts requires understanding the whole.

**Exit guarantee**: maximum 3 iterations. If gaps remain after 3 iterations, proceed to synthesis with the best available information. The procedure WILL eventually exit — new info each round through retrieval means it converges, and the hard cap prevents infinite cycling.

```python
# Parse Step 2 output
lines = output.strip().split('\n')
problem = ''
gaps = ''
vault_docs = []
lens_data = {}
current_lens = ''
lenses_applied = ''

for line in lines:
    if line.startswith('PROBLEM: '):
        problem = line.replace('PROBLEM: ', '').strip()
    elif line.startswith('GAPS_IDENTIFIED: '):
        gaps = line.replace('GAPS_IDENTIFIED: ', '').strip()
    elif line.startswith('VAULT_DOCS_CITED: '):
        vault_docs_str = line.replace('VAULT_DOCS_CITED: ', '').strip()
        vault_docs = [d.strip() for d in vault_docs_str.split('|') if d.strip()]
    elif line.startswith('LENSES_APPLIED: '):
        lenses_applied = line.replace('LENSES_APPLIED: ', '').strip()
    elif line.startswith('LENS: '):
        current_lens = line.replace('LENS: ', '').strip()
        lens_data[current_lens] = []
    elif line.startswith('OUTPUT: '):
        if current_lens:
            lens_data[current_lens].append(line.replace('OUTPUT: ', '').strip())

# Iterative refinement loop
max_iterations = 3
current_iteration = 0
refinement_log = []

while current_iteration < max_iterations:
    current_iteration += 1

    # Check: has the analysis covered the problem, or do gaps remain?
    lens_summaries = []
    for lens, outputs in lens_data.items():
        lens_summaries.append(f"{lens}: {' | '.join(outputs)[:300]}")

    # Bite-sized Step 3a: Coverage check (triple-try for consistency)
    # This is the critical exit decision — decomposed from the old 3-part prompt
    coverage_prompt = f"""Has the analysis covered the problem adequately?

Problem: {problem}

Lens analyses:
{chr(10).join(lens_summaries)}

Vault docs cited so far: {' | '.join(vault_docs)}

Respond with ONLY:
COVERAGE: sufficient
or
COVERAGE: gaps_remain"""

    from collections import Counter as _Counter3
    coverage_votes = []
    for _ in range(3):
        resp = llm_generate(coverage_prompt).strip()
        for line in resp.split('\n'):
            if line.startswith('COVERAGE:'):
                val = line.replace('COVERAGE:', '').strip().lower()
                if val in ['sufficient', 'gaps_remain']:
                    coverage_votes.append(val)
                break

    if coverage_votes:
        coverage = _Counter3(coverage_votes).most_common(1)[0][0]
    else:
        coverage = 'gaps_remain'  # safe default

    if coverage == 'sufficient':
        refinement_log.append(f"Iteration {current_iteration}: coverage sufficient (votes: {coverage_votes}), exiting loop")
        break

    # Bite-sized Step 3b: Identify remaining gaps (only if coverage says gaps_remain)
    gaps_prompt = f"""What is still unknown about this problem that the lens analyses didn't cover?

Problem: {problem}

Lens analyses:
{chr(10).join(lens_summaries)}

Respond with ONLY:
REMAINING_GAPS: [what's still unknown, or "none" if nothing is missing]"""

    gaps_response = llm_generate(gaps_prompt).strip()
    remaining_gaps = 'none'
    for line in gaps_response.split('\n'):
        if line.startswith('REMAINING_GAPS:'):
            remaining_gaps = line.replace('REMAINING_GAPS:', '').strip()
            break

    if remaining_gaps == 'none':
        refinement_log.append(f"Iteration {current_iteration}: no remaining gaps identified, exiting loop")
        break

    # Bite-sized Step 3c: Generate search terms (only if gaps remain)
    search_prompt = f"""What vault search terms would help fill this knowledge gap?

Remaining gap: {remaining_gaps}

Respond with ONLY:
NEW_SEARCH_TERMS: [search terms, or "none" if vault retrieval won't help]"""

    search_response = llm_generate(search_prompt).strip()
    new_search_terms = 'none'
    for line in search_response.split('\n'):
        if line.startswith('NEW_SEARCH_TERMS:'):
            new_search_terms = line.replace('NEW_SEARCH_TERMS:', '').strip()
            break

    # Gaps remain — do new vault retrieval
    if new_search_terms != 'none' and new_search_terms:
        search_query = new_search_terms if new_search_terms != 'none' else remaining_gaps
        new_results = vault_search(search_query, k=3)
        new_docs = []
        for r in new_results:
            title = r.get('name', r.get('file_path', ''))
            doc_ref = f"[[{title}]]"
            if doc_ref not in vault_docs:
                vault_docs.append(doc_ref)
                new_docs.append(doc_ref)

        refinement_log.append(f"Iteration {current_iteration}: found {len(new_docs)} new docs for gaps: {remaining_gaps}")

        if not new_docs:
            # No new docs found — can't fill gaps, exit
            refinement_log.append(f"Iteration {current_iteration}: no new vault docs found, proceeding to synthesis with available info")
            break
    else:
        refinement_log.append(f"Iteration {current_iteration}: no actionable search terms, proceeding to synthesis")
        break

    # Re-run the most relevant lens with the new context
    # Pick the lens that best matches the remaining gaps
    rerun_prompt = f"""Which of these lenses best matches the remaining gap?

Remaining gap: {remaining_gaps}

Available lenses: {lenses_applied}

Respond with ONLY the lens name, nothing else."""

    # Triple-try for consistency on lens re-run selection
    from collections import Counter
    rerun_responses = []
    for _ in range(3):
        resp = llm_generate(rerun_prompt).strip()
        rerun_responses.append(resp)

    # Parse each response for a valid lens name and majority vote
    valid_lenses = ['Root-Cause-Analysis', 'Trade-off-Analysis', 'Systematic-Inquiry',
                    'Decomposition', 'Multi-Perspective', 'Evidence-Weighing']
    parsed_lenses = []
    for resp in rerun_responses:
        for name in valid_lenses:
            if name.lower() in resp.lower():
                parsed_lenses.append(name)
                break

    if parsed_lenses:
        vote = Counter(parsed_lenses)
        rerun_lens = vote.most_common(1)[0][0]
    else:
        rerun_lens = None

    if rerun_lens:
        try:
            lens_args = {
                'problem': problem,
                'context': f"Additional vault docs: {' | '.join(new_docs)}. Focus on: {remaining_gaps}"
            }
            lens_output = run_procedure(rerun_lens, args=lens_args)
            if isinstance(lens_output, dict):
                lens_text = lens_output.get('final_output', str(lens_output))
            else:
                lens_text = str(lens_output)
            lens_data[f"{rerun_lens} (refinement-{current_iteration})"] = [lens_text]
            refinement_log.append(f"Iteration {current_iteration}: re-ran {rerun_lens} with new context")
        except Exception as e:
            refinement_log.append(f"Iteration {current_iteration}: re-run of {rerun_lens} failed: {str(e)}")

# Check if we hit the max iterations cap
if current_iteration >= max_iterations:
    refinement_log.append(f"Reached max iterations ({max_iterations}), proceeding to synthesis")

# Format output for Step 4
result_lines = [f"PROBLEM: {problem}"]
result_lines.append(f"GAPS_IDENTIFIED: {gaps}")
result_lines.append(f"VAULT_DOCS_CITED: {' | '.join(vault_docs)}")
result_lines.append(f"LENSES_APPLIED: {lenses_applied}")
result_lines.append(f"REFINEMENT_LOG: {' | '.join(refinement_log)}")
for lens, outputs in lens_data.items():
    result_lines.append(f"LENS: {lens}")
    result_lines.append(f"OUTPUT: {' | '.join(outputs)}")
result = '\n'.join(result_lines)
print(result)
```

[validate: contains "REFINEMENT_LOG"]
[validate: contains "VAULT_DOCS_CITED"]

---

### Step 4: Hermeneutics — Big LLM Synthesis with Structured Validation

Synthesize the full thought chain into a structured conclusion using the **big LLM cartridge**. The big model inherits all provenance from the thought chain — the vault docs cited, the lens analyses, the refinement log. The procedure is the guiding force; the big model's job is to weave the provenance-backed findings into a coherent conclusion.

**Single big LLM call**: the big model is consistent enough that triple-try is unnecessary — that's only for the small model. Run the synthesis once. If validation fails, retry once (not triple-try). If the retry also fails validation, use the fallback template.

**Structured validation**: the synthesis MUST contain:
1. Key findings (with wikilinks to vault sources)
2. Recommended action
3. Confidence level (high/medium/low) with reasoning
4. Key assumptions
5. At least one wikilink to a vault source (provenance requirement)

```python
# Parse Step 3 output
lines = output.strip().split('\n')
problem = ''
gaps = ''
vault_docs = []
lens_data = {}
current_lens = ''
lenses_applied = ''
refinement_log = ''

for line in lines:
    if line.startswith('PROBLEM: '):
        problem = line.replace('PROBLEM: ', '').strip()
    elif line.startswith('GAPS_IDENTIFIED: '):
        gaps = line.replace('GAPS_IDENTIFIED: ', '').strip()
    elif line.startswith('VAULT_DOCS_CITED: '):
        vault_docs_str = line.replace('VAULT_DOCS_CITED: ', '').strip()
        vault_docs = [d.strip() for d in vault_docs_str.split('|') if d.strip()]
    elif line.startswith('LENSES_APPLIED: '):
        lenses_applied = line.replace('LENSES_APPLIED: ', '').strip()
    elif line.startswith('REFINEMENT_LOG: '):
        refinement_log = line.replace('REFINEMENT_LOG: ', '').strip()
    elif line.startswith('LENS: '):
        current_lens = line.replace('LENS: ', '').strip()
        lens_data[current_lens] = []
    elif line.startswith('OUTPUT: '):
        if current_lens:
            lens_data[current_lens].append(line.replace('OUTPUT: ', '').strip())

# Build the synthesis prompt with full provenance chain
lens_summaries = []
for lens, outputs in lens_data.items():
    lens_summaries.append(f"## {lens}\n{' | '.join(outputs)}")

vault_docs_str = ' | '.join(vault_docs) if vault_docs else '(no vault docs retrieved)'

synthesis_prompt = f"""You are a reasoning synthesis system. Given the following thought chain — lens analyses, vault documents cited, and refinement log — synthesize them into a coherent conclusion.

Problem: {problem}

Knowledge gaps identified: {gaps}

Vault documents cited (these are the provenance sources):
{vault_docs_str}

Refinement log: {refinement_log}

Lens analyses:
{chr(10).join(lens_summaries)}

Provide a structured conclusion in this EXACT format:

KEY_FINDINGS:
- [finding 1, with wikilink to vault source like [[Note-Name]]]
- [finding 2, with wikilink to vault source]
- [additional findings as needed]

RECOMMENDED_ACTION:
[specific, actionable recommendation]

CONFIDENCE: [high|medium|low]
CONFIDENCE_REASONING: [why this confidence level — what's known vs unknown]

KEY_ASSUMPTIONS:
- [assumption 1]
- [assumption 2]

Rules:
- Every finding MUST cite at least one vault document using [[wikilink]] syntax
- Only state findings that are supported by the lens analyses or vault docs
- If evidence is insufficient, state that explicitly in KEY_FINDINGS
- Do not introduce new information not present in the lens analyses or vault docs"""

# Single big LLM call with structured validation (no triple-try — big model is consistent)
import re as _re

def validate_synthesis(text):
    """Validate that the synthesis has all required sections and provenance."""
    issues = []
    required_sections = ['KEY_FINDINGS:', 'RECOMMENDED_ACTION:', 'CONFIDENCE:', 'CONFIDENCE_REASONING:', 'KEY_ASSUMPTIONS:']
    for section in required_sections:
        if section not in text:
            issues.append(f"missing section: {section}")
    # Check for at least one wikilink (provenance requirement)
    wikilinks = _re.findall(r'\[\[([^\]]+)\]\]', text)
    if len(wikilinks) < 1:
        issues.append("missing wikilinks to vault sources (provenance requirement)")
    # Check confidence is a valid value
    has_confidence = False
    for line in text.split('\n'):
        if line.startswith('CONFIDENCE:'):
            conf_val = line.replace('CONFIDENCE:', '').strip().lower()
            if conf_val in ['high', 'medium', 'low']:
                has_confidence = True
            break
    if not has_confidence:
        issues.append("missing or invalid confidence level")
    return len(issues) == 0, issues

# Single big LLM call — the big model doesn't need triple-try for consistency
synthesis = llm_generate(synthesis_prompt)
is_valid, issues = validate_synthesis(synthesis)

if not is_valid:
    # One retry with explicit instruction about what was missing, then accept whatever comes back
    fix_prompt = f"""Your previous response was missing: {'; '.join(issues)}

Please regenerate the synthesis, fixing these issues. Original prompt:

{synthesis_prompt}"""
    synthesis = llm_generate(fix_prompt)
    is_valid, issues = validate_synthesis(synthesis)
    if is_valid:
        best_method = 'single-call-retry-validated'
    else:
        best_method = f'single-call-validation-failed: {"; ".join(issues)}'
else:
    best_method = 'single-call-validated'

# If still invalid after retry, construct fallback from raw lens data
if not is_valid:
    fallback_lines = ["SYNTHESIS (fallback — LLM validation failed):"]
    for lens, outputs in lens_data.items():
        fallback_lines.append(f"- {lens}: {' | '.join(outputs)[:200]}")
    if vault_docs:
        fallback_lines.append(f"\nVault docs cited: {' | '.join(vault_docs)}")
    fallback_lines.append(f"\nConfidence: low (LLM synthesis failed validation)")
    fallback_lines.append(f"Validation issues: {'; '.join(issues)}")
    synthesis = '\n'.join(fallback_lines)
    best_method = 'fallback-from-raw-lens-data'

result = f"SYNTHESIS_METHOD: {best_method}\nVAULT_DOCS_CITED: {' | '.join(vault_docs)}\nSYNTHESIS:\n{synthesis}"
print(result)
```

[validate: contains "SYNTHESIS:"]
[validate: contains "VAULT_DOCS_CITED"]
[validate: contains "CONFIDENCE:"]

---

## Research Sources

This procedure is grounded in the following research:

- [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]] — The triad structure (Ontology → Epistemology → Hermeneutics) is the core architecture. Ontology = classify what exists and what gaps remain (problem type + gap assessment). Epistemology = how we know (lens methods gather evidence with vault retrieval). Hermeneutics = interpretation (big LLM synthesis of lens outputs with inherited provenance).

- [[cognitive-psychology-of-reasoning-dual-process-theory-System-1-System-2-thinking]] — System 1 (fast, intuitive) vs System 2 (slow, analytical). Small models default to System 1; this procedure forces System 2 by decomposing reasoning into explicit steps. The LLM classification is the System 1 fast path; the lens procedures are the System 2 deep analysis.

- [[psychology-of-problem-solving-Gestalt-psychology-insight-vs-analytical-reasoning]] — Gestalt psychology shows that insight problems need holistic thinking while well-defined problems benefit from means-ends analysis (decomposition). This is why the Think procedure has multiple lenses instead of one universal method.

- [[cognitive-psychology-metacognition-thinking-dispositions-need-for-cognition-refl]] — Metacognition (thinking about thinking) improves reasoning quality. The Think procedure's gap assessment step is metacognitive — it asks "what don't we know?" before thinking. The iterative refinement loop is also metacognitive — it checks coverage after each lens.

- [[psychology-of-analytical-thinking-methods-when-to-use-root-cause-analysis-vs-fir]] — Evidence for when different analytical frameworks work: root cause analysis for debugging, first principles for design, systems thinking for complex systems, Socratic questioning for exploration.

- [[Deterministic-Scaffolding-for-Small-Models]] — "The AI proposes; the scaffolding disposes." The sandwich pattern: deterministic validation wrapping probabilistic AI. Triple-process for consistency. Structured outputs only. Fail safe. This procedure implements all five patterns.

- [[Structured-reasoning-formats-for-small-language-models-chain-of-thought-promptin]] — CoT only emerges in large models; small models under-think and skip steps. Structured scaffolding that forces each step is essential.

- [[critical-thinking-frameworks-and-methods-Socratic-method-dialectical-reasoning-f]] — Socratic method = systematic questioning, dialectical reasoning = thesis/antithesis/synthesis, first-principles = decompose to fundamentals.

## Knowledge Triad Mapping

| Triad Phase | Think Step | What It Does | Who Does the Work |
|-------------|-----------|--------------|-------------------|
| Ontology (What exists? What don't we know?) | Step 1 | Gap assessment + lens stack selection (triple-try) | LLM assesses gaps (semantic), code validates + majority votes (structural) |
| Epistemology (How do we know?) | Step 2 | Queued lens dispatch with per-step vault retrieval | Code dispatches + retrieves (structural), lens gathers evidence (structured) |
| Hermeneutics (What does it mean? Is it enough?) | Step 3 | Iterative refinement loop with exit guarantee | LLM checks coverage (semantic), code enforces max iterations (structural) |
| Hermeneutics (Final interpretation) | Step 4 | Big LLM synthesis with structured validation (single call + retry) | Big LLM synthesizes (semantic), code validates sections + provenance (structural) |

## Lens Selection Guide

| Problem Type | Lens Procedure | Cognitive Psychology Basis |
|--------------|----------------|---------------------------|
| Debugging, error diagnosis | Root-Cause-Analysis | Abductive reasoning, 5-Whys, fishbone diagram |
| Choosing between options | Trade-off-Analysis | Decision theory, dual-process theory on preferences |
| Research, exploration | Systematic-Inquiry | Socratic method, dialectical reasoning |
| Building, planning | Decomposition | Means-ends analysis, subgoal decomposition |
| Evaluation, judgment | Multi-Perspective | Dialectical reasoning (thesis-antithesis-synthesis) |
| Verifying claims | Evidence-Weighing | Epistemological justification, Bayesian reasoning |

## Related

- [[Think-Procedure-and-the-Knowledge-Triad]] — synthesis note documenting the architecture
- [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]] — philosophical foundation
- [[Deterministic-Scaffolding-for-Small-Models]] — scaffolding principle (triple-try, structured outputs, fail safe)
- [[Evidence-Weighing]] — lens: Bayesian evidence evaluation
- [[Trade-off-Analysis]] — lens: multi-criteria decision analysis
- [[Root-Cause-Analysis]] — lens: abductive causal reasoning
- [[Systematic-Inquiry]] — lens: Socratic questioning
- [[Decomposition]] — lens: means-ends analysis
- [[Multi-Perspective]] — lens: dialectical reasoning