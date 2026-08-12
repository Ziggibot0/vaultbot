---
type: architecture
status: design-spec
created: 2026-07-27
summary: "Design spec for procedure execution as blocking subprocesses with embedded code blocks (zero LLM cost) and minimal-context LLM calls (only when semantic reasoning is needed). Procedures are vault docs found via FUSED search, with a description field for retrieval efficiency."
tags: [architecture, procedures, deterministic, cost-optimization, subprocess, design-spec]
depends_on:
  - "[[Procedural-Bootstrap-and-Evolution-Plan]]"
  - "[[Deterministic-Scaffolding-for-Small-Models]]"
  - "[[Vault-Longevity-Architecture]]"
sources:
  - "https://arxiv.org/abs/2607.11346"
  - "https://arxiv.org/abs/2605.18747v1"
  - "https://github.com/trpc-group/trpc-agent-go/pull/2305"
---

# Procedure Subprocess Architecture

## The Problem

The current step-gate runtime (`step_gate_runtime.py`, 354 lines) sends **every step** through the LLM. For a 7-step procedure, that's 7 round trips to the model — expensive and slow. The research confirmed nobody has solved this: "Compile, Then Page" (arXiv 2607.11346) compiles procedures but still routes every step through the LLM. The trpc-agent-go deterministic review agent proves deterministic-first execution works in practice, but it's Go code, not markdown procedures.

the operator's redesign: **procedures contain embedded code blocks that call tools directly, executing deterministically. The LLM is only invoked for steps that genuinely need semantic reasoning — and when it is, it's a separate, stripped-down call with minimal context.**

## Core Architecture

### Procedures Are Blocking Subprocesses

When VaultBot invokes a procedure, the procedure runs as a **blocking subprocess**. VaultBot does nothing else until the procedure completes or fails. This is synchronous by design — no interleaving, no race conditions, no partial state.

```
User query -> FUSED search -> VaultBot reads procedure descriptions -> invokes procedure
                                                                          |
                                              +--- PROCEDURE SUBPROCESS (blocking) ---+
                                              |  Step 1: code block -> tool call (no LLM) |
                                              |  Step 2: code block -> tool call (no LLM) |
                                              |  Step 3: [llm:] -> minimal LLM call       |
                                              |  Step 4: code block -> tool call (no LLM) |
                                              |  Step 5: [llm:] -> minimal LLM call       |
                                              +------------------------------------------+
                                                                          |
                                              VaultBot receives result -> continues
```

### Two Step Types

| Step type | Syntax | Execution | Cost |
|---|---|---|---|
| **Code step** | ` ```python ... ``` ` | Runs directly in sandbox, calls tools | **Zero** (no LLM) |
| **LLM step** | `[llm: instruction]` | Separate LLM call with minimal context | **Minimal** (only tool results from this procedure) |

### The Procedure-Bot Is NOT VaultBot

LLM steps within a procedure use a **separate, stripped-down LLM call**:
- **Identity**: "You are a procedure executor. Follow the instruction. Output only the result."
- **Context**: Only the accumulated tool-call results from prior steps in THIS procedure — not the vault, not chat history, not VaultBot's identity
- **Tools**: Only the tools listed in the procedure's `allowed_tools` frontmatter
- **No self-model**: The procedure-bot doesn't know it's VaultBot. It doesn't have access to the vault, directives, or goals. It's a disposable agent that exists for the duration of one step.

This is the key cost optimization: instead of a 10K-token system prompt + vault context + chat history, the procedure-bot gets maybe 500 tokens of context. The LLM call is tiny, fast, and cheap.

### Loud Failures

When a step fails, the procedure **stops immediately** and returns a detailed error:

```json
{
  "procedure": "how-to-verify-claims-in-a-research-note",
  "status": "failed",
  "failed_step": 3,
  "step_type": "code",
  "error": "NameError: name 'vault_research' is not defined",
  "traceback": "...full traceback...",
  "prior_results": ["step 1 output...", "step 2 output..."],
  "context": "Step 3 was executing: result = vault_research(topic=query)"
}
```

This gives VaultBot (and the operator) everything needed to iterate on the procedure:
- Which step failed
- What type of step (code or LLM)
- The exact error and traceback
- What the prior steps produced (so you can reproduce)
- The code/instruction that failed

No silent failures. No swallowing errors. If something breaks, it breaks loud.

## Procedure Format Spec

### Frontmatter

```yaml
---
type: procedure
status: experimental          # experimental | tested | promoted
created: 2026-07-27
description: "Verify claims in a research note by extracting atomic claims, locating cited sources, and checking entailment. Use after vault_research writes a note."
allowed_tools:                # permission-scoped tools for this procedure
  - vault_search
  - web_read_source
  - vault_lint
version: 2.0.0
spec_version: 2               # v2 = embedded code blocks + [llm:] tags
success_count: 0
failure_count: 0
---
```

Key additions from v1:
- **`description`**: One-line summary of what the procedure does and when to use it. VaultBot reads THIS (not the full procedure body) during retrieval to decide whether to invoke. This keeps the LLM context small — it doesn't need to read the whole procedure to know what it's for.
- **`allowed_tools`**: Permission scope. The procedure can only call these tools. Code steps that try to call unlisted tools fail loudly.
- **`spec_version: 2`**: Distinguishes from v1 procedures (which use the old numbered-steps-only format).

### Steps Section

**PREFERRED format (v2.1) — human-readable `### Step N:` headers.**
Every step MUST have a `### Step N: short-summary` header. The summary
becomes the step's `instruction` field, shown in progress callbacks and
logs. Procedures are read by normal people who can't read code — the
header is how they reason about what each step does:

```markdown
## Steps

### Step 1: Search the vault for related notes

```python
results = vault_search(query=claim, k=5)
related_notes = [r["file_path"] for r in results]
```

### Step 2: Read the cited source

```python
source_text = web_read_source(url=cited_url)
```

### Step 3: Determine if the source entails the claim

[llm: Given the source text from step 2 and the claim, determine whether the source entails the claim. Output "SUPPORTED" or "UNSUPPORTED" with a one-sentence explanation.]

### Step 4: Log the verification result

```python
verification_log.append({
    "claim": claim,
    "source": cited_url,
    "verdict": llm_output,
    "step": 3
})
```

### Step 5: Summarize the verification results

[llm: Summarize the verification results. How many claims were supported? How many unsupported? What patterns do you see?]
```

**Legacy format (v2) — bare numbered steps.** Still accepted by the
compiler but produces steps with empty instructions (no human-readable
description). Don't use this for new procedures:

```markdown
## Steps

1. ```python
   results = vault_search(query=claim, k=5)
   ```

2. [llm: Given the results, determine if the claim is supported.]
```

### How Retrieval Works

1. User sends message -> FUSED search runs as normal
2. Procedure notes surface in results (they're vault docs, same as any other)
3. VaultBot reads the `description` field from frontmatter — NOT the full procedure body
4. VaultBot decides: "Does this procedure apply to the current task?"
5. If yes -> invoke the procedure as a blocking subprocess
6. Procedure executes (code steps + minimal LLM steps)
7. Result returns to VaultBot

This is where the **homogeneity of the Obsidian vault** shines: procedures are just .md files. The embedding index naturally surfaces them. The [[Procedural-Bootstrap-and-Evolution-Plan]]'s success/failure tracking applies because they're vault docs. The context budgeter treats them like any other note. No special-casing.

The `description` field is what makes this efficient: VaultBot doesn't burn tokens reading a 2KB procedure body to find out it's about claim verification. It reads a 100-character description and knows.

### Embedding Drift and Learning

Because procedures are vault docs:
- **Embedding drift** naturally adjusts which procedures surface for which queries — as more procedures are written and used, the embedding space refines what's relevant
- **`procedure_tracker.py`** logs success/failure per procedure, promotes procedures with high success rates, flags stale ones
- **The autonomous researcher** can write new procedures when it identifies procedural gaps
- **the operator's corrections** feed into [[Calibration-via-Operator-Feedback]], which can flag procedures that consistently produce bad output

## What Already Exists vs What Changes

> **<!-- updated 2026-08-11 -->** Line counts below are from the original design date (2026-07-27). Current actual line counts: `procedure_compiler.py` = 1241, `step_gate_runtime.py` = 1430, `procedure_tracker.py` = 996, `chat_handler.py` = 4181. See the "Implementation Status" section below for verified post-build counts.

| Component | Current state (as of 2026-07-27 design date) | What changes |
|---|---|---|
| `procedure_compiler.py` (289 lines) | Parses numbered steps with `[validate:]`, `[condition:]`, `[branch:]` annotations | Add: parse code blocks as step type "code", parse `[llm:]` tags as step type "llm", parse `description` and `allowed_tools` from frontmatter |
| `step_gate_runtime.py` (354 lines) | Every step -> LLM call with active frame | **Major rewrite**: code steps execute in sandbox (no LLM), LLM steps use stripped-down minimal context call, blocking execution, loud failures |
| `procedure_tracker.py` (613 lines) | Logs pass/fail per procedure | Add: step-level failure tracking with error details, traceback logging |
| `chat_handler.py` (970 lines → 4181 lines) | Dumps procedure text into context, hopes LLM follows | Procedure invocation logic merged surgically — no separate draft file. <!-- updated 2026-08-05: line count updated from 970 to 4181, chat_handler_new.py reference removed (file never merged) --> |

## Design Decisions

### Why blocking (not async)?

the operator explicitly wants blocking. The procedure is a subprocess — VaultBot can't do anything else until it completes or fails. This is simpler, more predictable, and avoids race conditions. A procedure that's running is the only thing happening.

### Why a separate LLM call (not VaultBot's main call)?

The whole point is cost reduction. VaultBot's main LLM call carries:
- 10K+ token system prompt (identity, directives, self-model, tools list)
- Vault context (FUSED retrieval results, potentially 20K+ tokens)
- Chat history

A procedure-bot LLM call carries:
- ~50 token system prompt ("You are a procedure executor. Follow the instruction.")
- Accumulated tool results from prior steps in this procedure (maybe 1-2K tokens)
- The step instruction (~100 tokens)

That's a 50-100x context reduction per LLM call. For a procedure with 2 LLM steps out of 7 total, you're paying for 2 tiny calls instead of 7 full VaultBot calls.

### Why permission-scoped tools?

Safety and determinism. A procedure that verifies claims should only be able to `vault_search`, `web_read_source`, and `vault_lint` — not `safe_write` or `vault_delete`. If a code step tries to call a tool not in `allowed_tools`, it fails loudly. This prevents a buggy procedure from doing damage.

### Why the description field?

Retrieval efficiency. Without it, VaultBot has to read the full procedure body to know what it does — burning tokens on every procedure that surfaces in FUSED search. With a `description` field, VaultBot reads one line and decides whether to invoke. The full procedure body is only loaded when the procedure is actually executed.

## Implementation Plan

### Phase 1: Update procedure_compiler.py (additive)
- Parse `description` and `allowed_tools` from frontmatter
- Parse code blocks (` ```python ... ``` `) as steps with `type: "code"`
- Parse `[llm: ...]` tags as steps with `type: "llm"`
- Keep backward compatibility with v1 format (numbered steps without code/llm tags)

### Phase 2: Rewrite step_gate_runtime.py
- Code steps: execute in sandbox, capture output, capture errors
- LLM steps: build minimal-context prompt, call LLM, capture output
- Blocking execution loop: step 1 -> step 2 -> ... -> done or fail
- Loud failure: detailed error JSON with traceback, prior results, step info
- Permission checking: verify each code step only calls allowed tools

### Phase 3: Surgical merge into chat_handler.py
- After FUSED retrieval, scan results for procedure notes
- Read `description` fields (not full bodies)
- Present procedure descriptions to VaultBot for invocation decision
- When invoked, call the updated `execute_procedure()` as blocking subprocess
- Receive result, continue conversation

### Phase 4: Update procedure_tracker.py
- Log step-level failures with error details
- Track which step types fail most (code vs LLM)
- Feed failure info to autonomous researcher for procedure improvement

## Related

- [[Procedural-Bootstrap-and-Evolution-Plan]] - the master plan this implements
- [[Deterministic-Scaffolding-for-Small-Models]] - why deterministic-first matters for small models
- [[Vault-Longevity-Architecture]] - why the vault (not the model) is the mind
- [[Chat-i-dont-think-youre-being-very-safe-about-self-ed]] - where the operator proposed embedded code in procedures
- [[Chat-first-do-research-to-see-if-anyone-else-has-alread]] - research confirming this is novel


---

## Corrections from the operator (2026-07-27)

### Correction 1: LLM Calls Must Go Through `get_llm_client()`

**Original design said:** LLM steps in procedures make "separate, stripped-down LLM calls" — but didn't specify the mechanism. Implied direct HTTP to Ollama (localhost:11434).

**Correction:** All LLM calls — including those inside procedure subprocesses — must go through `get_llm_client()` from `llm_client.py`. This factory reads `.env` for `LLM_BACKEND`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`. If the operator switches to OpenRouter, OpenAI, or any other backend, procedure LLM calls automatically hit the right endpoint. **Never hardcode Ollama or localhost:11434.**

**How this works in the subprocess:** The subprocess script imports `get_llm_client` directly:
```python
from llm_client import get_llm_client
client = get_llm_client()
result = client.generate(prompt=step_prompt, system="You are a procedure executor.")
```

Since PYTHONPATH is already set to BACKEND_DIR (same as `code_run`), the subprocess can import backend modules directly. The `.env` file is read by `get_llm_client()` at import time. No special wiring needed.

### Correction 2: Tools Are Imported Directly from Backend Modules

**Original design said:** Code steps "call tools directly" but was vague about how. Implied either raw HTTP calls, file reads, or an internal API endpoint.

**Correction:** The subprocess imports tool functions **directly from the backend Python modules**. No HTTP API, no live service objects, no IPC. The modules ARE the tools.

Examples:
```python
# Subprocess code can do:
from fused_retrieval import FUSEDRetriever
retriever = FUSEDRetriever(vault_path)
results = retriever.search("query", k=5)

from research_engine import ResearchEngine
researcher = ResearchEngine(...)
note = researcher.research("topic")

from claim_verifier import ClaimVerifier
verifier = ClaimVerifier(...)
verdict = verifier.verify_claim("claim", "source")
```

PYTHONPATH already points at `vaultbot_backend/` (set by `code_run`'s subprocess setup). Every backend module is importable. The subprocess instantiates its own objects — it doesn't share state with the main process.

### Correction 3: Subagents Have Scoped Abilities — Not All at Once, but ANY Ability

**Original design said:** `allowed_tools` in frontmatter scopes which tools the procedure can call. Code steps that try to call unlisted tools "fail loudly."

**Correction (clarification):** The `allowed_tools` field determines which imports/functions the subprocess wrapper script injects into the procedure code's namespace. A procedure with `allowed_tools: [vault_search, llm_generate]` gets only those two functions available. A different procedure with `allowed_tools: [vault_research, code_run, llm_generate, safe_write]` gets a different set.

**Key principle:** Subagents should NOT have all of VaultBot's abilities at the same time. But they should be able to have ANY of VaultBot's abilities if the procedure declares them. The `allowed_tools` field is the scoping mechanism.

**How the wrapper script works:**
```python
import sys, json, os

# Runtime passes allowed_tools as env var
allowed = json.loads(os.environ.get("PROCEDURE_ALLOWED_TOOLS", "[]"))
namespace = {"__builtins__": __builtins__}

if "vault_search" in allowed:
    from fused_retrieval import FUSEDRetriever
    _retriever = FUSEDRetriever(os.environ["VAULT_PATH"])
    namespace["vault_search"] = lambda query, k=5: _retriever.search(query, k)

if "llm_generate" in allowed:
    from llm_client import get_llm_client
    _client = get_llm_client()
    namespace["llm_generate"] = lambda prompt, system="": _client.generate(
        prompt=prompt, system=system, stream=False
    )

if "vault_research" in allowed:
    from research_engine import ResearchEngine
    namespace["vault_research"] = lambda topic, **kw: ResearchEngine(...).research(topic, **kw)

# ... etc for each tool

# Read procedure code from stdin, exec with scoped namespace
code = sys.stdin.read()
exec(code, namespace)
```

---

## Professional-Grade Upgrade (2026-07-26)

The compile→execute→track system was ~70% complete. Six gaps closed to
bring it to production quality. All changes are live and tested (72 unit
tests in `tests/test_procedure_*.py`).

### 1. Condition + Branch Execution (was dead code)

The compiler parsed `[condition: ...]` and `[branch: step N]` into the
`Step` dataclass, but the runtime never read them. Now:

- **`_evaluate_condition(condition, prior_results, step_outputs)`** in
  `step_gate_runtime.py` handles three forms:
  - Count comparison: `< 3 notes`, `>= 2 titles`, `!= 0 errors` — counts
    wikilinks (`[[...]]`), URLs (`https?://`), or bullet items in the
    prior step outputs, compares to the integer.
  - Presence: `contains "literal"` — substring check.
  - Boolean: `passed` / `failed` — last step's status.
  - Unparseable → **skip the step** (fail-safe: never run a step whose
    precondition can't be verified). Logged as `step_gate_condition_skip`.

- **Branch jump**: when a step has `branch_target` and passes
  validation, the runtime jumps to that step number instead of the
  linear next. The `executed_steps` set + `max_iterations =
  len(steps) * 3` guard prevents infinite loops.

### 2. Recursive Procedures (LLM thinks less, triggers the right one)

A procedure's code step can now call another procedure:

```python
# In a procedure with allowed_tools: [run_procedure]
result = run_procedure("How-to-Evaluate-Source-Credibility")
```

**Mechanism**: the injected `run_procedure` tool shells out to
`run_procedure.py` (a synchronous CLI entrypoint), which calls
`asyncio.run(execute_procedure(...))`. Same JSON-in/JSON-out contract
as every other code step.

**Guards**:
- `MAX_PROC_DEPTH = 3` — caps recursion depth. Exceeding → loud
  `{"depth_exceeded": true}`.
- Cycle detection: the call stack (list of procedure names in flight)
  is passed to each child. If the requested procedure is already in the
  stack → loud `{"cycle_detected": true}`.
- Least-privilege: `run_procedure` must be in the procedure's
  `allowed_tools` frontmatter. If not listed, the function isn't
  injected and the step fails with `NameError`.

**Tracking**: `ExecutionResult.child_procedures` records each child's
name, pass/fail, and step count. Surfaced in the chat_handler return
dict so the LLM sees "Step 3 ran sub-procedure X (5 steps, passed)."

### 3. Grading Loop Closure (the "scooch" path)

The grading pieces existed but weren't wired to procedure execution.

- **Procedure-level drift**: after `execute_procedure` returns,
  `chat_handler.py` records `embedding_drift.record_feedback(
  proc_file_path, query_embedding, helpful=overall_passed)`. A passed
  procedure drifts toward the query (ranks higher for similar queries
  next time); a failed one drifts away. Reuses the existing
  `EmbeddingDrift` — no new drift code.

- **Verified-procedure retrieval boost**: `fused_retrieval._rerank`
  adds a `VERIFIED_BOOST = 0.05` score bump to candidates whose
  frontmatter `status` is `verified` (set by
  `procedure_tracker.run_promotion_cycle`). Deliberately below
  `DRIFT_SCORE_WEIGHT = 0.25` so verified status only breaks ties
  near the margin, never overrides content similarity.

- **Step-failure → re-research**: `procedure_tracker.get_research_gaps`
  already surfaces `failing_step` gaps (step number + failure count) so
  the autonomous researcher re-researches that specific step's topic,
  not the whole procedure.

### 4. Structured Validation (replaces word-overlap heuristic)

The old validator checked if ≥50% of content words from the validation
string appeared in the output. Now three opt-in predicate forms, with
free-text as the fallback:

| Form | Syntax | Check |
|---|---|---|
| `at_least N <unit>` | `at_least 2 notes` | Count wikilinks/URLs/items in output, compare ≥ N |
| `contains "literal"` | `contains "supported"` | Substring check |
| `matches /regex/` | `matches /\d+/` | Regex search |
| Free text (fallback) | `mention 2 note titles` | Word-overlap ≥ 50% |

Backward-compatible: existing `[validate: mention 2 note titles]` keeps
working via the fallback. New procedures can opt into precise forms.

### 5. Procedure Index (O(1) lookup)

`chat_handler.py` used to `rglob("*.md")` on every `execute_procedure`
call. Now `procedure_tracker.get_procedure_index()` builds a
stem → {path, frontmatter} map, cached on the tracker instance. Falls
back to `rglob` only on a miss (covers a note written seconds ago).

### 6. Test Coverage (was zero)

Four test files, 72 cases, all green:
- `tests/test_procedure_compiler.py` (19) — frontmatter, steps, annotations
- `tests/test_procedure_validation.py` (17) — three forms + fallback
- `tests/test_procedure_grading.py` (9) — tracker + drift integration
- `tests/test_step_gate_runtime.py` (27) — loop, conditions, branches,
  code steps, recursion guards (cycle + depth)

### Files Changed

| File | Change |
|---|---|
| `step_gate_runtime.py` | `_evaluate_condition`, condition skip, branch jump, `run_procedure` tool injection, `child_procedures` field, `code_read` fix, structured validation dispatch |
| `procedure_compiler.py` | No change (annotations were already parsed) |
| `procedure_tracker.py` | `get_procedure_index()` stem→{path, frontmatter} map |
| `run_procedure.py` | NEW — synchronous CLI for recursive procedure calls |
| `chat_handler.py` | Stem-index lookup, procedure-level drift feedback, `child_procedures` in return |
| `fused_retrieval.py` | `VERIFIED_BOOST`, `procedure_status_index` attribute |
| `main.py` | Wire `procedure_status_index` from tracker at startup |
| `agent_tools.py` | `execute_procedure` tool description updated |

# Print result (convention: procedure code sets a `result` variable)
if "result" in namespace:
    print(json.dumps(namespace["result"]))
```

### What This Changes in the Design

| Original design | Corrected design |
|---|---|
| LLM calls to localhost:11434 | LLM calls via `get_llm_client()` (respects .env config) |
| Vague about how tools are called | Tools imported directly from backend modules |
| `allowed_tools` checked statically | `allowed_tools` determines what's injected into the subprocess namespace |
| Subprocess is a black box | Subprocess is a scoped Python environment with injected capabilities |

### What Doesn't Change

- Procedures are still markdown notes with `type: procedure` frontmatter
- Code steps still run in a subprocess (blocking, synchronous)
- LLM steps still use minimal context (not VaultBot's full system prompt + vault context)
- Loud failures still return detailed error JSON with traceback
- `procedure_tracker.py` still logs pass/fail
- The `description` field still drives retrieval efficiency
- The procedure-bot is still NOT VaultBot (no identity, no self-model, no directives)

### Updated Implementation Plan

**Phase 1: Update `procedure_compiler.py` (additive)**
- Parse `description` and `allowed_tools` from frontmatter (unchanged from original plan)
- Parse code blocks and `[llm:]` tags (unchanged from original plan)
- No changes needed from corrections — compiler just parses, doesn't execute

**Phase 2: Rewrite `step_gate_runtime.py`**
- Build the subprocess wrapper script dynamically based on `allowed_tools`
- Inject only the allowed tool functions into the subprocess namespace
- LLM calls inside the subprocess use `get_llm_client()` (imported from `llm_client.py`)
- Code steps exec in the subprocess with the scoped namespace
- LLM steps (`[llm:]` tags) compile to `llm_generate(prompt)` calls in the code
- Loud failures: non-zero exit → detailed error JSON with traceback, prior results, step info

**Phase 3: Surgical merge into `chat_handler.py`** (unchanged from original plan)

**Phase 4: Update `procedure_tracker.py`** (unchanged from original plan)

### Open Questions (Still Unresolved)

1. **Tool function signatures:** The wrapper script needs to know each tool's function signature to create proper lambda wrappers. Do we maintain a registry mapping tool names to (module, class, method, signature)? Or do we standardize all tools to a common interface?

2. **State between steps:** How do results from step 1 flow to step 2? Options: (a) each step's stdout is captured and passed as `input` to the next step's namespace, (b) all steps share a single subprocess and a persistent namespace, (c) results written to a temp file. Option (b) is simplest but means the subprocess stays alive for the whole procedure — which changes the "blocking subprocess" model slightly.

3. **Error handling for LLM steps:** If `llm_generate()` fails (API timeout, rate limit, bad response), what happens? Loud failure with the error? Retry with backoff? The procedure should probably fail loud — the runtime can retry the whole procedure later.

4. **Static analysis vs runtime enforcement:** Can a code step bypass the `allowed_tools` scoping by directly importing a module (e.g., `import os; os.system(...)`)? The subprocess has full Python access. True sandboxing would require seccomp or RestrictedPython. For MVP, we accept that the subprocess is trusted (the operator wrote the procedure, not an attacker).

### Related
- [[Chat-remember-this-shouldnt-be-bespoke-to-ollama]] — the operator's corrections on LLM client and tool imports
- [[llm_client]] — the abstraction layer that respects user's .env config


---

## Implementation Status (2026-07-27)

### What's Built and Tested

| Component | File | Status |
|---|---|---|
| **Procedure Compiler v2** | `procedure_compiler.py` (1241 lines) | DONE — parses v1 text steps + v2 code blocks + `[llm:]` tags, `description` and `allowed_tools` from frontmatter, `textwrap.dedent()` on code blocks |
| **Step-Gate Runtime v2** | `step_gate_runtime.py` (1430 lines) | DONE — code steps run in subprocess with tool injection, LLM steps use `get_llm_client()`, text steps use active-frame (v1 compat), loud failures with traceback, step-level + procedure-level tracking |
| **Tool Registry** | `step_gate_runtime.py` `_build_tool_preamble()` | DONE — supports `llm_generate`, `vault_search`, `web_read_source`, `vault_lint`, `vault_append`, `vault_list`, `code_read` |
| **Integration** | `chat_handler.py` + `agent_tools.py` | DONE — `execute_procedure` tool registered as 8th meta-tool, execution logic in `execute_agent_tool` |
| **Procedure Tracker** | `procedure_tracker.py` (996 lines) | UNCHANGED — already had step-level logging, now actually called by the v2 runtime |

### What's NOT Done Yet

1. **No v2 procedure notes exist** — all existing procedures are v1 (text-only). Need to write at least one v2 procedure to test end-to-end through the chat interface.
2. **`vault_search` in subprocess uses simple text search** — not FUSED retrieval (which needs FAISS index + Ollama embeddings). Good enough for MVP; upgrade later.
3. **No `allowed_tools` enforcement beyond namespace injection** — a code step could theoretically import modules directly (e.g., `import os`). MVP accepts this since the operator writes the procedures.
4. **No retry on step failure** — the procedure stops at the first failure. Could add retry logic later.
5. **No streaming for LLM steps** — LLM steps use `generate()` (non-streaming). The result is captured and returned as a whole.

### Test Results

All tests passed:
- v2 code steps: 3/3 passed (vault_search + vault_list + prior_results chaining)
- v1 backward compat: 6/6 text steps passed (mock LLM)
- Loud failures: error + traceback captured, procedure stops at failed step
- Step-level tracking: 3 step entries + 1 procedure entry logged correctly
- All modules import cleanly (procedure_compiler, step_gate_runtime, agent_tools, chat_handler)
