---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-03
description: "Conditional intent router that classifies incoming user requests and dispatches to procedure chains with if-branches. Each branch is backed by vault research. Uses small model for classification, then calls run_procedure for each step in the chain. This is the master dispatcher — the first procedure to call when a new task arrives."
when_to_use: "when a new user request arrives and you need to know which procedure chain to run, when you want procedures to handle most of the work automatically, when the big model should delegate to deterministic procedure chains"
falsifiable_if: "the router classifies a task into the wrong branch, or a branch's procedure chain produces worse results than an unconstrained cloud model would"
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
---

# Route-Task

## Purpose

This is the **master dispatcher** — a conditional intent router that classifies incoming user requests and dispatches to procedure chains with if-branches. Each branch is backed by vault research, so every routing decision is traceable to evidence.

This procedure implements the key insight from [[how-to-build-deterministic-scaffolding-for-small-language-models-so-they-can-do-]]: deterministic scaffolding with decision trees guides small models reliably. The routing decision is a classification task (cheap, small model), and the actual work happens in procedure chains that carry their own cartridges.

## Architecture

```
Route-Task (small cartridge, thin orchestrator)
├── Step 1: Classify intent (small model)
├── Step 2: Dispatch to procedure chain (conditional if-branches)
│   ├── IF research → Research-Batch → Cross-Check-Claims → Structure-Research-Note → Vault-Lint
│   ├── IF vault-maintenance → Dream-Pass
│   ├── IF self-improvement → Discover-Procedures → Write-Python-Tool → Safe-Write → Proc-Step-Summary
│   ├── IF gap-filling → Vault-Gaps → Gap-Fill → Research-Batch
│   ├── IF chat-consolidation → Chat-Consolidation
│   ├── IF question-answering → Filter-Context-For-Query → (answer from vault OR route to research)
│   ├── IF code-editing → Safe-Write → Proc-Step-Summary
│   ├── IF fact-checking → Cross-Check-Claims → Find-Contradictions
│   └── IF unknown → Small-Model-Route (fallback to single-procedure routing)
└── Step 3: Return results
```

## Research Backing

Each conditional branch is backed by vault research. This ensures every routing decision is traceable to evidence, not arbitrary:

- **Routing approach** → [[how-to-build-deterministic-scaffolding-for-small-language-models-so-they-can-do-]]: "Procedural scaffolding with structured processes guides learners. Decision trees provide deterministic paths for small models."
- **Self-improvement branch** → [[Information-feedback-loops-for-iterative-self-improvement-in-AI-systems-self-imp]]: "System 2 reflective loops — pause, critique, identify gaps, refine — improve outputs without retraining."
- **Quality verification steps** → [[Calibrating-automated-quality-assessment-gates-without-ground-truth-labels-metho]]: "Investing in rubric design, bias testing, and human calibration converts LLM-as-judge from a misleading shortcut into a reliable quality signal."
- **Research branch** → [[RAG-evaluation-metrics-how-to-measure-retrieval-quality-in-retrieval-augmented-g]]: "Retrieval precision, recall, and faithfulness metrics inform whether research quality is sufficient."

## Steps

### Step 1: Classify the intent

The small model classifies the user's request into one of these task types. This is a classification task — cheap and deterministic with scaffolding.

1. ```python
import json

intent = args.get("intent", "")
if not intent:
    result = json.dumps({"error": "intent argument required"})
else:
    prompt = f"""You are a task classifier. Classify the following user request into exactly ONE category.

Categories:
- research: User wants to learn about a topic, find information, or fill a knowledge gap
- vault-maintenance: User wants vault cleanup, consolidation, linking, or dream pass
- self-improvement: User wants VaultBot to improve itself — new procedures, code edits, tool creation
- gap-filling: User wants to fill knowledge gaps, dangling links, or thin notes
- chat-consolidation: User wants to consolidate chat history into memory notes
- question-answering: User is asking a question that should be answered from vault knowledge
- code-editing: User wants to edit backend Python source code
- fact-checking: User wants to verify claims, check sources, or find contradictions
- unknown: Cannot classify into any of the above

User request: {intent}

Return JSON: {{"category": "category-name", "confidence": "high|medium|low", "reason": "brief reason"}}
Return ONLY the JSON."""
    classification = llm_generate(prompt)
    result = classification
```

### Step 2: Dispatch to procedure chain based on classification

This is the **conditional if-branch** — the core of the procedure. Based on the category from Step 1, dispatch to a chain of procedures. Each branch cites its research backing.

2. ```python
import json as _json

try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"category": "unknown", "reason": "could not parse classification"}

category = parsed.get("category", "unknown")
chains = {
    "research": {
        "chain": ["Research-Batch", "Cross-Check-Claims", "Structure-Research-Note", "Vault-Lint"],
        "backing": "RAG-evaluation-metrics: retrieval precision, recall, and faithfulness metrics inform research quality"
    },
    "vault-maintenance": {
        "chain": ["Dream-Pass"],
        "backing": "Biomimetic offline processing consolidates memories during sleep"
    },
    "self-improvement": {
        "chain": ["Discover-Procedures", "Write-Python-Tool", "Safe-Write", "Proc-Step-Summary"],
        "backing": "Information-feedback-loops: System 2 reflective loops improve outputs without retraining"
    },
    "gap-filling": {
        "chain": ["Vault-Gaps", "Gap-Fill", "Research-Batch"],
        "backing": "Deterministic scaffolding: structured processes guide gap identification and filling"
    },
    "chat-consolidation": {
        "chain": ["Chat-Consolidation"],
        "backing": "Memory consolidation: chat history becomes permanent linked notes"
    },
    "question-answering": {
        "chain": ["Filter-Context-For-Query"],
        "backing": "RAG-evaluation-metrics: context relevance and answer relevance metrics"
    },
    "code-editing": {
        "chain": ["Safe-Write", "Proc-Step-Summary"],
        "backing": "Deterministic scaffolding: syntax check and auto-rollback ensure code safety"
    },
    "fact-checking": {
        "chain": ["Cross-Check-Claims", "Find-Contradictions"],
        "backing": "Calibrating-quality-assessment-gates: rubric design and calibration convert verification into reliable quality signals"
    },
    "unknown": {
        "chain": ["Small-Model-Route"],
        "backing": "Fallback to single-procedure routing when category is unclear"
    }
}

selected = chains.get(category, chains["unknown"])
result = _json.dumps({
    "category": category,
    "confidence": parsed.get("confidence", "low"),
    "reason": parsed.get("reason", ""),
    "procedure_chain": selected["chain"],
    "research_backing": selected["backing"],
    "instructions": f"Run each procedure in order: {' → '.join(selected['chain'])}. Pass relevant args from the original intent to each procedure."
})
```

### Step 3: Return the routing decision with chain and backing

3. ```python
import json as _json
try:
    parsed = _json.loads(output)
except Exception:
    parsed = {"category": "unknown", "procedure_chain": ["Small-Model-Route"], "research_backing": "fallback"}

result = _json.dumps(parsed, indent=2)
```

## Usage

The big model calls this procedure when a new user request arrives. The procedure:
1. Classifies the intent (small model, cheap)
2. Returns a procedure chain with research backing
3. The big model then calls `run_procedure()` for each procedure in the chain in order

The big model retains control over *when* to call each procedure in the chain — it can skip steps if conditions aren't met, or add steps if the chain needs extension. This is intentional: the chain is a *recommendation*, not a forced sequence. The conditional logic is in the *branching*, not in forcing every step.

## Conditional Logic Notes

This is the first procedure in the vault with true conditional if-branch logic that dispatches to different procedure chains based on a classification result. The pattern is:

1. **Classify** (small model, deterministic with scaffolding)
2. **Branch** (conditional if-branch based on classification)
3. **Chain** (sequence of procedures for the branch)
4. **Back** (each branch cites research that backs it)

This pattern can be replicated in other procedures. See [[Procedure-Composition-Patterns]] for the general template.

## Falsifiability

This procedure is falsifiable if:
- The classifier picks the wrong category for a task (testable: run it on known tasks and check)
- A branch's procedure chain produces worse results than an unconstrained cloud model (testable: compare outputs)
- The research backing doesn't actually support the branch design (testable: read the cited research)