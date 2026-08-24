---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-03
last_reviewed: 2026-08-15
description: Intent router that classifies incoming user requests and returns a procedure chain. Uses a small-model LLM step for classification — cheap, local, zero cloud cost. Each branch is backed by vault research.
when_to_use: when a new user request arrives and you need to know which procedure chain to run, when you want procedures to handle most of the work automatically, when the big model should delegate to deterministic procedure chains
falsifiable_if: the router classifies a task into the wrong branch, or a branch's procedure chain produces worse results than an unconstrained cloud model would
applies_to:
  - task-routing
  - procedure-composition
  - conditional-logic
  - intent-classification
  - orchestration
allowed_tools:
  - llm_generate
  - vault_search
  - code_read
research_backing:
  - "[[how-to-build-deterministic-scaffolding-for-small-language-models-so-they-can-do-]] — backs the routing approach: deterministic scaffolding with decision trees guides small models reliably"
  - "[[Information-feedback-loops-for-iterative-self-improvement-in-AI-systems-self-imp]] — backs the self-improvement branch: System 2 reflective loops improve outputs without retraining"
  - "[[Calibrating-automated-quality-assessment-gates-without-ground-truth-labels-metho]] — backs quality verification steps: rubric design and calibration convert LLM-as-judge into reliable quality signals"
  - "[[RAG-evaluation-metrics-how-to-measure-retrieval-quality-in-retrieval-augmented-g]] — backs the research branch: retrieval precision, recall, and faithfulness metrics inform research quality"
summary: master dispatcher classifies requests via small LLM to dispatch procedure chains; key topics include router architecture and classification logic.
tags:
  - procedure
  - procedures
success_count: 0
failure_count: 0
success_rate: 0.0
---

# Route-Task

## Purpose

This is the **master dispatcher** — a conditional intent router that classifies incoming user requests and dispatches to procedure chains. The routing decision is a classification task (cheap, small model), and the actual work happens in procedure chains that carry their own cartridges.

## Why This Exists

Without a dispatcher, the big model improvises workflows instead of delegating to deterministic procedure chains. This procedure closes that gap by classifying each incoming request and returning the procedure chain to run. The tradeoff is that the routing decision itself is a cheap small-model classification, while the actual work happens in procedure chains that carry their own cartridges.

## Architecture

```
Route-Task (small cartridge, thin orchestrator)
├── Step 1: Classify intent via small-model LLM call
├── Step 2: Validate classification and return the chain
│   ├── research → Research-Batch → Cross-Check-Claims → Vault-Lint
│   ├── vault-maintenance → Dream-Pass
│   ├── self-improvement → Discover-Procedures → Write-Python-Tool → Safe-Write → Proc-Step-Summary
│   ├── gap-filling → Vault-Gaps → Gap-Fill → Research-Batch
│   ├── chat-consolidation → Chat-Consolidation
│   ├── question-answering → Filter-Context-For-Query
│   ├── code-editing → Safe-Write → Proc-Step-Summary
│   ├── fact-checking → Cross-Check-Claims → Find-Contradictions
│   ├── conversational → (no chain — respond naturally, no research needed)
│   └── default → Small-Model-Route
```

## Steps

### Step 1: Classify the user's intent

1. [llm: Classify this user request into exactly one category. Reply with ONLY a JSON object, no other text.

Categories (match by keyword or meaning):
- research: learn, find, look up, investigate, study, what is, how does, explain a topic the vault doesn't cover
- vault-maintenance: cleanup, consolidate, links, lint, gaps, dream pass, organize vault
- self-improvement: build a tool, create a procedure, improve yourself, new ability, write code for the backend
- gap-filling: fill gaps, dangling links, thin notes, research gaps
- chat-consolidation: consolidate chats, save conversation, summarize session
- question-answering: answer a question, explain something (when vault already has the info)
- code-editing: edit code, fix a bug, modify .py or .js, safe_write, backend change
- fact-checking: verify claims, check sources, find contradictions, cross-check
- conversational: casual chat, greetings, confirmations, agreements, backchannels (yeah, ok, sure, sounds good, pretty good, go ahead, do that, let's do it), social responses that don't require tools or research
- unknown: none of the above clearly match

Return JSON in this exact format:
{"category": "<category>", "procedure_chain": ["<proc1>", "<proc2>", ...], "confidence": 0.0, "rationale_code": "<reason>"}

Chain mappings (use these exact procedure names):
- research: ["Research-Batch", "Cross-Check-Claims", "Vault-Lint"]
- vault-maintenance: ["Dream-Pass"]
- self-improvement: ["Discover-Procedures", "Write-Python-Tool", "Safe-Write", "Proc-Step-Summary"]
- gap-filling: ["Vault-Gaps", "Gap-Fill", "Research-Batch"]
- chat-consolidation: ["Chat-Consolidation"]
- question-answering: ["Filter-Context-For-Query"]
- code-editing: ["Safe-Write", "Proc-Step-Summary"]
- fact-checking: ["Cross-Check-Claims", "Find-Contradictions"]
- conversational: []
- unknown: ["Small-Model-Route"]

User request: {{ intent }}]

### Step 2: Validate and return the routing decision

2. ```python
import json

# Step 1 output is the LLM's JSON response
raw = prior_results[0] if prior_results else "{}"
if isinstance(raw, str):
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:]) if len(lines) > 1 else raw
        if raw.endswith("```"):
            raw = raw[:-3].strip()

try:
    dispatch = json.loads(raw)
except (json.JSONDecodeError, TypeError):
    dispatch = {"category": "unknown", "procedure_chain": ["Small-Model-Route"], "confidence": 0.0, "rationale_code": "schema_fallback"}

allowed_categories = {"research", "vault-maintenance", "self-improvement", "gap-filling", "chat-consolidation", "question-answering", "code-editing", "fact-checking", "conversational", "unknown"}
allowed_codes = {"action_signal", "clarification_or_explanation", "mixed_or_unsettled", "research_signal", "maintenance_signal", "self_improvement_signal", "gap_filling_signal", "chat_consolidation_signal", "question_answering_signal", "code_editing_signal", "fact_checking_signal", "conversational_signal", "unknown_signal", "schema_fallback"}

category = dispatch.get("category", "unknown")
chain = dispatch.get("procedure_chain", dispatch.get("chain", ["Small-Model-Route"]))
confidence = dispatch.get("confidence", 0.0)
rationale_code = dispatch.get("rationale_code", "schema_fallback")

if not isinstance(category, str) or category not in allowed_categories:
    category = "unknown"
if not isinstance(chain, list):
    chain = ["Small-Model-Route"]
chain = [p for p in chain if isinstance(p, str) and p.strip()]
if not chain and category != "conversational":
    chain = ["Small-Model-Route"]
if isinstance(confidence, (int, float)):
    confidence = max(0.0, min(1.0, float(confidence)))
else:
    confidence = 0.0
if not isinstance(rationale_code, str) or rationale_code not in allowed_codes:
    rationale_code = "schema_fallback"

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
    "conversational": "No procedure chain needed — the model responds naturally without tools or research",
    "unknown": "Fallback to single-procedure routing when category is unclear",
}

result = json.dumps({
    "category": category,
    "procedure_chain": chain,
    "confidence": round(confidence, 4),
    "rationale_code": rationale_code,
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

## Related

- [[Decision-Tree-Router]] — the three-layer router that calls this for Layer 1 intent classification
- [[Small-Model-Route]] — the default branch this dispatches to
- [[Research-Batch]] — the research chain this dispatches to
