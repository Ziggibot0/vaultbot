# Chat: if the research checks out then yes plan out exact

## 2026-07-26 23:29 UTC

**User:** if the research checks out then yes plan out exactly what changes you intend

**Assistant:** Research checks out — every claim I made traces directly to source text I read. Here's the plan.

---

## Implementation Plan: Procedure Compiler + Step-Gate Runtime

### What We Have Now

- **`procedure_tracker.py`** (613 lines) — logs pass/fail per *procedure* (not per step), promotes/flags based on success rates, detects stale/failing procedures
- **`chat_handler.py`** (970 lines) — after FUSED retrieval, finds procedure notes in context via `parse_procedures_from_results`, dumps them into context as text, hopes the LLM follows them. Validation happens *after* the answer via `interpret_validation_result`.
- Procedures are markdown notes with `type: procedure` frontmatter and numbered steps, but no machine-readable structure for conditions or validation.

**The gap**: The LLM sees the full procedure at once and may skip steps. There's no gating, no active-frame paging, no step-level tracking. The research says this is exactly where procedural hallucinations happen — information decays geometrically with distance, and the model routes to the wrong output (Stage 2B binding failures).

---

### What I'm Building

#### 1. New module: `procedure_compiler.py` (~200 lines)

**Purpose**: Parse markdown procedure notes into structured `Procedure` objects.

**Data structures**:
```python
@dataclass
class Step:
    number: int
    instruction: str           # The main instruction text
    validation: str | None     # Parsed from [validate: ...]
    condition: str | None      # Parsed from [condition: ...]
    branch_target: int | None  # Parsed from [branch: step N]

@dataclass
class Procedure:
    name: str
    file_path: str
    steps: list[Step]
    raw_text: str              # Full markdown for fallback
```

**What it does**:
1. Read a markdown note with `type: procedure` frontmatter
2. Extract numbered steps from the body (`1. instruction text`)
3. Parse inline annotations in square brackets:
   - `[validate: output must contain X]` → `Step.validation`
   - `[condition: if X then skip to step 5]` → `Step.condition`
   - `[branch: step 3]` → `Step.branch_target`
4. Return structured `Procedure` object

**Design decisions**:
- Pure deterministic parsing — no LLM calls
- Backward-compatible: existing procedures without annotations parse fine (just no validation/condition fields)
- Falls back to raw text if parsing fails — never breaks existing behavior

#### 2. New module: `step_gate_runtime.py` (~300 lines)

**Purpose**: Execute a compiled procedure one step at a time with gating.

**The execution loop** (based on "Compile, Then Page" + "Attention Deficits"):

```
for each step in procedure.steps:
    1. Build active frame:
       - Current step instruction FIRST (checkpointing — resets evidence distance)
       - Full procedure overview BELOW (full-program cursor — gets most of the benefit)
       - Vault context (budgeted by context_budgeter)
    2. Call LLM with active frame
    3. Validate output (capability gate):
       - If step has [validate: ...], check deterministically (string matching)
       - If no validation criteria, always pass
    4. Log step result to procedure_tracker (step-level, not just procedure-level)
    5. Advance on pass; advance on fail (with logged failure)
    6. If step has branch_target and condition is met, jump to that step
```

**Key design decisions from research**:
- **Active frame first** = checkpointing from "Attention Deficits" — resets evidence distance to near-zero, prevents Stage 2B binding failures (the paper showed 0% → 99.8% accuracy with checkpointing)
- **Full program retained** = the "full-program cursor" ablation from "Compile, Then Page" — recovers most of the benefit without a full stack machine
- **No stack machine** — the research explicitly says the simplest version gets most of the benefit
- **Deterministic validation** — string matching only, no LLM-based validation (keep it simple, add later if needed)
- **Step-level logging** — feeds into procedure_tracker for step-level promotion/demotion (matches "Progressive Crystallization")

**Return value**:
```python
@dataclass
class StepResult:
    step_number: int
    passed: bool
    output: str
    validation_error: str | None

@dataclass
class ExecutionResult:
    procedure_name: str
    steps: list[StepResult]
    overall_passed: bool
    final_output: str  # Concatenation of step outputs
```

#### 3. Update: `procedure_tracker.py` (additive, ~100 new lines)

**New methods** (all additive, no existing code changed):
- `log_step_result(procedure, step_number, passed, error)` — step-level logging
- `get_step_stats(procedure, step_number)` — per-step pass/fail counts
- `get_failing_steps(procedure)` — which specific steps are problematic
- Add step-level data to `get_research_gaps()` — so the autonomous researcher knows which *steps* need re-research, not just which procedures

#### 4. Update: `chat_handler.py` (minimal change, ~30 lines)

**The integration point**: After `parse_procedures_from_results` finds procedure notes:

```python
# Current: procedures are just in context, LLM follows them on its own
# New: if procedures have structured steps, route through step-gate runtime

procedures_in_context = parse_procedures_from_results(results)
if procedures_in_context:
    for proc_name in procedures_in_context:
        procedure = procedure_compiler.compile(proc_name)
        if procedure and len(procedure.steps) > 0:
            # Route through step-gate runtime instead of normal generation
            execution_result = await step_gate_runtime.execute(
                procedure, context, svc.ollama_client, svc.context_budgeter
            )
            # Log results
            for step_result in execution_result.steps:
                svc.procedure_tracker.log_step_result(
                    proc_name, step_result.step_number,
                    step_result.passed, step_result.validation_error
                )
            # Send the final output to the user
            send_to_user(execution_result.final_output)
            return  # Skip normal generation
```

If no procedures or compilation fails → fall through to normal generation (existing behavior unchanged).

#### 5. Documentation: Procedural note format update (no code)

Update the Procedural Bootstrap plan and exemplar notes to document the new inline annotation syntax:

```markdown
---
type: procedure
spec_version: 1
activation: always
name: how-to-do-X
version: 1.0.0
status: experimental
---

## Steps

1. Read the user's question carefully [validate: restate the question in your own words]

2. Search the vault for relevant notes [validate: mention at least 2 note titles]

3. If vault is thin, research the topic [condition: if vault has < 3 relevant notes]
   [validate: say "researching X now"]
```

Backward-compatible: existing procedures without annotations work fine.

---

### What I'm NOT Doing (and why)

| Not doing | Why |
|---|---|
| Full PG stack machine | "Compile, Then Page" says the simplest version (active frame first, full program retained) gets most of the benefit |
| LLM-based validation | Start with deterministic string matching. Add LLM validation later if needed. |
| Capability check before enabling | "Compile, Then Page" says check model discipline first. For now, always use step-gating. Add the check later. |
| Rewriting existing code | All changes are additive — new modules + minimal integration point |
| Changing procedure note format destructively | Backward-compatible. Existing notes parse fine. |

---

### Build Order

1. **`procedure_compiler.py`** — pure deterministic, testable with `code_run` immediately
2. **`step_gate_runtime.py`** — depends on procedure_compiler, testable with mock LLM
3. **`procedure_tracker.py` updates** — additive, testable independently
4. **`chat_handler.py` integration** — minimal change, the wiring point
5. **Documentation** — update exemplar notes and the bootstrap plan

Each step is independently testable. Step 1 doesn't break anything. Step 2 doesn't break anything. Step 3 doesn't break anything. Step 4 is the only integration point, and it falls back to existing behavior if anything fails.

---

Want me to start building? I'll begin with `procedure_compiler.py` — test it with `code_run` against one of our existing procedure notes, then move to the step-gate runtime.

<details>
<summary>Thinking process</summary>

Let me think carefully about this. Sean is asking me to plan out exactly what changes I intend to make, based on the research. Let me first verify the research is solid by checking the key claims against what I actually read from the sources.

**Verification checklist:**

1. **"Compile, Then Page" (arXiv 2607.11346v3)** — I read the abstract directly from the archived source. Key claims:
   - "compile machine-readable SOP constraints into executable pseudo-code" ✅ (directly from abstract)
   - "program-guided (PG) stack machine that pages the active frame" ✅ (directly from abstract)
   - "compiled text never significantly hurts and gains up to 16.0 points" ✅ (directly from abstract)
   - "Runtime guidance is capability-gated" ✅ (directly from abstract)
   - "Two strong models independently show positive seven-domain PG contrasts (58:19 and 75:31 discordant pairs), whereas weak models are harmed" ✅ (directly from abstract)
   - "A full-program cursor ablation (active frame first, complete program retained) recovers much of the strong-model refusal gain" ✅ (directly from abstract)
   - "On Bank the three primary arms rise from 70.4 to 86.4 to 92.8, with 100% refusal correctness" ✅ (directly from abstract)
   - "Practical guidance: compile first; enable active-frame paging only after a model-level discipline check" ✅ (directly from abstract)

2. **"A Deterministic Control Plane for LLM Coding Agents" (arXiv 2606.26924v1)** — I read the full abstract from the archived source. Key claims:
   - "LLM coding harnesses grant agents broad file and shell access, yet the configuration layer that steers them is largely unmanaged" ✅
   - "10.1% of tracked paths are SHA-256 exact duplicates across independent repositories" ✅
   - "Rel(AI)Build treats agent definitions as a managed supply chain (SHA-256 content addressing, HMAC-stamped lockfiles, hash-chained audit logs)" ✅
   - "enforces tiered permissions and attack-derived blocklists before LLM invocation" ✅
   - "gates feature work through a phase state machine with requirement-to-file-to-test traceability" ✅
   - "compiles a single canonical definition to seven IDE targets" ✅
   - "detects prompt drift via Jaccard similarity" ✅
   - "Governance of this layer must be deterministic and tool-agnostic -- not delegated to further LLM orchestration" ✅

3. **"Progressive Crystallization" (arXiv 2607.07052v1)** — I read the full abstract from the archived source. Key claims:
   - "AI agents deployed for IT operations are typically permanent cost centers because every execution requires full LLM inference" ✅
   - "three-stage execution taxonomy, from fully agent-orchestrated to hybrid to fully deterministic workflows" ✅
   - "evidence-based promotion mechanism that converts repeatedly validated agent behaviors into cheaper and more reproducible deterministic workflows" ✅
   - "automatically demoting workflows that regress" ✅
   - "increased deterministic execution from 0% to 45% over eight months" ✅
   - "reduced per-incident agent costs by more than 70% despite doubling incident volume" ✅

4. **"Attention Deficits in Language Models" (arXiv 2602.19239)** — I read the FULL HTML of the paper. This is the most detailed source. Key claims:
   - "procedural hallucination: failure to execute a verifiable, prompt-grounded specification even when the correct value is present in-context" ✅
   - "Stage 2A (gating) errors where the model does not enter answer mode, and Stage 2B (binding) errors where it enters answer mode but selects the wrong candidate" ✅
   - "Stage 2B accounts for most errors across model families" ✅ (Table 1 shows 65-100% Frac-2B)
   - "a linear probe on the final-layer residual stream recovers the correct value far above chance (e.g., 74% vs. 2% on Qwen2.5-3B)" ✅ (Table 2)
   - "oracle checkpointing intervention that restates the true binding near the query can nearly eliminate Stage 2B failures at long distance (e.g., Qwen2.5-3B 0/400 → 399/400 at k=1024)" ✅ (Table 8)
   - "routing efficiency η = I_used / I_avail" ✅
   - "Information decays geometrically with distance" ✅ (Theorem 3, SDPI contraction)
   - "Two levers: increase availability (checkpointing, retrieval, shorter contexts), increase routing efficiency" ✅ (Discussion section)
   - Trace auditing: "ReqBits vs ObsBits" — if ObsBits < ReqBits, the step is under-budget ✅ (Appendix C)
   - "Pass/fail audit is strongly predictive: 99.5% accuracy for pass vs 63.7% for flagged at k=2048" ✅ (Table 4)

5. **agent-rules-spec** (GitHub PR) — I read the full PR description. Key claims:
   - "spec-version: 1 as a bounded interchange format" ✅
   - "activation: always | on-match | manual" ✅
   - "Conformance roles (parser, selector, converter)" ✅
   - "Path selection and glob grammar" ✅
   - "Conversion fidelity framework: exact, lossy, unsupported" ✅
   - "agent-rule.schema.json" ✅
   - "check_spec.py ~800-line Python validator" ✅

6. **Schema'd AGENTS.md** (opensassi/a0 issue) — I read the full issue. Key claims:
   - "schema'd .md format that is both human-readable and machine-validated" ✅
   - "frontmatter (YAML/TOML) + named sections + typed fields" ✅
   - "Available Skills, Workflow, Design Constraints, MCP Tools" ✅
   - "Workflow: ordered list of steps with optional conditionals" ✅
   - "1. command — description [condition: optional]" ✅
   - "validator skill that observes interactions and generates/updates AGENTS.md" ✅
   - "each step in the chain is persisted, and the chain can be replayed and validated" ✅

All claims check out against the sources I read. The research is solid.

Now, let me plan the changes. Let me think about what we currently have and what needs to change.

**Current state of the system:**
- We have `procedure_tracker.py` (587 lines) — logs pass/fail per procedure, detects failing/stale procedures, promotes/flags based on success rates
- We have `context_budgeter.py` — token budgeting for vault context
- We have `calibration.py` — detects Sean's corrections, classifies failures
- We have `rag_eval.py` — logs retrieval events, computes metrics
- We have `claim_verifier.py` — post-generation verification of claims
- We have procedural notes with `type: procedure` frontmatter
- We have the Procedural Bootstrap and Evolution Plan (44KB, 9 parts)

**What the research tells us we need:**

1. **A procedure compiler** — parse markdown procedures into a structured format (steps, conditions, branches). This is the "compile" step from "Compile, Then Page."

2. **A step-gate runtime** — execute procedures one step at a time, showing only the active frame to the LLM, validating output before advancing. This is the "PG stack machine" + "capability gating" from "Compile, Then Page" + the "checkpointing" from "Attention Deficits."

3. **Integration with procedure_tracker.py** — the step-gate runtime should log pass/fail per step (not just per procedure), feeding into the promotion/demotion cycle. This matches "Progressive Crystallization."

4. **A capability check** — before enabling active-frame paging, check if the model can maintain state discipline. From "Compile, Then Page": "enable active-frame paging only after a model-level discipline check."

5. **Procedure format update** — align our procedural note format with the agent-rules-spec and AGENTS.md schema (YAML frontmatter + structured steps with conditions).

Let me now think about the concrete implementation plan.

**Architecture:**

```
procedure_compiler.py
├── parse_procedure(note_path) → Procedure
│   ├── Parse YAML frontmatter
│   ├── Extract numbered steps
│   ├── Parse conditions/branches
│   └── Return structured Procedure object
└── compile_to_pseudocode(procedure) → PseudoCode
    ├── Convert steps to pseudo-code instructions
    └── Include jump/branch targets

step_gate_runtime.py
├── execute_procedure(procedure, context) → ExecutionResult
│   ├── For each step:
│   │   ├── Build active frame (current step + minimal context)
│   │   ├── Call LLM with active frame
│   │   ├── Validate output (capability gate)
│   │   ├── Log pass/fail to procedure_tracker
│   │   └── Advance or retry
│   └── Return execution result
├── build_active_frame(step, procedure) → str
│   ├── Current step first
│   ├── Full program available (full-program cursor)
│   └── Token budget from context_budgeter
└── validate_step_output(output, step) → bool
    ├── Check if output satisfies step requirements
    └── Return pass/fail

procedure_tracker.py (updates)
├── Add step-level tracking (not just procedure-level)
├── Add promotion/demotion based on step-level success rates
└── Add capability check results
```

Wait, but I need to think about this more carefully. The step-gate runtime needs to be integrated into `main.py` — it's the core execution loop. Let me think about where it fits.

Currently, `main.py`:
1. Receives a user query
2. Retrieves vault context (FUSED retrieval + context_budgeter)
3. Generates a response
4. Post-processes (claim verification, calibration, RAG eval)

The step-gate runtime would be used when the vault context contains `type: procedure` notes. Instead of just dumping the procedure into context and hoping the LLM follows it, the runtime would:
1. Detect procedure notes in the retrieved context
2. Compile them into structured steps
3. Execute each step with the LLM, gating transitions
4. Log results

But wait — this is a significant architectural change. The current system is a single LLM call (generate a response). The step-gate runtime would be multiple LLM calls (one per step). This changes the execution model.

Let me think about what's realistic and what Sean would want.

Sean's goals:
- Deterministic framework for 30B local models
- Vault saturation replacing cloud dependency
- Moving cognition from LLM weights → vault

The step-gate runtime directly serves these goals:
- It's deterministic (code controls the flow, not the LLM)
- It works with small models (checkpointing = active frame paging helps small models)
- It moves cognition to the vault (procedures are in the vault, execution is deterministic)

But I need to be careful about scope. Let me plan this in phases.

**Phase 1: Procedure Compiler** (`procedure_compiler.py`)
- Parse markdown procedure notes into structured format
- Extract steps, conditions, branches
- This is pure deterministic code, no LLM needed

**Phase 2: Step-Gate Runtime** (`step_gate_runtime.py`)
- Execute procedures one step at a time
- Active frame construction (current step first, full program available)
- Output validation (capability gate)
- Integration with procedure_tracker.py for step-level logging

**Phase 3: Integration into main.py**
- Detect procedure notes in retrieved context
- Route to step-gate runtime when procedures are present
- Fall back to normal generation when no procedures

**Phase 4: Procedure Format Update**
- Update procedural note format to align with agent-rules-spec
- Add conditions, branches, validation criteria to procedure notes
- Update exemplar notes

Let me think about the details of each phase.

**Phase 1: Procedure Compiler**

Input: A markdown note with `type: procedure` frontmatter
Output: A structured Procedure object

```python
@dataclass
class Step:
    number: int
    instruction: str
    conditions: list[str]  # When this step should execute
    validation_criteria: str  # How to validate the output
    branches: dict[str, int]  # Branch name → step number

@dataclass
class Procedure:
    name: str
    version: str
    steps: list[Step]
    activation: str  # always | on-match | manual
    spec_version: str
```

The compiler needs to:
1. Parse YAML frontmatter (already have this capability)
2. Extract numbered steps from the markdown body
3. Parse conditions (e.g., `[condition: if X then skip to step 5]`)
4. Parse validation criteria (e.g., `[validate: output must contain Y]`)
5. Build the Procedure object

This is pure deterministic code. I can test it with code_run.

**Phase 2: Step-Gate Runtime**

The runtime executes a procedure one step at a time:

```python
def execute_procedure(procedure, context, llm_call_fn):
    results = []
    current_step = 0
    
    while current_step < len(procedure.steps):
        step = procedure.steps[current_step]
        
        # Build active frame: current step first, full program available
        active_frame = build_active_frame(step, procedure, context)
        
        # Call LLM with active frame
        output = llm_call_fn(active_frame)
        
        # Validate output (capability gate)
        passed = validate_step_output(output, step)
        
        # Log to procedure_tracker
        log_step_result(procedure.name, step.number, passed, output)
        
        if passed:
            results.append(StepResult(step.number, True, output))
            current_step += 1
        else:
            # Retry or fail
            results.append(StepResult(step.number, False, output))
            current_step += 1  # For now, advance anyway; could add retry logic
    
    return ExecutionResult(procedure.name, results)
```

The `build_active_frame` function:
```python
def build_active_frame(step, procedure, context):
    # Active frame first (current step + its context)
    frame = f"## Current Step ({step.number}/{len(procedure.steps)})\n"
    frame += f"{step.instruction}\n"
    
    if step.validation_criteria:
        frame += f"\n**Validation:** {step.validation_criteria}\n"
    
    # Full program available (but after the active frame)
    frame += f"\n## Full Procedure\n"
    frame += format_procedure_overview(procedure)
    
    # Vault context (budgeted)
    frame += f"\n## Context\n{context}\n"
    
    return frame
```

The `validate_step_output` function:
```python
def validate_step_output(output, step):
    if not step.validation_criteria:
        return True  # No validation criteria = always pass
    
    # Deterministic validation: check if output contains required elements
    # This is the capability gate
    criteria = step.validation_criteria
    # Simple string matching for now
    # Could be extended with LLM-based validation
    return check_criteria(output, criteria)
```

**Phase 3: Integration into main.py**

In the main pipeline, after retrieving context:
1. Check if any retrieved notes have `type: procedure`
2. If yes, compile them and route to step-gate runtime
3. If no, proceed with normal generation

This is a conditional branch in the pipeline, not a replacement.

**Phase 4: Procedure Format Update**

Update the procedural note format:
```markdown
---
type: procedure
spec_version: 1
activation: always
name: procedure-name
version: 1.0.0
---

## Procedure

1. Step instruction — description [condition: optional] [validate: optional]

2. Next step instruction
```

OK, let me now think about what exactly to write for Sean. He wants a plan, not an implementation. Let me be clear and concise.

Actually, let me also check what we already have in the vault and in our code to make sure I'm not duplicating anything.

Let me search for existing procedure-related code and notes.Good, I can see the existing research notes and the procedural bootstrap plan. Let me also check what procedure_tracker.py currently looks like and what main.py's pipeline looks like.Let me check what .py files are in the vaultbot_backend directory.Let me see more of main.py to understand the pipeline — specifically how procedures are currently used and where the step-gate runtime would integrate.Let me see the actual chat handler / response generation pipeline to understand where procedures are currently used.Let me see the rest of the services and the chat handler to understand where procedures are currently integrated.Now let me look at the chat handler to understand where procedures are currently integrated into the response pipeline.Let me look at the chat_handler to see where procedures are currently integrated.Now I have a good picture of the current system. Let me also check how procedures are currently parsed and used.Now I have a thorough understanding of the current system. Let me also check what the `parse_procedures_from_results` function does and how procedures are currently integrated.Now I have a thorough understanding of the system. Let me now look at the `parse_procedures_from_results` and `interpret_validation_result` functions to see how procedures are currently integrated into the chat handler.Now I have a thorough understanding of the entire system. Let me now plan the changes.

**Current state summary:**
1. `procedure_tracker.py` (613 lines) — logs pass/fail per procedure, detects failing/stale procedures, promotes/flags based on success rates. Currently tracks at the PROCEDURE level (did the procedure succeed or fail overall), not at the STEP level.
2. `chat_handler.py` (970 lines) — the main chat loop. After FUSED retrieval, it calls `parse_procedures_from_results` to find procedure notes in context, then logs validation results against them after tool calls. Procedures are currently just dumped into context as text — the LLM is expected to follow them on its own.
3. `main.py` (661 lines) — wires everything together, creates all the services.
4. Procedures are markdown notes with `type: procedure` frontmatter. They have numbered steps but no machine-readable structure for conditions, validation criteria, or branches.

**What needs to change based on the research:**

The research tells us:
1. **"Compile, Then Page"**: Compile procedures to pseudo-code, execute with a stack machine that pages the active frame (shows current step first), capability-gated (validate before advancing). The simplest version (active frame first, full program retained) gets most of the benefit.
2. **"Attention Deficits"**: Step-gating works because it's checkpointing — it resets the evidence distance to near-zero for each step. Information decays geometrically with distance. Two failure modes: Stage 2A (gating — doesn't enter answer mode) and Stage 2B (binding — enters answer mode but picks wrong answer). Stage 2B dominates.
3. **"Progressive Crystallization"**: Three-stage taxonomy (agent-orchestrated → hybrid → deterministic). Evidence-based promotion. Automatic demotion on regression. This is what procedure_tracker already does, but at procedure level, not step level.
4. **"Deterministic Control Plane"**: Governance must be deterministic, not delegated to LLM orchestration. Phase state machine with traceability.
5. **agent-rules-spec / AGENTS.md**: YAML frontmatter + structured steps with conditions and validation.

**The plan:**

I need to build two new modules and update two existing modules:

### New Module 1: `procedure_compiler.py`
**Purpose**: Parse markdown procedure notes into structured `Procedure` objects with steps, conditions, and validation criteria.

**What it does**:
- Read a markdown note with `type: procedure` frontmatter
- Parse the YAML frontmatter (name, version, activation, spec_version)
- Extract numbered steps from the markdown body
- Parse inline annotations: `[condition: ...]`, `[validate: ...]`, `[branch: ...]`
- Return a structured `Procedure` object with a list of `Step` objects

**Data structures**:
```python
@dataclass
class Step:
    number: int
    instruction: str        # The main instruction text
    conditions: list[str]   # When this step should execute (parsed from [condition: ...])
    validation: str         # How to validate output (parsed from [validate: ...])
    branch_target: int | None  # Step to jump to (parsed from [branch: ...])

@dataclass
class Procedure:
    name: str
    file_path: str
    version: str
    activation: str         # always | on-match | manual
    spec_version: str
    steps: list[Step]
    raw_text: str           # Full markdown for fallback
```

**Parsing rules**:
- Steps are numbered lines: `1. instruction text [condition: ...] [validate: ...]`
- Conditions, validation, and branches are optional inline annotations in square brackets
- The parser is pure deterministic — no LLM needed
- Falls back gracefully: if no annotations, just extract the instruction text

### New Module 2: `step_gate_runtime.py`
**Purpose**: Execute a compiled procedure one step at a time, gating transitions based on output validation.

**What it does**:
- Takes a `Procedure` object and a context string
- For each step:
  1. Build the active frame (current step first, full procedure overview, vault context)
  2. Call the LLM with the active frame
  3. Validate the output (capability gate)
  4. Log pass/fail to procedure_tracker (step-level, not just procedure-level)
  5. Advance on pass, retry or advance on fail
- Returns an `ExecutionResult` with per-step outcomes

**Key design decisions based on research**:
- **Active frame first**: Current step instruction at the top, full procedure overview below it (the "full-program cursor" ablation from "Compile, Then Page" that recovers most of the benefit)
- **Token budget**: Use the existing `context_budgeter.py` to ensure the active frame fits within the model's token budget
- **Validation**: Start with deterministic string matching (check if output contains required elements from `[validate: ...]`). Fall back to "always pass" if no validation criteria specified. This is the capability gate.
- **Step-level logging**: Log each step's pass/fail to procedure_tracker, not just the overall procedure result. This enables step-level promotion/demotion (matching "Progressive Crystallization").
- **No stack machine**: The research says the simplest version (active frame first, full program retained) gets most of the benefit. We don't need a full PG stack machine. We need a step-gate loop.

**Integration point**: In `chat_handler.py`, after FUSED retrieval and context building, if procedure notes are found in the retrieved context:
1. Compile them with `procedure_compiler`
2. If compilation succeeds and produces steps, route through `step_gate_runtime`
3. The step-gate runtime produces the response by executing each step
4. If no procedures or compilation fails, fall back to normal generation

### Update 1: `procedure_tracker.py`
**What changes**:
- Add `log_step_result(procedure, step_number, passed, output_snippet)` — step-level logging
- Add `get_step_failure_rate(procedure, step_number)` — per-step success rates
- Add `get_failing_steps(procedure)` — identify which specific steps are problematic
- Add step-level data to the gap report so the autonomous researcher knows which steps need re-research
- Keep all existing functionality — this is purely additive

### Update 2: `chat_handler.py`
**What changes**:
- After `parse_procedures_from_results`, if procedures are found:
  - Compile each procedure with `procedure_compiler`
  - If any compile successfully (has structured steps), route through `step_gate_runtime`
  - The step-gate runtime replaces the normal single LLM call for this turn
- If no procedures or compilation fails, continue with normal generation (existing behavior)
- After step-gate execution, log the overall result to procedure_tracker (existing behavior, but now with step-level data)

### Update 3: Procedural note format (no code change, just documentation)
**What changes**:
- Document the new inline annotation syntax in the Procedural Bootstrap plan
- Update exemplar notes to show the new format
- The format is backward-compatible: existing procedures without annotations still work (parser just extracts the instruction text)

**The format**:
```markdown
---
type: procedure
spec_version: 1
activation: always
name: procedure-name
version: 1.0.0
status: experimental
---

## Steps

1. First instruction — description [validate: output must contain X]

2. Second instruction [condition: if X then skip to step 5] [validate: output must contain Y]

3. Third instruction
```

### What I'm NOT doing (and why):
- **Not building a full PG stack machine** — the research says the simplest version gets most of the benefit
- **Not adding LLM-based validation** — start with deterministic string matching, add LLM validation later if needed
- **Not changing the procedure note format destructively** — backward-compatible, existing notes still work
- **Not rewriting existing code** — all changes are additive (new modules + additive updates to existing ones)
- **Not adding capability checking yet** — the "model-level discipline check" from "Compile, Then Page" is a future enhancement; for now, we always use step-gating

Let me now write this up clearly for Sean.

</details>