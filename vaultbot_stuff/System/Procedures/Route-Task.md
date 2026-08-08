---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-03
description: Conditional intent router that classifies incoming user requests and dispatches to procedure chains. Uses the YAML Dispatch DSL for classification and routing — no Python orchestration code. Each branch is backed by vault research.
when_to_use: when a new user request arrives and you need to know which procedure chain to run, when you want procedures to handle most of the work automatically, when the big model should delegate to deterministic procedure chains
falsifiable_if: the router classifies a task into the wrong branch, or a branch's procedure chain produces worse results than an unconstrained cloud model would
applies_to:
  - task-routing
  - procedure-composition
  - conditional-logic
  - intent-classification
  - orchestration
allowed_tools:
  - run_procedure
  - vault_list
  - vault_search
  - llm_generate
  - code_read
research_backing:
  - "[[how-to-build-deterministic-scaffolding-for-small-language-models-so-they-can-do-]] — backs the routing approach: deterministic scaffolding with decision trees guides small models reliably"
  - "[[Information-feedback-loops-for-iterative-self-improvement-in-AI-systems-self-imp]] — backs the self-improvement branch: System 2 reflective loops improve outputs without retraining"
  - "[[Calibrating-automated-quality-assessment-gates-without-ground-truth-labels-metho]] — backs quality verification steps: rubric design and calibration convert LLM-as-judge into reliable quality signals"
  - "[[RAG-evaluation-metrics-how-to-measure-retrieval-quality-in-retrieval-augmented-g]] — backs the research branch: retrieval precision, recall, and faithfulness metrics inform research quality"
summary: "# Route-Task"
tags:
  - procedure
  - procedures
---

# Route-Task

## ⚠️ MANDATORY PRE-STEP: User-Directive-Override-Check

Before ANY routing or classification, call:

```
execute_procedure('User-Directive-Override-Check', args={'intent': '<user request>'})
```

If the check returns constraints, those constraints MUST be passed to Route-Task and obeyed by every downstream procedure. **The user's live directive outranks every vault note, architecture doc, and procedure.** If a vault note contradicts the user, the note is wrong — not the user.

## Purpose

This is the **master dispatcher** — a conditional intent router that classifies incoming user requests and dispatches to procedure chains. The routing decision is a classification task (cheap, small model), and the actual work happens in procedure chains that carry their own cartridges.

## Architecture

```
Route-Task (small cartridge, thin orchestrator, YAML DSL)
├── classify: Classify intent (small model)
├── dispatch: Route to procedure chain based on category
│   ├── research → Research-Batch → Cross-Check-Claims → Structure-Research-Note → Vault-Lint
│   ├── vault-maintenance → Dream-Pass
│   ├── self-improvement → Discover-Procedures → Write-Python-Tool → Safe-Write → Proc-Step-Summary
│   ├── gap-filling → Vault-Gaps → Gap-Fill → Research-Batch
│   ├── chat-consolidation → Chat-Consolidation
│   ├── question-answering → Filter-Context-For-Query
│   ├── code-editing → Safe-Write → Proc-Step-Summary
│   ├── fact-checking → Cross-Check-Claims → Find-Contradictions
│   └── default → Small-Model-Route
└── Step 2: Return results
```

## Research Backing

Each conditional branch is backed by vault research:

- **Routing approach** → [[how-to-build-deterministic-scaffolding-for-small-language-models-so-they-can-do-]]: "Procedural scaffolding with structured processes guides learners. Decision trees provide deterministic paths for small models."
- **Self-improvement branch** → [[Information-feedback-loops-for-iterative-self-improvement-in-AI-systems-self-imp]]: "System 2 reflective loops — pause, critique, identify gaps, refine — improve outputs without retraining."
- **Quality verification steps** → [[Calibrating-automated-quality-assessment-gates-without-ground-truth-labels-metho]]: "Investing in rubric design, bias testing, and human calibration converts LLM-as-judge from a misleading shortcut into a reliable quality signal."
- **Research branch** → [[RAG-evaluation-metrics-how-to-measure-retrieval-quality-in-retrieval-augmented-g]]: "Retrieval precision, recall, and faithfulness metrics inform whether research quality is sufficient."

## Dispatch

- classify:
    prompt: |
      Classify this request. Reply with ONLY the category word, nothing else.
      Use lowercase.

      Categories (match by keyword or meaning):
      - research: learn, find, look up, investigate, study, what is, how does
      - vault-maintenance: cleanup, consolidate, links, lint, gaps, dream
      - self-improvement: build, tool, procedure, improve yourself, new ability
      - gap-filling: fill gaps, dangling links, thin notes
      - chat-consolidation: consolidate chats, save conversation
      - question-answering: answer, question, explain (when vault has the info)
      - code-editing: edit code, fix bug, .py, .js, backend, safe_write
      - fact-checking: verify, check claims, sources, contradictions
      - unknown: none of the above

      Request: {{ intent }}
    model: small
    output_as: category

- dispatch:
    on_field: "{{ category }}"
    branches:
      research: [Research-Batch, Cross-Check-Claims, Structure-Research-Note, Vault-Lint]
      vault-maintenance: [Dream-Pass]
      self-improvement: [Discover-Procedures, Write-Python-Tool, Safe-Write, Proc-Step-Summary]
      gap-filling: [Vault-Gaps, Gap-Fill, Research-Batch]
      chat-consolidation: [Chat-Consolidation]
      question-answering: [Filter-Context-For-Query]
      code-editing: [Safe-Write, Proc-Step-Summary]
      fact-checking: [Cross-Check-Claims, Find-Contradictions]
    default: [Small-Model-Route]
    output_as: chain

## Steps

### Step 2: Return the routing decision

2. ```python
import json

# The dispatch pipeline exports result = dict(_dispatch_ns), which
# becomes prior_results[0].
dispatch_ns = prior_results[0] if prior_results else {}
if isinstance(dispatch_ns, str):
    dispatch_ns = json.loads(dispatch_ns)

category = dispatch_ns.get("category", "unknown")
chain = dispatch_ns.get("chain", ["Small-Model-Route"])

# Research backing for each branch
backing = {
    "research": "RAG-evaluation-metrics: retrieval precision, recall, and faithfulness metrics inform research quality",
    "vault-maintenance": "Biomimetic offline processing consolidates memories during sleep",
    "self-improvement": "Information-feedback-loops: System 2 reflective loops improve outputs without retraining",
    "gap-filling": "Deterministic scaffolding: structured processes guide gap identification and filling",
    "chat-consolidation": "Memory consolidation: chat history becomes permanent linked notes",
    "question-answering": "RAG-evaluation-metrics: context relevance and answer relevance metrics",
    "code-editing": "Deterministic scaffolding: syntax check and auto-rollback ensure code safety",
    "fact-checking": "Calibrating-quality-assessment-gates: rubric design and calibration convert verification into reliable quality signals",
    "unknown": "Fallback to single-procedure routing when category is unclear",
}

result = json.dumps({
    "category": category,
    "procedure_chain": chain,
    "research_backing": backing.get(category, backing["unknown"]),
    "instructions": f"Run each procedure in order: {' → '.join(chain)}. Pass relevant args from the original intent to each procedure.",
}, indent=2)
```

## Usage

This procedure is the small model's front door. Call it with the user's request as the `intent` arg. It returns a JSON object with `procedure_chain` — a list of procedure names.

**Your next action is always the same:** call `execute_procedure` with the FIRST procedure name in the chain. When it finishes, call the next one. Do not improvise between steps. Do not call raw tools that the chain already covers.

## Falsifiability

This procedure is falsifiable if:
- The classifier picks the wrong category for a task (testable: run it on known tasks and check)
- A branch's procedure chain produces worse results than an unconstrained cloud model (testable: compare outputs)
- The research backing doesn't actually support the branch design (testable: read the cited research)
