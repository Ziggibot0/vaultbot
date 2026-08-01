---
type: architecture-note
status: draft
created: 2026-08-01
tags: [architecture, small-models, cascade, framework-driven, procedures, cloud-obsolescence]
---

# The Cognitive Load Cascade

> The framework takes weight off the small model's shoulders. The small
> model takes weight off the big model's shoulders. Through iteration and
> procedure tuning, both LLMs do less and less over time.

## The Core Principle

VaultBot is not "a big model with helpers." It's a **cascade** where each
tier only does what the tier below it cannot. Work flows downward over time:
big model → small model → framework. The big model's job is to make itself
redundant. The small model's job is to make itself redundant. The framework's
job is to grow until it handles everything it can.

```
User sends message
    ↓
TIER 1 — Framework (deterministic, zero LLM)
    Intent routing (keyword rules + retrieval score thresholds)
    Context retrieval (FUSED: vector + graph + backlinks)
    Research engine (LLM-light: scrape, extract, corroborate)
    Procedure execution (code-only steps run deterministically)
    Output validation (schema, source citation, non-empty, <done>)
    ↓ (what it can't do deterministically)
TIER 2 — Small model (local, ≤4B, runs on any laptop)
    Template extraction (fill blanks the framework prepared)
    Classification (pick from a bounded list)
    Short formatting (structured data → prose)
    Tag/keyword extraction from text
    ↓ (what it can't do reliably)
TIER 3 — Big model (free OpenRouter cloud, OR local if hardware)
    Multi-source synthesis
    Complex planning / goal decomposition
    Writing new procedures
    Generation the small model can't handle
```

## Default Configuration

| Tier | Default | Optional |
|---|---|---|
| Framework | Same code on all hardware | — |
| Small model | `qwen3:4b` or `granite4.1:3b` (2.1-2.5GB) | Any Ollama model the user's RAM can hold |
| Big model | Free OpenRouter model (zero cost) | Local model (`qwen3:14b`, `qwen3.6:latest`) if the user has the hardware |

The user pays nothing. They use less energy than ChatGPT because the free
cloud model only fires on the residual — the work that the framework and
small model can't handle. The small local model and framework handle the
rest. That's the environmental win: fewer cloud calls, smaller models, same
outcome.

Someone with a good GPU can set the big model to local and go fully offline.
Someone on grandpa's laptop uses the free cloud model as the big model and
still gets a working system because the small model + framework do most of
the work.

## How Work Moves Down the Cascade

This is what makes the cascade more than "use small models." The system gets
cheaper over time through three mechanisms:

### Mechanism 1: Big → Small (Procedures)

A task that needed the big model (write a research note) gets a **procedure**
with a template. Now the small model just fills in the template from
structured facts. The big model wrote the procedure once; the small model
uses it forever.

```
Day 1:  Big model synthesizes research into a note (reasoning task)
Day 7:  A procedure is written: "Format-Research-Note" with a template
Day 8+: Small model fills the template from structured facts (extraction task)
        Big model no longer fires for this task
```

### Mechanism 2: Small → Framework (Deterministic Rules)

A task that needed the small model (classify intent) gets a **deterministic
rule** that handles 90% of cases. The small model only fires on the 10% the
rules can't catch. Over time, the rules get better and the small model fires
less.

```
Day 1:  Small model classifies every message (research/code/chat/status)
Day 7:  Keyword rules handle 80% of cases deterministically
        Small model only fires on ambiguous messages
Day 30: Rules refined to handle 90% of cases
        Small model fires on 10% — and those are the edge cases that
        reveal what new rules to write
```

### Mechanism 3: Procedure Tuning (Failure-Driven)

Every time a procedure produces output that fails validation, the failure is
logged. When failures on a procedure exceed a threshold, the procedure is
re-researched and updated. Bad procedures get replaced. Good procedures get
simpler (fewer LLM steps, more deterministic steps).

```
procedure_used → small_model_fills_template → validation → pass/fail
                                                           |
                                                           v fail
                                                   failure_log[procedure] += 1
                                                           |
                                                           v threshold exceeded
                                                   re_research(procedure.topic)
                                                   update_note(procedure, new_findings)
```

This is mechanical. No LLM decides "this procedure is stale" — a counter
trips a threshold. See [[Procedural-Bootstrap-and-Evolution-Plan]].

## Why No Fallbacks Make the Cascade Work

The no-fallback rule is what makes the cascade **honest**. If there were a
fallback ("if small model fails, use big model"), failures would be hidden:
the system would silently use the big model when the small model fails, and
you'd never know the small model wasn't pulling its weight. The big model
would never get less work because the fallback would keep covering for the
small model's failures.

Without fallbacks:
- If the small model fails, it fails **loud**. You see it.
- That failure tells you: this task needs a better template, or a better
  procedure, or it genuinely belongs on the big model.
- You fix the template/procedure. Next time, the small model handles it.
  The big model's load shrinks.

Fallbacks mask the signal. No-fallback makes every failure a data point for
improvement. The no-fallback rule isn't just about code cleanliness — it's
the mechanism that makes the cascade improve over time.

## What the Model Actually Does at Each Tier

### Framework (Tier 1) — The Architect

The framework does all the reasoning about WHAT to do. It never asks the
model to decide strategy. It:

- Detects intent (deterministic: keyword rules + retrieval scores)
- Picks the procedure (deterministic: retrieval + scoring)
- Retrieves context (FUSED: vector + graph + backlinks)
- Runs the research engine if the vault is thin (LLM-light, already built)
- Follows procedure steps, executing code-only steps itself
- Validates every model output (schema, source, non-empty, `<done>`)

### Small Model (Tier 2) — The Extractor

The small model never thinks. It extracts. The framework hands it a
template with blanks and a focused context. The model fills the blanks.

Example — answering a question from vault context:

```
Framework builds:
  "Context: {retrieved notes, truncated to relevant sections}
   Question: {user message}
   
   Fill in:
   - Answer (2-4 sentences, cite notes as [[Note-Title]]): ____
   - Confidence [high/medium/low]: ____
   <done>"

Small model fills the blanks.
Framework validates: has <done>? cites real notes? non-empty? not too short?
If invalid → fail hard, surface error.
```

Example — classifying intent:

```
Framework builds:
  "Message: {user message}
   Categories: [question, research, code, status, chat]
   Reply with one word."

Small model picks one word.
Framework validates: is it one of the 5 options?
If invalid → fail hard.
```

The small model's jobs are always:
1. Bounded output (fill blanks, pick from list, 1-3 tokens)
2. Extract from provided context (not generate from weights)
3. Validated deterministically by the framework

### Big Model (Tier 3) — The Residual

The big model only fires for what the small model genuinely can't do:
- Multi-source synthesis (combine 5+ sources into a coherent note)
- Complex planning (decompose a multi-step goal)
- Writing new procedures (understand task structure, create the template)
- Novel generation the small model can't handle

And even here, the framework reduces the load. The big model gets a focused
prompt, not the full agentic loop. It gets the research engine's structured
facts, not raw web pages. It gets a template to fill, not a blank page.

## How This Maps to the Existing Codebase

| Existing component | Cascade role | What changes |
|---|---|---|
| `chat_handler.py` agentic loop | Currently Tier 3 (big model drives) | Becomes framework-driven: framework routes, model extracts |
| `agent_tools.py` system prompt | Currently full briefing to big model | Shrinks: model sees focused step, not 20 tools |
| `fused_retrieval.py` | Tier 1 (already framework) | No change — already deterministic |
| `research_engine.py` | Tier 1 (already LLM-light) | No change — already framework-driven |
| `procedure_compiler.py` + `step_gate_runtime.py` | Tier 1 (framework executes steps) | Framework calls procedures FOR the model, not the model calling them |
| Model cartridge system | Already routes small/big/vision | Default: small=local 3-4B, big=free OpenRouter |
| `<done>` turn protocol | Already small-model-friendly | Stays — it's a single-token signal, not a judgment call |
| `safe_write` / `vault_lint` | Tier 1 validation (already built) | Extends to validate small-model output |

## The Aider Connection

Aider's Architect/Editor split showed that splitting cognitive load improves
results even when using the same model for both roles. gpt-4o solo: 71.4%.
gpt-4o as Architect + Editor: 75.2%. The split itself helps.

VaultBot's cascade is the same principle, extended to three tiers:
- Framework = Architect (decides what to do)
- Small model = Editor (fills in the structured output)
- Big model = Senior Architect (handles what the Editor can't)

The Aider lesson: the model doesn't need to be big if the framework handles
the reasoning. The model just needs to follow a format. A 3-4B model can
follow a format if the template is tight and the validation catches mistakes.

See [[Deterministic-Scaffolding-for-Small-Models]] for the research backing.

## What Makes This Different From "Just Use Small Models"

The cascade is not "try to use a small model and fall back to big." It's:

1. **Framework-first**: the framework does everything it can before any
   model fires. Most interactions should need zero LLM calls.

2. **Template-driven**: when a model does fire, it fills a template, it
   doesn't reason. The framework built the template. The model extracts.

3. **Failure-driven improvement**: every loud failure is a signal that
   drives a procedure update, a new rule, or a better template. The system
   gets cheaper because failures are visible, not hidden.

4. **Over-time redundancy**: the big model's calls get rarer as procedures
   are tuned. The small model's calls get simpler as rules are written.
   The framework handles more as procedures are refined. This is the
   [[Small-Model-Path-to-AGI]] vision, made concrete.

## Related

- [[Deterministic-Scaffolding-for-Small-Models]] — the sandwich pattern research
- [[Procedural-Bootstrap-and-Evolution-Plan]] — how procedures evolve over time
- [[How I want RAG and procedures to work]] — the user's design intent
- [[Small-Model-Path-to-AGI]] — the vision this implements
- [[Vault-Longevity-Architecture]] — why model independence matters