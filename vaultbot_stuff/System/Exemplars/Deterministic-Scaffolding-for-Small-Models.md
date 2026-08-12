---
type: exemplar
exemplar: architecture-note
created: 2025-07-25
summary: How to structure an architecture note — design reasoning, trade-offs, research-backed arguments, and what to build next.
tags:
  - architecture
  - exemplar
  - scaffolding
  - small-models
  - deterministic
status: stale
---

<!-- EXEMPLAR ANNOTATION: ARCHITECTURE NOTE
     This note is an exemplar for writing architecture notes. An architecture note:
     1. Leads with a core insight or quote that frames the problem
     2. Presents the pattern/solution with structured evidence (code blocks, tables)
     3. Includes quantitative results from real-world case studies
     4. Generalizes patterns into actionable principles (numbered list)
     5. Maps the pattern to VaultBot's specific architecture
     6. Identifies what's already deterministic vs what needs scaffolding
     7. Ends with a "what to build" section and related wikilinks
     8. Every claim is sourced with inline links to archived web pages
-->

# Deterministic Scaffolding for Small Models

<!-- ANNOTATION: Lead with the core insight. One paragraph that frames the entire problem and solution. Include a sourced quote if available. -->
## The Core Insight

> "The AI proposes; the scaffolding disposes." -- [OpenEmpower: Deterministic Scaffolding for Agentic AI](learningMaterial/web/www-openempower-com-blog-deterministic-scaffolding-agentic-ai-case-study-what-1bad6127.html)

AI models are probabilistic. Production systems must be deterministic. The solution is **not** to make the AI deterministic -- that's fighting physics. The solution is to wrap the probabilistic AI in deterministic validation, orchestration, and rollback mechanisms. The model generates proposals; the framework validates, corrects, or rejects them.

This is the architecture that makes a 30B local model sufficient from day 1. The framework does the heavy lifting, not the model's weights.

<!-- ANNOTATION: Present the core pattern with a structured code block. This makes the architecture visually clear and easy to pattern-match against. -->
## The Sandwich Pattern

The core architecture from the [OpenEmpower case study](learningMaterial/web/www-openempower-com-blog-deterministic-scaffolding-agentic-ai-case-study-what-1bad6127.html) -- a European bank processing 2,000 regulatory filings/month:

```
Input Layer (Deterministic)
    -> Format validation, classification, PII detection, audit log
AI Layer (Probabilistic, Sandboxed)
    -> Self-hosted LLM, structured JSON outputs only, each task runs independently
Validation Layer (Deterministic)
    -> Schema validation, reference validation, consistency check, confidence scoring
Human Review Layer (when needed)
    -> Low-confidence extractions queued, analyst approves/corrects/rejects
Output Layer (Deterministic)
    -> Approved outputs only, full audit trail
```

**Results:** 3 weeks (12 analysts) -> 3 days (2 analysts). Error rate: 5% -> 0.3%. The scaffolding caught most AI errors; humans caught the rest.

<!-- ANNOTATION: Generalize from the specific case to actionable principles. Use a numbered list with bold key terms. Each pattern should be independently applicable. -->
## Five Patterns That Generalize

From the [OpenEmpower case study](learningMaterial/web/www-openempower-com-blog-deterministic-scaffolding-agentic-ai-case-study-what-1bad6127.html):

1. **Decompose the task** -- Split into AI-suitable subtasks (extraction, classification, generation) and deterministic subtasks (validation, cross-referencing, output formatting). The AI only does what it's good at; the framework does everything else.

2. **Sandwich AI between deterministic layers** -- Input validation -> AI processing -> Output validation -> Human review (for low-confidence) -> Deterministic output. The AI never touches the output directly.

3. **Triple-process for consistency** -- Run the same input through the AI multiple times. Flag divergent outputs. Consistent outputs have higher confidence. This is a deterministic reliability check on a probabilistic system.

4. **Structured outputs only** -- Never let AI produce free-text that feeds directly into downstream systems. JSON with schema validation. Every field typed, every reference verifiable. The [Qwen function calling docs](learningMaterial/web/qwen-readthedocs-io-en-latest-framework-function-call-html-c7a14bc5.html) confirm this: Hermes-style tool use produces structured `function_call` objects with `name` and `arguments` fields, not free text.

5. **Fail safe** -- When validation fails, route to humans (or in VaultBot's case, to IDK fallback). Never produce unvalidated output. The system degrades to "I don't know," not to uncontrolled AI output. This directly connects to the [[IDK-Fallback-Directive]].

<!-- ANNOTATION: Map the general pattern to VaultBot's specific architecture. Split into 'what's already deterministic' vs 'what needs scaffolding' — this makes the gap actionable. -->
## What This Means for VaultBot

VaultBot already has pieces of this pattern. The gap is making them explicit and deterministic:

### What's Already Deterministic
- **Research engine** -- scrapes, extracts, corroborates. Returns structured facts ("9 sources, 18 corroborated facts"). The LLM doesn't do the research; the framework does.
- **safe_write** -- syntax-checks, import-tests, auto-rolls-back. The LLM proposes code; the framework validates it.
- **vault_lint** -- checks wikilinks, frontmatter, argument quality. The LLM writes a note; the framework validates it.
- **FUSED retrieval** -- vector + graph + backlinks. The LLM doesn't search; the framework retrieves.

### What's Currently Probabilistic (Needs Scaffolding)
- **Tool selection** -- The LLM decides which tool to call. Should be: deterministic rules ("if vault_search returns <3 results with score <200 -> vault_research").
- **Note synthesis** -- The LLM synthesizes research into a note. Should be: template + structured facts -> fill in sections. The 30B formats, it doesn't reason.
- **Decision making** -- The LLM decides when to research, when to say IDK, when to build a tool. Should be: explicit decision tree, not judgment.
- **Code generation** -- The LLM writes new tools. Should be: template + examples from the vault -> fill in blanks -> safe_write validates.

## Small Model Function Calling: What the Research Shows

### Qwen3 + Hermes-Style Tool Use
The [Qwen function calling docs](learningMaterial/web/qwen-readthedocs-io-en-latest-framework-function-call-html-c7a14bc5.html) show that Qwen3 (including 8B variants) can do function calling with structured prompts:

- Tools are described as JSON Schema objects with `name`, `description`, `parameters`
- The model returns `function_call` objects with `name` and JSON `arguments`
- vLLM can auto-parse tool calls with `--tool-call-parser hermes`
- **Key caveat:** "It is not guaranteed that the model generation will always follow the protocol even with proper prompting or templates." -- This is exactly why deterministic validation layers are needed.

### Small Models, Big Tasks (arXiv:2504.19277)
The [empirical study on SLMs for function calling](learningMaterial/web/arxiv-org-abs-2504-19277v1-7dbc1ff3.html) found:
- SLMs improve from zero-shot -> few-shot -> fine-tuning (in that order)
- They **struggle significantly with adhering to the given output format** -- this is the #1 problem deterministic scaffolding solves
- They're robust against prompt injection (slight decline only)
- They demonstrate potential but need refinement for real-time use

**Implication for VaultBot:** The format adherence problem is solved by the validation layer, not by the model. If the 30B produces malformed JSON, the framework catches it and either retries or falls back. The model doesn't need to be perfect -- it needs to be "good enough that the scaffolding can catch the rest."

### SWEnergy: SLMs in Agentic Frameworks
The [SWEnergy study](learningMaterial/web/arxiv-org-abs-2512-09543v2-7d4ef3ba.html) tested Gemma-3 4B and Qwen-3 1.7B in agentic frameworks (SWE-Agent, OpenHands, Mini SWE Agent, AutoCodeRover). Even 1.7B models can operate within agentic frameworks -- the framework does the orchestration, not the model.

## Few-Shot Prompting: The Vault as Few-Shot Library

From [Few-Shot Prompting Examples 2026](learningMaterial/web/neuraplus-ai-github-io-blog-few-shot-prompting-examples-2026-html-15fdd923.html):

- **Sweet spot: 3-5 examples** for most tasks. Too few (1-2) and the model doesn't grasp the pattern. Too many (10+) and you waste tokens and confuse the model.
- **Research shows 30-50% accuracy improvement** over zero-shot approaches.
- **Example quality > quantity.** Three excellent, diverse examples outperform ten mediocre ones.
- **Consistent formatting is critical.** If examples use different formats, the model gets confused.
- **Dynamic retrieval of examples** (vs static) -- examples can be retrieved based on the query, not hardcoded. This is exactly what FUSED retrieval does: pull the most relevant exemplar from the vault based on the current task.

**For VaultBot:** The vault should contain tagged exemplar notes -- best research note, best tool creation, best gap-fill. When a 30B model needs to write a research note, FUSED retrieval pulls the exemplar and the model pattern-matches. The shots live permanently in the vault, not in the prompt.

<!-- ANNOTATION: End with a forward-looking section. What needs to be built? What components are missing? This makes the note actionable, not just descriptive. -->
## The Path to Cloud Model Obsolescence

### What the Cloud Model Currently Does (That It Shouldn't Need To)

| Dependency | Deterministic Replacement |
|---|---|
| Research synthesis | Research engine returns structured facts -> template -> 30B formats |
| Tool selection | Decision rules: "if score <200 and <3 results -> vault_research" |
| Multi-step planning | Procedural notes found online -> 30B follows steps |
| Code generation | Templates + examples from vault -> 30B fills blanks -> safe_write validates |
| Note writing | Template + structured facts -> 30B fills sections |
| Decision making | Explicit if-then rules, not judgment |

### The Framework Components We Need to Build

1. **Decision tree engine** -- A deterministic rules system that routes tasks. Not "the LLM decides what to do" but "the framework evaluates conditions and tells the LLM what to do."

2. **Template system** -- Note templates for research notes, tool specs, gap-fills. The 30B fills in sections, doesn't design the structure.

3. **Exemplar retrieval** -- When a task is assigned, FUSED retrieval pulls the best exemplar from the vault. The 30B pattern-matches against it.

4. **Output validation** -- Every LLM output passes through schema validation before it's committed to the vault. Malformed output -> retry or IDK. This is the "scaffolding disposes" half.

5. **Procedural knowledge base** -- Step-by-step procedures for every operation, found through research and stored in the vault. The 30B follows the procedure, doesn't figure it out.

6. **Consistency checking** -- For high-stakes operations, run the 30B multiple times and flag divergent outputs. Deterministic reliability check on a probabilistic system.

### The Profound Shift

The cloud model's job is **not** to build the scaffolding. The framework's job is to **find** the scaffolding through research. The internet already contains:
- How to do research -> store the procedure
- How to structure a technical note -> store the template
- How to write a Python tool -> store the code pattern
- How to make decisions -> store the decision tree
- Few-shot examples for everything -> store the exemplars

The vault finds these things through [[Autonomy-Directive|autonomous research]] and stores them. The 30B follows them. The cloud model becomes unnecessary not because it's been replaced by a better model, but because the framework has absorbed the cognition that the model was providing.

<!-- ANNOTATION: Every architecture note should end with wikilinks to related notes. These connections make the note findable via graph traversal, not just keyword search. -->
## Related
- [[Small-Model-Path-to-AGI]] -- the original theory note (now updated with this framing)
- [[Autonomy-Directive]] -- VaultBot operates autonomously, finding and storing scaffolding
- [[IDK-Fallback-Directive]] -- the "fail safe" pattern in practice
- [[vaultbot_stuff/Vault-Knowledge-Only-Directive]] -- the vault is the only knowledge source
- [[Vault-Longevity-Architecture]] -- why the vault, not the model, is the mind

---

## Philosophical Grounding

The sandwich pattern (deterministic validation wrapping probabilistic AI) is grounded in **epistemology** — specifically the Gettier problem. A model can produce a true output by accident (Gettier case), but the validation layer ensures outputs are not just true but *justified*. See [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]] for how this connects to ontology (structure) and hermeneutics (interpretation).


LOCKED
