---
type: architecture-plan
status: draft
baseline: true
created: 2026-07-26
tags:
  - architecture
  - self-improvement
  - procedures
  - bootstrap
  - evolution
summary: "# PROCEDURAL BOOTSTRAP AND EVOLUTION PLAN

## SUMMARY"
---

# Procedural Bootstrap and Evolution Plan

## The Problem

Two questions from the operator:

1. **Bootstrap**: How do we get procedures into the vault without needing an LLM to author them first?
2. **Evolution**: If we seed procedures, how does the system grow and not get stuck with stale methods?

These are the chicken-and-egg at the center of the [[Small-Model-Path-to-AGI]] vision. The goal is a framework where a 30B local model works from day 1, and the cloud model's only job is to make itself redundant as fast as possible. See [[Deterministic-Scaffolding-for-Small-Models]] for the research backing.

---

## Part 1: The Bootstrap Problem

### The Key Insight: Procedures Are Found, Not Authored

The internet already contains procedures for almost everything VaultBot needs to do. The [[vaultbot/Vault-Knowledge-Only-Directive]] says the vault is the only knowledge source. The research engine (see `research_engine.py`) is LLM-light: it scrapes, extracts, corroborates, and synthesizes deterministically. The LLM only sees the finished, sourced summary. This means:

- **No LLM invents procedures.** The research engine finds what someone else already wrote and stores it.
- **The LLM's role in research is formatting and synthesis, not creation.** The content comes from the web.
- **The research engine already works without procedural guidance.** It's deterministic enough to bootstrap.

### What Procedures Already Exist in the Vault

The vault already has directives that ARE procedures, just written for LLM interpretation:

| Existing Note | What It Does | How to Make It More Deterministic |
|---|---|---|
| [[Autonomy-Directive]] | "Operate without permission" | Already deterministic: if-then rules for when to act vs report |
| [[IDK-Fallback-Directive]] | "Say IDK when you don't know" | Already a decision tree: vault has it -> answer; empty + research works -> research; empty + research down -> IDK |
| [[vaultbot/Vault-Knowledge-Only-Directive]] | "Vault is the only knowledge source" | Already deterministic: check vault, if empty say IDK |
| [[No-Wikipedia-Directive]] | "Never use Wikipedia" | Already enforced at code level: `_BLOCKED_DOMAINS` in `duckduckgo_client.py` and `research_engine.py` |
| [[the operator-Communication-Preferences]] | "Keep it short, lead with outcome" | Already deterministic: bullet points, bottom line up front |

The shift is not creating new procedures. It's refactoring existing ones into more explicit if-then rules and finding the missing ones through research.

### The Minimal Seed

The vault does not start from zero. It starts with:

1. **Existing directives** (5 notes, already in vault) -- refactored into deterministic rules
2. **Research engine** (already works, `research_engine.py`, 585 lines, deterministic pipeline)
3. **Knowledge curriculum** (already works, `knowledge_curriculum.py`, 902 lines, Voyager-style gap detection)
4. **Autonomous researcher** (already works, `autonomous_researcher.py`, 495 lines, background gap-filling)
5. **Validation tools** (already work: `vault_lint`, `safe_write`, `vault_guard`)

What's missing is a small set of procedural notes for higher-level tasks:

| Procedure Needed | Where to Find It Online |
|---|---|
| Format-Research-Note | Zettelkasten guides, academic writing templates |
| Check-Source-Credibility | Research methodology guides, media literacy frameworks |
| Research-Decision-Gate | Agent framework decision trees, RAG architecture docs |
| Build-Tool | Python style guides, tool scaffolding patterns |
| Organize-Vault | PKM best practices, Obsidian guides |
| Validate-LLM-Output | Schema validation patterns, QA checklists |

> **Naming convention:** Procedures are tools, not tutorials. Name them
> like tools (e.g., 'Dream-Pass', 'Verify-Claims', 'Procedure-Creator'),
> never 'How to X'. The procedure validator rejects 'How to' prefixes.

The research engine finds these. The vault stores them. The 30B follows them. No LLM authors them.

### The Bootstrap Loop

```
Day 1:
  1. Existing directives refactored into deterministic rules (5 notes)
  2. Research engine finds 5-10 core procedures online, stores them
  3. 30B starts operating: follows procedures, produces output, validation checks it

Day 2-N:
  1. 30B hits a wall (validation fails, or IDK fires)
  2. Failure logged -> background researcher re-researches the gap
  3. New procedure found -> stored -> 30B uses it next time
  4. Old procedures age -> re-researched on schedule
  5. Bad procedures fail -> replaced by better ones
  6. Good procedures succeed -> verified, promoted
```

The loop is: **try -> fail -> find better way -> store it -> try again.** That's not an LLM reasoning loop. That's a deterministic feedback loop with an LLM as the execution engine.

### Circular Dependency Analysis

**Concern**: The research engine needs procedures to know how to research. But we're using the research engine to find procedures.

**Resolution**: The research engine already works without procedural guidance. Its pipeline is deterministic: extract key terms -> multi-round search -> fetch sources -> extractive synthesis -> gap detection -> follow-up queries. No procedure tells it how to do this. The procedures we're finding are for *higher-level* tasks (how to write notes, how to make decisions, how to validate output), not for the research engine itself.

The research engine is the bootstrap layer. It's already built. It doesn't need procedures to function. It needs procedures to help the *model* that uses its output.

---

## Part 2: The Evolution Problem

### Four Deterministic Evolution Mechanisms

#### Mechanism 1: Failure-Driven Evolution (The Keystone)

Every time the 30B follows a procedure and the output fails validation, that failure is logged. When failures on a given procedure exceed a threshold, the background researcher re-researches the topic and updates the procedure.

```
procedure_used -> output -> validation -> pass/fail
                                    |
                                    v fail
                            failure_log[procedure] += 1
                                    |
                                    v threshold exceeded
                            re_research(procedure.topic)
                            update_note(procedure, new_findings)
```

This is mechanical. No LLM decides "this procedure is stale" -- a counter trips a threshold.

**What gets logged:**
- Timestamp
- Procedure name (which procedural note was followed)
- Task description (what was being attempted)
- Validation result (pass/fail)
- Validation tool (which checker caught it: vault_lint, safe_write, user correction)
- Error details (what specifically failed)
- Severity (low/medium/high)

**Where the log lives:** `vaultbot_backend/procedure_failure_log.json` -- a simple JSON file, read by the background researcher each cycle.

**Threshold:** Start with a simple fixed threshold (e.g., 3 failures in 30 days). Make it adaptive later based on usage volume.

#### Mechanism 2: Time-Driven Re-Research

Every procedural note gets a `last_reviewed` date in its frontmatter. The background researcher periodically checks for notes where `last_reviewed` is older than `review_interval_days`. Those get re-researched automatically.

```
For each procedural note:
  if (now - last_reviewed) > review_interval_days:
    re_research(note.topic)
    compare new findings with existing procedure
    if different: update note, archive old version in git
    update last_reviewed
```

**Default interval:** 90 days. Different procedure types could have different intervals (fast-moving topics shorter, stable topics longer).

This catches the case where the world has moved on but the procedure hasn't. No LLM judgment needed -- it's a date comparison.

#### Mechanism 3: Source-Driven Updates

Every procedural note cites its sources. The background researcher can periodically check whether those sources have been updated (HTTP last-modified header, page content hash). If a source has changed, the note is flagged for re-research.

```
For each procedural note:
  for each source_url in note.sources:
    current_hash = hash(fetch(source_url))
    if current_hash != note.source_hashes[source_url]:
      flag_for_research(note)
```

This catches the case where a specific source has been updated, even if the review interval hasn't elapsed yet.

**Limitation:** Not all sources support last-modified headers. Content hashing requires fetching the page, which is expensive. This mechanism is lower priority than the other two.

#### Mechanism 4: Quality-Driven Promotion

New procedures start as `status: experimental`. They run alongside the old procedure on a few tasks. If the new one produces fewer validation failures, it graduates to `status: verified` and becomes the default. The old one is archived.

```
New procedure found:
  status: experimental
  Run on N tasks, track pass/fail rate

After N tasks:
  if success_rate > threshold:
    status: verified
    old_procedure.status: archived
  else:
    status: rejected
```

This is A/B testing, but the metric is deterministic: validation pass rate, not LLM judgment.

**the operator's role here:** the operator's corrections are the highest-weight quality signal. If the operator says "this is wrong," the procedure is immediately flagged for re-research, regardless of the failure count. the operator doesn't need to write procedures -- he just needs to correct bad output. The system figures out why the output was bad and finds a better procedure.

---

## Part 3: Procedural Note Schema (UNIFIED — 2026-08-10)

This is the **single authoritative format** for all procedure notes. The compiler, validator, and Build-Procedure factory all agree on this format. See [[Build-Procedure]] for the factory that enforces it.

### Step Format

Every step MUST use this exact format:

```markdown
### Step N: Short human-readable summary

N. ```python
code here
```

### Step N: Short human-readable summary

N. [llm: instruction here]
```

- The `### Step N:` header provides the human-readable description (shown in logs and progress callbacks).
- The `N.` prefix on the code fence or LLM tag makes step numbers visible in raw markdown.
- Code steps use ```python blocks. LLM steps use `[llm: ...]` tags.
- NEVER use bare `N.` without a `### Step N:` header above it.
- NEVER use `[vllm:]`, `[model_cartridge:]`, or any other tag format — only `[llm: ...]`.
- Decimal step numbers (e.g., 1.5, 2.5) are allowed for inserting steps between existing ones without renumbering.

### Required Frontmatter

```yaml
---
type: procedure
status: experimental | active | verified | archived
model_cartridge: small  # or big, or vision
created: YYYY-MM-DD
description: "one-line summary for retrieval — specific enough that RAG surfaces it"
when_to_use: "SITUATIONS that trigger this procedure, not topics"
falsifiable_if: "specific, observable failure condition"
allowed_tools:
  - tool_name
summary: Short-Title
tags:
  - procedure
  - procedures
---
```

- `model_cartridge: small` for classification, extraction, routing, formatting.
- `model_cartridge: big` only for novel reasoning or complex synthesis.
- `status` should be `experimental` for new procedures.

### Optional Frontmatter

```yaml
last_reviewed: YYYY-MM-DD
applies_to:
  - category
provides:
  - Sub-Procedure-Name
```

### Standardized Sections (in this order)

1. `## When to Run This` — trigger conditions (required)
2. `## Inputs` — documented args if the procedure takes any (required if args exist)
3. `## Steps` — the machine-executable steps (required)
4. `## Why This Exists` — the failure or gap that spawned this procedure
5. `## Related` — wikilinks to related notes

Additional sections (Architecture, History, Composition Map, etc.) are optional and go after `## Related`.

### The `falsifiable_if` Field

Every procedural note is a hypothesis: "following these steps produces a good result." The `falsifiable_if` field makes the failure condition explicit.

This is not philosophy — it's operational. When the failure log records a failure, the system checks `falsifiable_if` to confirm the failure actually falsifies this procedure (vs. a different cause like a bad source or a model error). If the failure matches the condition, the procedure's `failure_count` increments and `status` moves toward `rejected`. If it doesn't match, the failure is logged against a different cause.

**Only procedural notes get this field.** Factual notes, research notes, and concept notes don't — they're not testable claims, they're knowledge. The scientific method applies to the *process*, not the *content*.

---

## Part 4: Failure Log Schema

### File: `vaultbot_backend/procedure_failure_log.json`

```json
{
  "entries": [
    {
      "timestamp": "2026-07-26T12:00:00Z",
      "procedure": "How-to-Structure-a-Research-Note",
      "task": "research quantum computing basics",
      "validation_result": "fail",
      "validation_tool": "vault_lint",
      "error_details": "3 broken wikilinks, no frontmatter, empty sections",
      "severity": "medium"
    }
  ],
  "summary": {
    "How-to-Structure-a-Research-Note": {
      "total": 5,
      "failures": 3,
      "passes": 2,
      "last_failure": "2026-07-26T12:00:00Z"
    }
  }
}
```

The summary section is computed from entries and used by the background researcher to trigger re-research when `failures` exceeds the threshold.

---

## Part 5: Risk Analysis

### Risk 1: The Research Engine Finds Bad Procedures

**The internet has bad advice too.** A procedure found online might be wrong, outdated, or misleading.

**Mitigation:**
- The validation layer catches bad output. If a procedure consistently produces bad output, it gets replaced.
- The research engine already has source quality scoring (corroboration across multiple sources, source count). Bad procedures from bad sources will have low corroboration.
- the operator's corrections are ground truth. If the operator says "this is wrong," that overrides everything.

**Residual risk:** A bad procedure that produces output that *passes validation but is actually wrong.* This is the "looks correct but isn't" problem. The only defense is the operator's judgment. The system should surface low-confidence procedures for the operator's review.

### Risk 2: The 30B Can't Follow a Procedure

**A procedure might be too complex for a 30B to execute**, even if it's correct.

**Mitigation:**
- Procedures should be simple, step-by-step, with clear if-then rules. The [[Deterministic-Scaffolding-for-Small-Models]] research shows that structured outputs and explicit rules are what make small models work.
- If the 30B can't follow it, the validation will catch the bad output, and the failure log will flag the procedure as problematic.
- Diagnostic step: try the same procedure with the cloud model. If the cloud model can follow it but the 30B can't, the procedure is good but too complex. Simplify it. If both fail, the procedure is bad. Replace it.

**Residual risk:** The 30B might silently produce mediocre output that passes validation but isn't great. The quality promotion system catches this over time -- mediocre procedures have lower success rates than good ones and get replaced.

### Risk 3: Circular Dependency

**The research engine needs procedures to know how to research. But we're using the research engine to find procedures.**

**Resolution:** See Circular Dependency Analysis in Part 1. The research engine is the bootstrap layer. It's already built and deterministic. The procedures we're finding are for higher-level tasks, not for the research engine itself.

### Risk 4: Over-Engineering

**We could build a complex system that's more trouble than it's worth.**

**Mitigation:**
- Start with the simplest version: failure log + time-based re-research.
- Add complexity only when the simple version isn't enough.
- Don't build the A/B testing system until the failure log shows it's needed.
- Don't build the source change detector until the time-based re-research shows it's missing updates.

**Principle:** The [[Fractal-Entropy-Principle]] says expect entropy. Every system tends toward disorder. The simplest system that works is the most maintainable. Complexity is entropy's ally.

### Risk 5: The Vault Becomes a Junkyard of Outdated Procedures

**Over time, the vault could accumulate dozens of stale procedural notes.**

**Mitigation:**
- Quality promotion system: bad procedures get archived, good ones get verified.
- Time-based re-research: old procedures get updated or replaced.
- The vault self-cleans through the same mechanism it self-fills.
- Archived procedures are kept in git history, not deleted. They're available for reference but not used by default.

### Risk 6: Validation Is Too Strict or Too Loose

**Too strict:** Good outputs rejected because they don't match a rigid schema. **Too loose:** Bad outputs slip through.

**Mitigation:**
- Start with the existing validation tools (vault_lint, safe_write) which are already calibrated.
- Adjust thresholds based on failure patterns. If the failure log shows many false positives, loosen. If it shows many false negatives, tighten.
- the operator's corrections are the calibration signal. If the operator says "this should have passed," the validation is too strict. If the operator says "this should have failed," it's too loose.

### Risk 7: The Cold Start Problem

**The first few days, the system has very few procedures and makes lots of mistakes.**

**Mitigation:**
- The existing directives (5 notes) already cover the most important behavioral rules.
- The research engine can find 5-10 core procedures on day 1 in a single research cycle.
- The cloud model is still available as a fallback during the cold start period. The goal is to make it redundant, not to remove it on day 1.
- The cold start is not a cliff -- it's a gradient. The system gets better over time as more procedures are found and tested.

---

## Part 6: Interaction With Existing Systems

### Knowledge Curriculum (`knowledge_curriculum.py`)

The curriculum already finds 5 types of gaps: dangling_link, thin_note, missing_entity, thin_community, link_density. Procedural gaps would be a **6th gap type**: `procedural_gap` -- triggered when the failure log shows a procedure is needed but doesn't exist.

This integrates naturally:
- The failure log detects a pattern of failures around a task type.
- The curriculum proposes "how to [task type]" as a gap.
- The autonomous researcher researches it.
- The resulting note is a procedural note with the schema defined in Part 3.

### Autonomous Researcher (`autonomous_researcher.py`)

The autonomous researcher already runs on a schedule (default 600 seconds), picks the highest-priority gap, researches it, writes a note, and re-indexes. The evolution mechanisms extend this:

- **Failure-driven**: The researcher checks the failure log each cycle. If any procedure has exceeded the failure threshold, it re-researches that procedure's topic.
- **Time-driven**: The researcher checks `last_reviewed` dates on procedural notes. If any are older than their `review_interval_days`, it re-researches them.
- **Source-driven**: Lower priority. Implemented later if needed.

### A-MEM Layer

The A-MEM layer already evolves tags and links on neighboring notes when a new note is created. Procedural notes would benefit from this: when a new procedure is added, the A-MEM layer would link it to related procedures and tag it appropriately.

### FUSED Retrieval

The 30B finds the right procedure for a task using the existing FUSED retrieval (vector + wikilink graph + backlinks). The model searches for "how to [task]" and gets the procedural note. The procedural note has clear if-then rules the 30B follows.

### Validation Tools

Existing validation tools are the backbone of the quality loop:
- `vault_lint`: checks notes for broken wikilinks, missing frontmatter, argument quality
- `safe_write`: verifies code edits won't break the backend (syntax + import check, auto-rollback)
- `vault_guard`: protects sacred files (date-only journals, LOCKED notes)

These already exist and work. The failure log just records their results and triggers re-research when they catch too many failures.

---

## Part 7: Implementation Phases

### Phase 1: Foundation (Minimal, Safe)

**Goal:** Get the failure log working and procedural notes schema defined.

1. Define the procedural note frontmatter schema (Part 3)
2. Build the failure log (`procedure_failure_log.json`) -- a simple JSON file
3. Refactor existing directives into explicit if-then rules (not new content, just clearer structure)
4. Write 2-3 seed procedural notes found through research (how to structure a research note, how to evaluate source credibility)

**What this enables:** The system can track when procedures fail and has a clear format for procedural notes.

**What this does NOT change:** No source code changes to the backend. The failure log is a new file, not a modification to existing modules. The procedural notes are just markdown files in the vault.

### Phase 2: Feedback Loop (The Keystone)

**Goal:** Connect the failure log to the autonomous researcher.

1. Add a check in the autonomous researcher's cycle: read the failure log, check for procedures that have exceeded the failure threshold.
2. When a procedure exceeds the threshold, add its topic to the research queue.
3. After re-research, update the procedural note and reset its failure count.
4. Add `last_reviewed` checking to the autonomous researcher's cycle.

**What this enables:** The system automatically re-researches procedures that are failing or stale.

**What this changes:** A modification to `autonomous_researcher.py` -- adding a new gap source (the failure log) to the cycle. This is additive, not a rewrite. The existing gap detection and research pipeline remain unchanged.

**Risk:** Low. The modification is a new function call in the cycle, not a change to the existing logic. If it fails, the existing cycle still works.

### Phase 3: Quality Promotion

**Goal:** Track experimental vs verified procedures and promote based on track record.

1. Add `status` tracking to procedural notes (experimental, verified, archived, rejected).
2. After N uses of a procedure, compute its success rate.
3. If success rate exceeds threshold, promote to verified.
4. If success rate is below threshold, flag for re-research or archive.

**What this enables:** The system distinguishes between untested procedures and proven ones.

**What this changes:** A new module (`procedure_tracker.py`) that reads the failure log and updates procedural note frontmatter. No changes to existing modules.

**Risk:** Low. The tracker is a new module that reads and writes markdown frontmatter. It doesn't modify existing code.

### Phase 4: Source Change Detection (Optional, Later)

**Goal:** Detect when cited sources have been updated.

1. Store content hashes of source URLs in procedural note frontmatter.
2. Periodically re-fetch sources and compare hashes.
3. If a source has changed, flag the procedure for re-research.

**What this enables:** Faster detection of stale procedures when sources update.

**Risk:** Medium. Requires HTTP requests to external sources, which could fail or be slow. Lower priority than the other mechanisms.

---

## Part 8: What NOT to Change

To keep the system stable during implementation:

1. **Do NOT modify `research_engine.py`** -- it works. The procedures are for the model, not the engine.
2. **Do NOT modify `knowledge_curriculum.py`** -- it works. Procedural gaps are a new gap source, not a change to existing gap detection. They can be fed in through the autonomous researcher.
3. **Do NOT modify the existing validation tools** -- they work. The failure log just records their results.
4. **Do NOT remove the cloud model** -- it's the fallback during cold start. The goal is to make it redundant, not to remove it prematurely.
5. **Do NOT change the autonomous researcher's existing gap detection** -- the failure log is an additional gap source, not a replacement.

All changes are additive: new files, new modules, new frontmatter fields. No existing functionality is removed or rewritten.

---

## Part 9: The Deeper Point

The model doesn't evolve. The vault does. This is the whole thesis of [[Small-Model-Path-to-AGI]] and [[Vault-Longevity-Architecture]]:

- The 30B is a swappable cartridge. It executes whatever the vault tells it.
- The vault learns by finding new procedures, updating old ones, and tracking what works.
- The learning happens at the *knowledge* level (vault), not the *execution* level (model).

A 30B model in 2026 and a 30B model in 2028 follow the same procedures. But the procedures in the vault in 2028 are better -- because they've been re-researched, failure-tested, and quality-promoted. The system gets smarter without the model getting smarter.

This is the [[Fractal-Entropy-Principle]] in action: the system fights entropy (procedures going stale) with energy input (re-research, failure tracking, quality promotion). The fractal pattern: this same feedback loop operates at every scale, from a single procedure being updated to the entire vault evolving over years.

---

## Related

- [[Small-Model-Path-to-AGI]] -- the overall vision
- [[Deterministic-Scaffolding-for-Small-Models]] -- the research backing
- [[Vault-Longevity-Architecture]] -- why the vault is the mind
- [[Autonomy-Directive]] -- operating without permission
- [[vaultbot/Vault-Knowledge-Only-Directive]] -- vault is the only knowledge source
- [[IDK-Fallback-Directive]] -- what to do when you don't know
- [[Fractal-Entropy-Principle]] -- expect entropy, fight it with energy input
- [[Autonomous-Researcher-Quality-Gate]] -- lessons from the researcher producing garbage
- [[the operator-Communication-Preferences]] -- how the operator wants to be communicated with

---

## Part 10: Research-Audit Findings (Last-Minute Check)

*Conducted before implementation. Sources cited inline. Every claim here is
from a web source I read, not from my training weights.*

### Finding 1: 30B Models CAN Follow Procedures — But With a Compounding Failure Rate

**Source**: PromptQuorum MCP benchmarks (May 2026), testing 5 models across 4 MCP servers, 600 graded calls per model.

The benchmark data is clear: 30B-class models with tool-call training (Qwen3-Coder 30B, Qwen3 32B, Gemma 4 27B, GLM-4.7 32B) achieve **93-96% well-formed call rates** on simple tasks. But the critical finding is compounding:

> "A 95% per-call rate over 8 steps lands successfully ~66% of the time."

**What this means for the plan**: An 8-step procedure followed by a 30B will fail ~34% of the time. This is NOT a reason to abandon the plan — it's the reason the failure log exists. The plan's design is correct: the validation layer catches the ~34% of failures, the failure log records them, and the system re-researches and improves. But the plan should explicitly acknowledge:

1. **Procedures should be SHORT** — fewer steps = higher success rate. A 3-step procedure at 95% per step succeeds ~86% of the time. An 8-step procedure succeeds ~66%. Keep procedures to 3-5 steps where possible.
2. **The validation layer is not optional** — it's the only thing standing between the 30B's ~66-86% success rate and the operator's vault. Without it, bad output silently accumulates.
3. **Models below 7B and models without tool-call training fail regardless of size.** The plan should specify a minimum model requirement: 27B+ with tool-call training, Q4_K_M quantization or better.

### Finding 2: Procedural Hallucinations — The Model May "Know But Not Use" a Procedure

**Source**: "Attention Deficits in Language Models: Causal Explanations for Procedural Hallucinations" (arXiv:2602.19239)

This paper defines **procedural hallucination** as "failure to execute a verifiable, prompt-grounded specification even when the correct value is present in-context." The model may ENCODE the correct procedure in its hidden state but fail to ROUTE it to the output.

The paper's key mitigation is **checkpointing**: restating the relevant information near the query can dramatically improve accuracy. In their experiments, checkpointing converted 0% accuracy to 99.8% at long context distances.

**What this means for the plan**: If a procedure is buried early in the system prompt and the user's query is far away, the model may "know but not use" the procedure. The mitigation is structural:

1. **Procedures should appear in the VAULT CONTEXT section** (end of system prompt, closest to the user's message), not in the system prompt's identity/rules section. This is already how the system works — FUSED retrieval pulls relevant notes into the vault context, which is at the end of the prompt.
2. **Procedures should be concise** — a 200-word procedure near the query is more effective than a 2000-word procedure far from it.
3. **The system prompt should mention that procedural notes exist** — a single line: "If the vault context contains notes with `type: procedure`, follow their steps." This primes the model to look for and use procedures.

### Finding 3: Human-Authored Skills Work, LLM-Authored Skills Don't

**Source**: SkillsBench, cited in "Recursive Self-Improvement in AI" (arXiv:2607.07663, survey of 1,250 papers)

> "On SkillsBench, human-authored skills improve pass rates by 16.2 points while LLM-authored skills provide no measurable gain."

**What this means for the plan**: This DIRECTLY VALIDATES the core insight. The plan says "procedures are found, not authored" — the research confirms that human-authored procedures (found on the web) are significantly better than LLM-authored ones. The research engine finds human-authored content. The vault stores it. The 30B follows it. This is the right approach.

### Finding 4: "No External Signal, No Reliable Improvement"

**Source**: "Recursive Self-Improvement in AI" survey (arXiv:2607.07663)

The survey's central design rule, established across hundreds of papers:

> "No external signal, no reliable improvement."

Intrinsic self-correction (the model judging its own output) largely doesn't work. Every self-improvement system that actually works uses an EXTERNAL signal: a test suite, a proof checker, a human rater, an execution result.

**What this means for the plan**: The plan's validation layer (vault_lint, safe_write, the operator's corrections) IS the external signal. This is correct. But the plan should explicitly state: the system works because validation is EXTERNAL to the model, not because the model self-corrects. The failure log should never rely on the model self-reporting failures — it should rely on the validation tools catching them.

### Finding 5: The Self-Improving Claude Code Bootstrap

**Source**: Christopher Allen's "Self-Improving Claude Code" gist (2026)

A single ~1400 token seed prompt bootstraps a self-improving system. Key insights:

1. **"A good seed doesn't front-load structure. It provides the recursive improvement kernel and lets complexity emerge from pressure."** — The plan's approach of starting with 5 directives + a few seed procedures and letting the rest emerge is correct.
2. **"Cold start quality: Session 1 learnings may be thin. The system may need 3-5 sessions before the improvement loop becomes meaningful."** — The plan should set this expectation. The first few days won't be impressive. The system gets better over time.
3. **"Anti-proliferation guardrails: Without it, Claude eagerly creates files 'just in case.' The default should be editing existing files; new files require justification."** — The plan should include an anti-proliferation rule: don't create new procedural notes unless a failure pattern shows one is needed. The failure log is the justification mechanism.

### Finding 6: The Verifier Is the Bottleneck

**Source**: "Recursive Self-Improvement in AI" survey (arXiv:2607.07663)

> "Every category we survey lives or dies by the reliability of its improvement signal — a verifier, a reward model, execution feedback, a proof checker, a meta-evaluator."

The survey identifies a verification hierarchy from formal verifiers (strongest) to intrinsic self-assessment (weakest). VaultBot's validation tools map onto this hierarchy:

| Verification Level | VaultBot Equivalent | Strength |
|---|---|---|
| Formal proof checker | safe_write (syntax + import check) | Strong — code either imports or doesn't |
| Test suite execution | code_run (sandbox execution) | Strong — code either runs or crashes |
| Structured validator | vault_lint (wikilink/frontmatter/quality checks) | Medium — catches structural issues, not semantic ones |
| LLM judge | (not used — by design) | Weak — the survey shows this doesn't work |
| Intrinsic self-assessment | (not used — by design) | Weakest — the survey shows this doesn't work |
| Human rater | the operator's corrections | Strongest — ground truth |

**What this means for the plan**: The plan is using the RIGHT verification level for each task. Code changes get safe_write (strongest). Note quality gets vault_lint (medium). Everything gets the operator's corrections (ground truth). The plan should NOT add an LLM judge — the research shows it doesn't work reliably.

---

## Part 11: Critical Clarifications (Gaps in the Original Plan)

### Clarification A: How Procedures Reach the Model

**The original plan says**: "The 30B finds the right procedure for a task using the existing FUSED retrieval."

**The actual mechanism**: Procedures are just notes with `type: procedure` in their frontmatter. The existing FUSED retrieval (vector + wikilink graph + backlinks) already pulls relevant notes into the VAULT CONTEXT section of the system prompt. The vault context section is at the END of the system prompt — closest to the user's message. This is exactly where the procedural hallucinations research says information should be for best routing.

**What needs to change**: One line in the system prompt (in `build_system_prompt()` in `agent_tools.py`):

```
If the vault context contains notes with type: procedure, follow their steps.
```

This is a ~15-word addition to the system prompt. No structural change to the retrieval pipeline. No new tool. No new module.

### Clarification B: How the Failure Log Gets Populated

**The original plan says**: "Every time the 30B follows a procedure and the output fails validation, that failure is logged."

**The actual mechanism**: This requires two things:

1. **Procedure context tracking**: When FUSED retrieval pulls notes into the vault context for a conversation turn, the system records which procedural notes (if any) were included. This is a simple list of note titles stored alongside the conversation turn.

2. **Validation-driven logging**: When a validation tool (vault_lint, safe_write) catches a failure, the system checks which procedures were in context for the current turn and logs the failure against them. If no procedures were in context, the failure is logged as "no procedure" — which the system interprets as "a procedure is needed for this task type but doesn't exist yet."

**Implementation**: This is a new function in the chat handler (in `main.py`), not a new module. After each turn where a validation tool is called:
- If validation passes: log success for each procedure in context
- If validation fails: log failure for each procedure in context
- If no procedure was in context and validation fails: log as "procedural gap" — triggers the autonomous researcher to find a procedure for this task type

**Risk**: Medium. This requires modifying the chat handler to track which procedures were in context. But it's additive — a new function call after validation, not a change to the validation logic itself.

### Clarification C: The Ollama Dependency

**The plan doesn't mention this**: Even if a user sets `LLM_BACKEND=openai` with an API key, the vault indexer uses OllamaClient for embeddings (nomic-embed-text, ~270MB). This means:

- **Ollama is a hard dependency for embeddings**, even when using a cloud LLM for synthesis
- The user MUST install Ollama and pull nomic-embed-text, regardless of their LLM backend
- This is already documented in the README and .env.example

**What this means for the "average user" experience**: The minimum setup is:
1. Install Python 3.11+
2. Install Ollama
3. Pull nomic-embed-text (embeddings, ~270MB, required)
4. Pull a chat model (e.g., qwen3:latest) OR set LLM_BACKEND=openai + LLM_API_KEY
5. Copy .env.example to .env, set VAULTBOT_OWNER
6. Install dependencies
7. Start the backend
8. Open in Obsidian

This is already handled by the existing README. The plan doesn't change this. But the plan should acknowledge it so the operator knows the full picture.

### Clarification D: Minimum Model Requirements

Based on the PromptQuorum benchmarks:

| Model Class | Tool-Call Reliability | Suitable for VaultBot? |
|---|---|---|
| 70B+ (Llama 3.3 70B) | ~97% per-call | Yes — best, but needs 42GB+ VRAM |
| 27-32B with tool-call training | 93-96% per-call | Yes — recommended for local |
| 7-13B without tool-call training | Fails — paraphrases calls | No |
| Sub-7B | Fails — malformed calls | No |

**Recommendation**: The .env.example should specify a minimum model requirement. The default `qwen3.6:latest` may or may not have tool-call training — the user should be guided to a model that does. Q4_K_M quantization is the production floor.

### Clarification E: Anti-Proliferation Guardrail

From the Self-Improving Claude Code research: without anti-proliferation rules, the system eagerly creates procedural notes "just in case." The plan should include:

1. **Don't create a new procedural note unless the failure log shows a pattern of failures** around a task type. The failure log is the justification.
2. **Prefer updating existing procedures over creating new ones.** If a procedure exists and is failing, re-research and update it, don't create a parallel one.
3. **Cap the number of procedural notes.** Start with a soft cap of 20. If the vault has 20 procedural notes and a new one is needed, archive the one with the lowest success rate.

---

## Part 12: Updated Risk Assessment

### Original Risk 7 (Cold Start) — Updated

The Self-Improving Claude Code research confirms: "Session 1 learnings may be thin. The system may need 3-5 sessions before the improvement loop becomes meaningful."

**Updated mitigation**: Set expectations. The first 3-5 sessions won't be impressive. The system needs time to find procedures, test them, and build up a track record. The cloud model is the fallback during this period. The cold start is a gradient, not a cliff.

### New Risk 8: Procedural Hallucination at Long Context

**Risk**: The model has a procedure in its context but doesn't follow it because the procedure is too far from the query.

**Mitigation**: Procedures appear in the vault context section (end of system prompt, near the query). Keep procedures concise (200-500 words). The system prompt mentions procedures exist. The validation layer catches non-compliance.

**Residual risk**: If the vault context is very large (many notes retrieved), the procedure may still be too far from the query. Mitigation: FUSED retrieval already limits context size. If this becomes a problem, add a "procedure first" ordering to the vault context so procedures appear at the very end (closest to the query).

### New Risk 9: Compounding Failure Rate on Multi-Step Procedures

**Risk**: An 8-step procedure at 95% per-step reliability succeeds only ~66% of the time.

**Mitigation**: Keep procedures to 3-5 steps. Break long procedures into shorter sub-procedures. The failure log catches failures on long procedures faster (more failures per use), triggering re-research sooner.

### New Risk 10: The Verifier Is the Bottleneck

**Risk**: The validation tools (vault_lint, safe_write) are the only thing catching the 30B's mistakes. If they're too weak, bad output accumulates.

**Mitigation**: The validation hierarchy is already correct (see Finding 6). Code changes get the strongest verification (safe_write). Note quality gets medium verification (vault_lint). the operator's corrections are ground truth. The plan should NOT add an LLM judge — the research shows it doesn't work.

**Residual risk**: vault_lint catches structural issues (broken wikilinks, missing frontmatter) but not semantic ones (wrong reasoning, bad arguments). The only defense against semantic errors is the operator's corrections. The system should surface low-confidence outputs for the operator's review.

---

## Part 13: Updated Implementation Phases

### Phase 0: System Prompt Update (Before Phase 1)

**Goal**: Tell the model that procedural notes exist and should be followed.

1. Add one line to `build_system_prompt()` in `agent_tools.py`: "If the vault context contains notes with type: procedure, follow their steps."
2. Add a note in the system prompt about the minimum model requirement (tool-call trained, 27B+, Q4_K_M).

**Risk**: Very low. A one-line addition to the system prompt. No structural change.

### Phase 1: Foundation (No Changes from Original)

The original Phase 1 is correct. No changes needed.

### Phase 2: Feedback Loop (Updated)

**Original**: Connect the failure log to the autonomous researcher.

**Updated**: Four changes:

1. **Add procedure context tracking to the chat handler** (in `main.py`): After FUSED retrieval pulls notes into the vault context, record which notes have `type: procedure` in their frontmatter. Store this list for the current turn.

2. **Add validation-driven logging** (in `main.py`): After each turn where a validation tool is called, log the result (pass/fail) against the procedures that were in context. If no procedures were in context and validation failed, log as a procedural gap.

3. **Add failure log checking to the autonomous researcher** (in `autonomous_researcher.py`): Each cycle, check the failure log for procedures that have exceeded the failure threshold. Re-research those topics.

4. **Add `last_reviewed` checking** to the autonomous researcher: Each cycle, check procedural notes for stale `last_reviewed` dates.

**Risk**: Medium. Changes 1-2 modify the chat handler, which is the most complex part of the system. But they're additive — new function calls after existing logic, not changes to the existing logic itself. Change 3-4 modifies the autonomous researcher, which is simpler.

### Phase 3: Quality Promotion (No Changes from Original)

The original Phase 3 is correct. No changes needed.

### Phase 4: Source Change Detection (No Changes from Original)

The original Phase 4 is correct. No changes needed.

---

## Part 14: Confidence Assessment

After substantial research and code review, here is my honest assessment:

### What I'm Confident About

1. **The core approach is sound.** The research validates "find procedures, don't author them" (SkillsBench), "use external validation, not self-correction" (RSI survey), and "the vault is the mind, the model is plumbing" (the entire architecture).

2. **The existing infrastructure supports this.** The research engine, knowledge curriculum, autonomous researcher, FUSED retrieval, and validation tools all already work. The plan is additive — it adds a failure log and a few function calls, not a rewrite.

3. **The "average user" experience is already handled.** The README, .env.example, and setup instructions already exist. The plan doesn't change the setup process.

4. **The failure log mechanism is correct.** The RSI survey confirms that external validation is the key to self-improvement. The failure log is the external signal that drives evolution.

### What I'm Less Confident About

1. **The procedure context tracking** (Clarification B). This requires modifying the chat handler to track which procedures were in context. I haven't read the full chat handler yet, so I don't know exactly how complex this is. It's the highest-risk part of the implementation.

2. **The 30B's ability to follow procedures reliably.** The research shows 93-96% per-call reliability, but compounding means 66-86% for multi-step procedures. The validation layer catches the failures, but the system will produce failed output ~14-34% of the time on multi-step tasks. This is acceptable for a research assistant (the operator can correct it), but it's not "set and forget."

3. **The cold start period.** The first 3-5 sessions will be thin. The system needs time to find and test procedures. This is expected, but it means the "boom, self-augmenting Jarvis" experience won't be instant — it'll be a gradient over days/weeks.

### What I'm Not Sure About

1. **Whether the research engine can find good procedural content.** The engine is designed for factual research (topics, concepts), not procedural content (how-to guides, best practices). It might find generic articles instead of specific step-by-step procedures. This needs to be tested empirically — run the engine on "how to structure a research note" and see what comes back.

2. **Whether the autonomous researcher will correctly identify procedural gaps.** The failure log tracks failures by task type, but mapping "a validation failure on note X" to "we need a procedure for task type Y" requires some inference. This might need human (the operator) input to calibrate.

### Bottom Line

The plan is sound. The research validates the approach. The existing infrastructure supports it. The risks are known and mitigated. The "average user" experience is already handled.

**My recommendation**: Proceed with implementation, starting with Phase 0 (system prompt update) and Phase 1 (failure log + seed procedures). Test the research engine on procedural topics before committing to Phase 2. If the engine finds good procedural content, proceed. If not, we'll need to adjust the research queries or seed procedures manually.

The system won't be "instant Jarvis" — it'll be a gradient that gets better over days/weeks. But the architecture is correct, the research backs it, and the implementation is additive and safe.


---

## Part 15: Implementation Log

### Phase 0: System Prompt Update — ✅ COMPLETE (2026-07-26)

Added the procedural notes rule to `build_system_prompt()` in `agent_tools.py`:

> "If the vault context contains notes with `type: procedure` in their frontmatter, follow their steps. These are tested procedures found through research, not improvised methods."

- **File changed:** `vaultbot_backend/agent_tools.py` (246 bytes added)
- **Verification:** Backend imports cleanly, rule appears in generated system prompt
- **Risk:** Very low — one-line addition, no structural change

### Phase 1: Foundation — ✅ COMPLETE (2026-07-26)

**1. Procedural note schema** — Defined in Part 3. Implemented in the two seed notes below.

**2. Failure log** — Created `vaultbot_backend/procedure_failure_log.json`:
- Schema: entries array + summary dict + thresholds (3 failures in 30 days)
- Currently empty (no procedures have been used yet)
- Ready for Phase 2 to connect it to the autonomous researcher

**3. Seed procedural notes** — Two notes written and linted:

| Note | Sources | Wikilinks | Lint |
|---|---|---|---|
| [[vaultbot/Structure-Research-Note]] | 5 (Zettelkasten guides) | 8, 0 broken | ✅ Pass |
| [[How-to-Evaluate-Source-Credibility]] | 2 (Ohio State, Stanford study) | 7, 0 broken | ✅ Pass |

Both notes follow the procedural schema: frontmatter with `type: procedure`, `status: experimental`, `falsifiable_if`, `applies_to`, `depends_on`, `sources`. Body contains: when to use, steps, decision points, validation criteria, common failure modes, examples.

**4. Directive refactoring** — Deferred. Existing directives are already deterministic (IDK Fallback has a decision tree, Autonomy Directive has if-then rules). No urgent need to refactor.

**What's ready for Phase 2:**
- Failure log exists and is readable
- Procedural notes exist with proper schema
- System prompt tells the model to follow them
- Next step: connect failure log to autonomous researcher + add procedure context tracking to chat handler


---

## Part 16: Concrete Implementation (Phase 2 — Built 2026-07-26)

*This section replaces the aspirational descriptions in Parts 2-4 with actual
code that was written, tested, and deployed. Every gap identified in the
"Critical Gaps" assessment is addressed here.*

### What Was Built

**1. `procedure_tracker.py` (NEW MODULE — 16KB, 7 tests passing)**

The deterministic feedback loop. No LLM judgment — just counters, date
comparisons, and structured categories.

| Method | What It Does | Mechanism |
|---|---|---|
| `log_result()` | Log pass/fail for a procedure | Structured JSON entry with category |
| `get_failing_procedures()` | Find procedures exceeding failure threshold | Counter >= 3 in 30-day window |
| `get_procedural_gaps()` | Find task types needing a procedure | "no_procedure" failures grouped by task |
| `get_stale_procedures()` | Find procedures overdue for review | Date comparison: now - last_reviewed > interval |
| `check_promotion()` | Promote/flag based on success rate | passes/total >= 0.7 → verified, < 0.4 → flagged |
| `reset_failures()` | Clear failures after re-research | Remove entries for a procedure |
| `get_research_gaps()` | Combined gap report for the researcher | Merges all three gap types, prioritized |

**2. `main.py` (4 ADDITIVE INSERTIONS — 36 lines, no existing code changed)**

- **Import**: `from procedure_tracker import ProcedureTracker, parse_procedures_from_results, interpret_validation_result`
- **Instantiation**: `procedure_tracker = ProcedureTracker(...)` — created BEFORE `autonomous_researcher` so it can be passed as a parameter
- **Procedure context tracking** (in `handle_chat()`): After FUSED retrieval, `parse_procedures_from_results(results)` checks each retrieved note's frontmatter for `type: procedure`. The list is stored for the current turn.
- **Validation logging** (in the agentic loop): After each tool call, if the tool was `vault_lint`, `safe_write`, or `code_run`, `interpret_validation_result()` converts the tool result to pass/fail + structured category, and `procedure_tracker.log_result()` records it against the procedure that was in context (or "no_procedure" if none).

**3. `autonomous_researcher.py` (2 ADDITIVE INSERTIONS — no existing code changed)**

- **Parameter**: `procedure_tracker=None` added to `__init__`, stored as `self.procedure_tracker`
- **Gap checking in `_cycle()`**: Before the normal gap detection, checks `procedure_tracker.get_research_gaps()`. If there are failing/stale procedures or procedural gaps, those are researched first (higher priority). If none, falls through to the normal curriculum-based gap detection.

### How Each Critical Gap Was Resolved

**Gap 1: The `falsifiable_if` matching engine → DROPPED.**
Research on failure classification showed structured, categorized logging is the right approach, not free-text matching. Instead of matching free-text failure descriptions against free-text `falsifiable_if` conditions, we use **structured failure categories** (`broken_wikilinks`, `missing_frontmatter`, `syntax_error`, `import_error`, `argument_quality`, `user_correction`, `validation_error`). The `falsifiable_if` field becomes documentation for humans, not a matching target for code. All failures are counted against the procedure that was in context — no matching needed.

**Gap 2: Procedure context tracking → 3 LINES.**
The `results` list from FUSED retrieval already contains `file_path` for each note. `parse_procedures_from_results()` reads each note's frontmatter and checks for `type: procedure`. The list is stored in `procedures_in_context` for the current turn. This was the highest-risk unknown and turned out to be trivial — the infrastructure was already there.

**Gap 3: Failure log population → HOOKED INTO EXISTING LOOP.**
After each tool call in the agentic loop, if the tool was a validation tool (`vault_lint`, `safe_write`, `code_run`), `interpret_validation_result()` converts the result to pass/fail + category, and `procedure_tracker.log_result()` records it. This is a new function call AFTER the existing tool execution — it doesn't change the tool execution itself.

**Gap 4: Quality promotion module → BUILT.**
`check_promotion()` reads the failure log summary, computes success rate after N uses, and returns "verified" (>= 70%), "flagged" (< 40%), or None (not enough data). This is just counting — no LLM judgment, no A/B test infrastructure.

**Gap 5: "Run alongside" for A/B testing → SEQUENTIAL, NOT PARALLEL.**
New procedures start as `status: experimental`. They get used in production. After 5 uses, `check_promotion()` compares their success rate to the threshold. If >= 70%, promote to verified. If < 40%, flag for re-research. The old verified procedure stays as default until the new one proves itself. No parallel execution needed — just tracking over time.

**Gap 6: Mapping failures to procedural gaps → TASK TYPE, NOT INFERENCE.**
When validation fails and NO procedure was in context, the failure is logged with `procedure="no_procedure"` and `task=<tool_name>`. If the same task type accumulates 3+ failures, `get_procedural_gaps()` returns it as a gap with `topic="how to <task>"`. The autonomous researcher then researches that topic. Simple string matching on task type — no LLM inference.

**Gap 7: Research engine on procedural content → NOT YET TESTED.**
This needs empirical testing: run `vault_research` on "how to write a Python tool" and see if it returns step-by-step guides or generic articles. The system is now in place to test this — the failure log will show whether procedures found by the research engine actually help (pass validation) or not.

**Gap 8: Cloud → 30B transition → CONFIG CHANGE, NOT CODE.**
The user changes their model in `.env` or via the `/llm/config` endpoint. The procedures make the 30B better, but there's no switch to flip. The success rate tracking in the failure log tells you when the 30B is "ready" — when its success rate on procedures is acceptable.

### What's Left (Phase 3+)

- **`procedure_tracker.py` quality promotion loop**: The `check_promotion()` method exists but isn't called automatically yet. It needs a periodic check (in the autonomous researcher's cycle) that reads all procedural notes, checks their promotion status, and updates their frontmatter.
- **Source change detection** (Phase 4): Still optional, lower priority.
- **Empirical testing**: Run the research engine on procedural queries to verify it finds good content.
- **Frontmatter update automation**: After re-research, update `last_reviewed` date and reset failure count. Currently manual — the autonomous researcher re-researches but doesn't update the procedural note's frontmatter automatically.

---

## Part 17: Phase 3 Implementation — Quality Promotion Loop (Built 2026-07-26)

### What Was Built

**1. `procedure_tracker.py` — 3 new methods + 1 helper function**

| Method | What It Does | Deterministic? |
|---|---|---|
| `update_frontmatter()` | Module-level helper: reads note, updates YAML frontmatter fields, preserves body | ✅ Pure file I/O |
| `run_promotion_cycle()` | Scans all procedural notes in vault, checks success rates, promotes (≥70%) or flags (<40%), writes updated frontmatter | ✅ Counters + thresholds |
| `update_after_research()` | After re-research: sets status → experimental, last_reviewed → today, resets stats to 0, clears failure log entries | ✅ Date + file I/O |

**2. `autonomous_researcher.py` — 2 additive insertions**

- **In `_research_to_note()`**: After successfully re-researching a failing/stale procedure, calls `update_after_research()` to reset the procedure's frontmatter and failure log. The procedure gets a fresh slate.
- **In `_cycle()`**: After each research cycle completes, calls `run_promotion_cycle()` to scan all procedural notes and promote/flag them based on accumulated stats. Only logs when something actually changes.

### How the Full Loop Works Now

```
1. Tool call (vault_lint, safe_write, code_run)
   → interpret_validation_result() → pass/fail + category
   → procedure_tracker.log_result()
   
2. Autonomous researcher cycle starts
   → get_research_gaps() checks for:
     a. Failing procedures (3+ failures → re-research)
     b. Procedural gaps (no procedure for a failing task → research "how to [task]")
     c. Stale procedures (last_reviewed > review_interval → re-research)
   → If procedure gaps exist, research those first (higher priority)
   → Otherwise, normal curriculum-based gap detection
   
3. After re-searching a failing/stale procedure:
   → update_after_research() resets frontmatter (status → experimental, last_reviewed → today, stats → 0)
   → reset_failures() clears the failure log for that procedure
   → Fresh slate: the re-researched procedure starts accumulating new pass/fail data
   
4. After each cycle:
   → run_promotion_cycle() scans ALL procedural notes
   → For each: check_promotion() compares success rate to thresholds
   → ≥70% after 5+ uses → promote to "verified" (writes stats to frontmatter)
   → <40% after 5+ uses → flag for re-research (writes stats to frontmatter)
   → Idempotent: won't re-promote an already-verified note
```

### Tests (5/5 passing)

1. **run_promotion_cycle**: Promotes high-success procedures, flags low-success ones, leaves insufficient-data ones unchanged
2. **update_after_research**: Resets frontmatter (status, last_reviewed, stats) and clears failure log
3. **Non-existent procedure**: Returns False gracefully
4. **Idempotency**: Running promotion cycle twice doesn't re-promote already-verified notes
5. **Non-procedural notes ignored**: Notes without `type: procedure` are never touched

### What's Left

- **Empirical testing**: Run the research engine on a procedural query to verify it finds how-to guides (Gap 7 from Part 16)
- **Source change detection** (Phase 4): Optional, lower priority
- **Live cycle test**: The backend needs a restart to pick up the new code. The running instance (PID 13936) still has the old code in memory.
