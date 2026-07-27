# Chat: is there any additional research into subfields th

## 2026-07-26 02:31 UTC

**User:** is there any additional research into subfields that you must do in order to get better coverage of these concepts?

**Assistant:** Here's what I found after checking the vault against each gap:

## What the Vault Already Covers ✅

| Topic | Notes | Coverage |
|---|---|---|
| Procedural bootstrapping | [[Procedural-Bootstrap-and-Evolution-Plan]], self-improving agent research | Strong — the "find don't author" approach is validated |
| Few-shot prompting for small models | 5 sources, 21 facts on prompt-time few-shot | Good for prompt design, but NOT for vault-stored exemplars |
| Deterministic scaffolding | [[Deterministic-Scaffolding-for-Small-Models]], sandwich pattern | Strong |
| Failure logging & context tracking | Research + `procedure_tracker.py` built | Strong |
| A/B testing of prompts | 11 sources, 18 facts | Good for prompts, but NOT for quality gate calibration |
| Source credibility evaluation | [[How-to-Evaluate-Source-Credibility]] | Good for pre-citation, NOT for post-synthesis verification |

## What I Need to Research ❌ — 5 Subfields

**1. LLM output verification / claim-level fact-checking** (Epistemology)
The vault has source credibility evaluation — checking sources *before* citing them. But I have zero research on verifying that synthesized claims actually *match* their cited sources after generation. This is the gap between "good sources" and "faithful synthesis." I need to research: automated claim verification, hallucination detection, faithfulness metrics.

**2. RAG evaluation metrics** (Hermeneutics)
I have no research on how to measure whether FUSED retrieval finds the right notes. No precision, recall, faithfulness, or answer relevance metrics. I can't improve what I can't measure.

**3. Context window management for graph-based retrieval** (Hermeneutics)
No research on truncation strategies, hop radius optimization, or context prioritization. As the vault grows past 154 notes, subgraphs will exceed the context window. I need to know how other graph-RAG systems handle this.

**4. Calibration of automated quality gates without ground truth** (Epistemology)
`vault_lint` passes/fails notes, but I have no way to know if its judgments are *correct*. I need research on calibrating automated quality assessment when labeled data is sparse — Sean's corrections are the only ground truth signal.

**5. Vault-stored exemplar design** (Ontology)
The existing few-shot research is about prompt-time examples ("put 2-5 examples in the prompt"). But [[Small-Model-Path-to-AGI]] strategy #2 calls for *permanent* exemplar notes in the vault that a model pattern-matches against during retrieval. That's a different design problem — how do you structure an exemplar note so it's useful as a retrieval target, not just as a prompt injection?

---

**Bottom line:** 5 subfields need research before I can confidently build the Tier 2 and Tier 3 gaps. Tier 1 (writing more procedures) I can start now — the vault has enough coverage. Want me to kick off the 5 research topics?

<details>
<summary>Thinking process</summary>

Sean is asking whether I need to research any subfields to get better coverage of the concepts in the self-assessment. Let me think about what I actually know vs. what I'm missing.

The self-assessment identified 10 gaps across three layers. For each gap, I need to ask: do I have enough vault knowledge to act, or do I need to research subfields first?

Let me think through each tier:

**Tier 1 — Ontology gaps:**
1. Write more procedural notes — I have the [[Procedural-Bootstrap-and-Evolution-Plan]] and [[How-to-Structure-a-Research-Note]]. But do I know what procedures other agent systems use? Do I know what a "minimal viable procedure set" looks like for a research assistant? I should research this.
2. Example notes — I know what good notes look like (I've written some), but do I know the research on few-shot exemplar design for small models? How many examples? What format? This could use research.
3. Note taxonomy — I have ontology research but it's general. Do I have research on knowledge management taxonomies specifically for agent systems? Not really.

**Tier 2 — Epistemology gaps:**
4. Source-claim verification — do I know how agent systems fact-check LLM outputs against sources? No. I need to research this.
5. Calibration — do I know how to calibrate automated quality gates without ground truth? No. Need research.
6. Source change detection — this is more of an engineering task. I know how to hash files. Probably don't need research.

**Tier 3 — Hermeneutics gaps:**
7. Connect textbook indexes — this is an engineering/curation task, not a research question.
8. Synthesis procedure — do I know how to scaffold synthesis for small models? I have some research on deterministic scaffolding, but not specifically on synthesis procedures. Could use research.
9. Retrieval quality testing — do I know RAG evaluation metrics? I have some research on graph retrieval vs graph reasoning, but not on evaluation metrics specifically. Need research.
10. Context window management — do I know strategies for managing context in graph-based retrieval? Not really. Need research.

So the subfields I need to research are:
1. **Procedural knowledge for AI agents** — what procedures do other agent systems use? What's the minimal set?
2. **Few-shot exemplar design for small models** — how to design effective examples for 30B-class models
3. **LLM output fact-checking / verification** — how to verify synthesized claims against source documents
4. **RAG evaluation metrics** — how to measure retrieval quality (precision, recall, etc.)
5. **Context window management for graph-based retrieval** — truncation strategies, hop radius optimization
6. **Calibration of automated quality gates** — how to calibrate without ground truth

Let me also check what the vault already has on these topics to avoid redundant research.

Actually, let me search the vault for some of these topics first.Now I have a clear picture of what the vault already covers. Let me map each gap to what exists vs. what's missing:

**1. Procedural knowledge for AI agents** — I have:
- [[Procedural-Bootstrap-and-Evolution-Plan]] — the plan
- Research on self-improving AI agent systems, deterministic agent frameworks, failure logging, A/B testing
- Research on few-shot prompting for small models
- Research on deterministic scaffolding

What I DON'T have: research on what specific procedures other agent systems use (like Voyager's skill library, AutoGPT's procedures, etc.). I know "procedures are found not authored" but I don't have a curated list of what procedures to find.

**2. Few-shot exemplar design** — I have:
- Research on few-shot prompting examples and techniques for small models (5 sources, 21 facts)
- Research on Microsoft Hermes Qwen function calling

What I DON'T have: research on how to design effective exemplar notes specifically for a knowledge vault — not just prompt templates, but permanent exemplar documents that a model can pattern-match against. The existing research is about prompt-time few-shot, not vault-stored exemplars.

**3. LLM output fact-checking / claim verification** — I have:
- [[How-to-Evaluate-Source-Credibility]] — but this is about evaluating sources BEFORE citing them, not verifying that synthesized claims MATCH cited sources
- No research on post-generation fact-checking, claim verification against source documents, or hallucination detection

This is a clear gap. I need to research: how do you verify that an LLM's synthesized output actually matches the sources it claims to cite? What methods exist for automated claim verification?

**4. RAG evaluation metrics** — I have:
- Research on A/B testing of prompts
- Research on graph retrieval vs graph reasoning (but it's mostly about math research, not RAG eval)
- No research on RAG evaluation metrics (precision, recall, F1, faithfulness, answer relevance)

This is a clear gap. I need to research: how do you measure RAG retrieval quality? What metrics exist? How do you build a test suite?

**5. Context window management** — I have:
- No research on context window management for graph-based retrieval
- No research on truncation strategies, hop radius optimization, or context prioritization

This is a clear gap.

**6. Calibration of automated quality gates** — I have:
- Research on A/B testing (but for prompts, not for quality gates)
- No research on calibrating automated quality assessment without ground truth

This is a gap.

So the subfields I need to research are:

1. **LLM output verification / claim-level fact-checking** — how to verify synthesized claims against source documents (epistemology gap #4)
2. **RAG evaluation metrics** — how to measure retrieval quality, precision, recall, faithfulness (hermeneutics gap #9)
3. **Context window management for graph-based retrieval** — truncation strategies, hop radius, context prioritization (hermeneutics gap #10)
4. **Calibration of automated quality gates without ground truth** — how to calibrate when you don't have labeled data (epistemology gap #5)
5. **Vault-stored exemplar design** — how to design permanent exemplar notes for pattern-matching, as distinct from prompt-time few-shot (ontology gap #2)

The vault already has good coverage of:
- Procedural bootstrapping (the plan, the research)
- Few-shot prompting for small models (prompt-time)
- Deterministic scaffolding (the sandwich pattern)
- Failure logging and context tracking
- A/B testing of prompts
- Source credibility evaluation
- Self-improving agent systems

So I need 5 research topics. Let me tell Sean what I found and what I need to research.

</details>