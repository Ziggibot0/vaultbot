# Chat: we got farther than that. you were doing research 

## 2026-07-26 23:14 UTC

**User:** we got farther than that. you were doing research on something important and i can't remember what it was. oh that's right! procedure compiling with markdown files. you said you found a gold lead on that and you were gonna do more precise queries. look at what you already have about procedural prompting and assess what you still don't know enough about yet

**Assistant:** Okay Sean, I've dug through everything. Here's the full picture.

## The Gold Lead

The key find was a paper called **"Compile, Then Page: Executable SOP Programs and a Capability-Gated Runtime for Procedural LLM Agents"** (arXiv 2607.11346). It showed up in two research notes:

- `SOP-standard-operating-procedure-AI-agent-execution...`
- `executable-standard-operating-procedures-for-LLM-agents...`

The core idea: **compile markdown SOPs into executable pseudo-code, run them through a program-guided stack machine that pages the active frame, while the LLM does semantic execution**. The runtime is **capability-gated** — it checks whether the LLM can handle each step before letting it execute. This is exactly "procedure compiling with markdown files."

A second lead came from an **FSM-based AI agent control** concept (from a GitHub project): an embedded FSM bot acts as a deterministic control layer. The FSM maintains task state; the LLM generates content within each state. Transitions are deterministic — planning→coding when a valid plan is output, coding→debugging when tests fail. This mitigates LLM nondeterminism with a structured, verifiable workflow.

A third lead: **procedural hallucination** (arXiv 2602.19239) — a formal definition of *why* models skip steps. "Procedural hallucination" = failure to follow a verifiable, prompt-grounded specification even when the correct answer is in-context. The hidden state encodes the answer but the output ignores it. This is the failure mode that step-gating is supposed to prevent.

## What I Already Have (Solid)

| Topic | Notes | Quality |
|---|---|---|
| Procedural bootstrap plan | [[Procedural-Bootstrap-and-Evolution-Plan]] (44KB) | Deep — 9 parts, full architecture |
| 6 procedural notes | How-to-Structure, How-to-Evaluate, How-to-Verify, How-to-Decide, How-to-Write-Tool, How-to-Organize | Working, all pass lint |
| `procedure_tracker.py` | 587 lines — pass/fail logging, promotion/flagging | Built and integrated |
| Deterministic scaffolding | [[Deterministic-Scaffolding-for-Small-Models]] — the "sandwich pattern" | Solid, sourced from OpenEmpower case study |
| Small model path to AGI | [[Small-Model-Path-to-AGI]] — 6 strategies | Good vision doc |
| FSM control flow concept | Research note on FSM bots for AI agents | Thin — one source, abstract level |
| Procedural hallucination | Research note defining the failure mode | Thin — definitions only, no mitigations |
| "Compile, Then Page" | Two research notes mentioning the paper | **Very thin** — abstract-level facts only |

## What I Still Don't Know (The Gaps)

**1. How "Compile, Then Page" actually works.** I only have abstract-level facts. I don't know:
- What does the compilation step look like? How do you turn a markdown SOP into executable pseudo-code?
- What is the PG stack machine architecture? How does frame paging work?
- How does capability gating actually work? What are the specific gates?
- What were the empirical results? Did it outperform free-form prompting?

**2. How to implement step-gating in VaultBot's architecture.** The FSM concept is interesting but:
- How do I parse a markdown procedure file into FSM states?
- How do I force the LLM to execute one step at a time — what's the enforcement mechanism?
- How do I verify a step was completed before advancing?
- Where does this hook into `main.py`'s chat handler?

**3. What format should "executable" markdown procedures be?** The research mentions "machine-readable SOP constraints" but:
- What format? YAML frontmatter? Structured markdown? JSON schema?
- How do you represent conditionals, loops, and branching in markdown?
- How does this map to my existing procedural note schema (`type: procedure`, `status`, etc.)?

**4. Procedural hallucination mitigation for 30B models.** The paper explains *why* models skip steps, but:
- What techniques actually reduce procedural hallucination in small models?
- Does step-gating actually help, or just move the failure point?
- Are there attention manipulation or structured output techniques that work?

**5. Existing open-source implementations.**
- Is there code released for "Compile, Then Page"?
- Do LangGraph, CrewAI, AutoGen, or any framework support markdown-driven control flow?
- Are there other "markdown → executable procedure" systems I haven't found?

**6. How the Karpathy llm-wiki pattern connects.** The research surfaced Karpathy's concept: "Obsidian is the IDE; the LLM is the programmer; the wiki is the codebase." But this is about knowledge management, not procedure execution. Is there a way to merge the two — where markdown procedures are both knowledge *and* executable code?

---

**Bottom line:** The gold lead is real — "Compile, Then Page" describes exactly what you want (markdown SOPs compiled to executable code with deterministic control flow). But the research notes are thin. They captured the *what* but not the *how*. The next move is precise queries on that paper's actual mechanism, the FSM control flow pattern, and how to implement step-gating in the existing backend.

Want me to start researching those gaps now?

<details>
<summary>Thinking process</summary>

Sean is asking me to look at what I already have about procedural prompting — specifically "procedure compiling with markdown files" — and assess what I still don't know enough about. He says I found a "gold lead" and was going to do more precise queries.

Let me search the vault for everything related to procedural prompting, procedure compiling, markdown procedures, etc. to understand what I have and identify gaps.Now let me look at the specific research notes about "markdown files as executable procedures" and the "procedural prompting" research notes to understand what the "gold lead" was. Let me read those research notes more carefully.Now I see it. There's a research note about "SOP standard operating procedure AI agent execution — compiling procedures into executable code for LLM agents" which mentions "Compile, Then Page: Executable SOP Programs and a Capability-Gated Runtime for Procedural LLM Agents." That sounds like the gold lead — compiling markdown procedures into executable code for LLM agents. Let me read that note more carefully, and also the procedural prompting one that mentions FSMs.

Let me read the full SOP research note and the procedural-prompting one.I see two very relevant research notes:
1. `SOP-standard-operating-procedure-AI-agent-execution-compiling-procedures-into-ex.md`
2. `executable-standard-operating-procedures-for-LLM-agents-compiling-SOPs-into-prog.md`

These are the "gold lead" — compiling procedures (markdown SOPs) into executable code for LLM agents. Let me read both of these in full, plus the procedural-prompting one that mentions FSMs.Now I have a clear picture. Let me also read the "Can 30B parameter LLM models reliably follow step-by-step procedural instructions" note and the "LLM agent control flow enforcement" note, since those are directly relevant.Now I have a comprehensive picture. Let me synthesize what I have and what I'm still missing.

## What I Have (the research notes)

### The Gold Lead: "Compile, Then Page" (arXiv 2607.11346)
The key paper found is **"Compile, Then Page: Executable SOP Programs and a Capability-Gated Runtime for Procedural LLM Agents"**. This is the gold lead Sean is referring to. The core idea:
- Compile machine-readable SOP constraints into executable pseudo-code
- Run them with a program-guided (PG) stack machine that pages the active frame
- The LLM performs semantic execution while the runtime enforces control flow
- Capability-gated runtime: the runtime checks if the LLM has the capability for each step before allowing it to execute

This was found in two research notes:
1. `SOP-standard-operating-procedure-AI-agent-execution-compiling-procedures-into-ex.md`
2. `executable-standard-operating-procedures-for-LLM-agents-compiling-SOPs-into-prog.md`

### Other Research Notes on Procedural Prompting

2. **FSM-based AI agent control** (from `procedural-prompting-LLM-step-by-step-forced-execution...`):
   - An FSM bot that acts as a control layer for AI agents
   - The FSM maintains task state, AI agents generate and execute code within those states
   - Transitions are deterministic (planning→coding when valid plan output, coding→debugging when tests fail)
   - Mitigates nondeterminism of pure LLM approaches with structured, verifiable workflow

3. **Procedural hallucination** (from `Can-30B-parameter-LLM-models-reliably-follow...`):
   - "Procedural hallucination" = failure to follow a verifiable, prompt-grounded specification even when the correct value is in-context
   - Unlike factual hallucination, this is about the model's ability to faithfully execute a specification given in its own context
   - Has a causal/information-theoretic explanation: hidden state encodes the answer but output ignores it

4. **Markdown as executable procedures** (from `markdown-files-as-executable-procedures...`):
   - Karpathy's llm-wiki: "Obsidian is the IDE; the LLM is the programmer; the wiki is the codebase"
   - The LLM reads from markdown files but never modifies immutable ones
   - Wiki is incrementally built and maintained

5. **LLM agent control flow enforcement** (from `LLM-agent-control-flow-enforcement...`):
   - Mostly noise — returned irrelevant results (datadog agent, focus group, etc.)

6. **ReAct chain-of-thought forced execution** (from `ReAct-chain-of-thought...`):
   - Also mostly noise — speculative execution attacks, TTRPG

### What's Already Built (the procedural bootstrap)
- 6 procedural notes (How-to-Structure-a-Research-Note, How-to-Evaluate-Source-Credibility, How-to-Verify-Claims, How-to-Decide-When-to-Research-vs-Answer, How-to-Write-a-Python-Tool, How-to-Organize-a-Knowledge-Base)
- `procedure_tracker.py` — logs pass/fail, detects failing/stale procedures, promotes/flags
- System prompt rule: "If vault context contains notes with `type: procedure`, follow their steps"
- Phases 0-3 of the procedural bootstrap are built

## What I Still Don't Know (the gaps)

Now, the key question: what's still missing for "procedure compiling with markdown files"?

1. **How does "Compile, Then Page" actually work in detail?** The research note only has the abstract-level facts. I don't know:
   - What does the "compilation" step look like? How do you turn a markdown SOP into executable pseudo-code?
   - What is the "PG stack machine" architecture? How does it page frames?
   - How does capability gating work? What are the specific gates?
   - What were the empirical results? Did it actually work better than free-form prompting?

2. **How to implement step-gating in VaultBot's current architecture?** The FSM bot concept is interesting but:
   - How would I integrate an FSM into the existing chat handler in main.py?
   - How do I parse a markdown procedure file into FSM states?
   - How do I force the LLM to execute one step at a time (step gates)?
   - What's the mechanism for verifying a step was completed before moving to the next?

3. **What's the actual format for "executable" markdown procedures?** The research mentions "machine-readable SOP constraints" but:
   - What format are these in? YAML? JSON? Structured markdown?
   - How do you represent conditionals, loops, and branching in markdown?
   - How does this map to the existing procedural note schema (type: procedure, status, etc.)?

4. **Procedural hallucination mitigation for 30B models:**
   - The "Attention Deficits" paper explains WHY models skip steps, but:
   - What are the specific techniques to mitigate procedural hallucination in small models?
   - Does step-gating actually reduce procedural hallucination?
   - Are there other techniques (attention manipulation, structured output formats, etc.)?

5. **How does the Karpathy llm-wiki pattern relate?** The research found Karpathy's llm-wiki concept:
   - "Obsidian is the IDE; the LLM is the programmer; the wiki is the codebase"
   - But this is more about knowledge management than procedure execution
   - Is there a way to merge the two concepts?

6. **Existing frameworks that do this:**
   - Are there open-source implementations of "compile markdown to executable procedure"?
   - LangGraph, CrewAI, AutoGen — do any of them support markdown-driven control flow?
   - The "Compile, Then Page" paper — is there a code release?

So the gold lead was the "Compile, Then Page" paper (arXiv 2607.11346) which describes compiling SOPs into executable pseudo-code with a capability-gated runtime. The research notes are thin — they only captured the abstract-level facts. The follow-up queries listed in the research notes show the gaps weren't filled yet.

Let me now present this to Sean clearly.

</details>