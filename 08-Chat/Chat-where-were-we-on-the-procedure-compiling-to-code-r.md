# Chat: where were we on the procedure compiling to code r

## 2026-07-27 00:18 UTC

**User:** where were we on the procedure compiling to code research? we were gonna find a way to make the framework follow a procedure that's written down on a markdown file

**Assistant:** Here's exactly where we are:

## Status: Built but not wired in

| Component | File | Lines | Status |
|---|---|---|---|
| **Procedure Compiler** | `procedure_compiler.py` | 289 | ✅ Built — parses markdown procedure notes into structured `Procedure`/`Step` objects with `[validate:]`, `[condition:]`, `[branch:]` annotations |
| **Step-Gate Runtime** | `step_gate_runtime.py` | 354 | ✅ Built — executes one step at a time with active-frame paging (current step first to reset evidence decay), deterministic word-overlap validation, structured logging |
| **Integration** | `chat_handler_new.py` | 1127 | ⚠️ Draft — full wiring written (compile → execute → log → send progress), but **NOT LIVE** |

The live system still uses `chat_handler.py` (original) which has **zero references** to step_gate or procedure_compiler. `chat_handler_new.py` is a complete rewrite with the step-gate code injected, but it was never swapped in.

## What the integration does (from the draft)

After FUSED retrieval, if procedure notes are found in context:
1. Compiles them via `compile_from_text()` (from retrieval results) or `compile_procedure()` (from disk)
2. Runs the first compiled procedure through `execute_step_gate()` — one step at a time, active frame first
3. Validates each step deterministically (word-overlap heuristic)
4. Logs per-step results to `procedure_tracker`
5. Sends step progress to the user via WebSocket
6. Falls through to normal generation on any error

## What's left

The next step is **flipping the switch** — either swap `chat_handler_new.py` in as the live handler, or merge just the step-gate block (~140 lines) into the existing `chat_handler.py`. Then test that it doesn't break normal chat (the fallthrough path).

Want me to wire it in and test?

<details>
<summary>Thinking process</summary>

Sean is asking where we left off on the procedure compiling research. The vault context already has the key chat logs. Let me look at what's in the context:

1. [[Chat-we-got-farther-than-that-you-were-doing-research]] — Sean reminded me about the "gold lead" on procedure compiling
2. [[Chat-if-the-research-checks-out-then-yes-plan-out-exact]] — I researched, verified the claims, and laid out an implementation plan for a Procedure Compiler + Step-Gate Runtime

The plan from that chat was:
- New module: `procedure_compiler.py` (~200 lines) — parse markdown procedure notes into structured Procedure/Step objects
- Step-gate runtime — feed steps one at a time, gate on validation/conditions
- Integration into chat_handler.py

Let me check what actually got built vs what was just planned. Let me search the vault for any implementation notes or check if procedure_compiler.py exists.So `procedure_compiler.py` exists (289 lines). Let me check if `step_gate_runtime.py` also exists, and whether either is integrated into `chat_handler.py`.Both `procedure_compiler.py` (289 lines) and `step_gate_runtime.py` (354 lines) exist. Now I need to check if they're actually integrated into `chat_handler.py` or `main.py`. The import at the top of `chat_handler.py` shows `procedure_tracker` but not `procedure_compiler` or `step_gate_runtime`. Let me search for any references to these modules in the backend.So there's a `chat_handler_new.py` that has the integration code. Let me check if `chat_handler_new.py` is actually being used or if it's a draft that was never wired in. Let me check `main.py` to see which chat handler it imports.Hmm, main.py doesn't import chat_handler directly? Let me look at how the chat flow works. Maybe it's in a different file or the main.py is structured differently.So `research_handler.py` imports `handle_chat` from `chat_handler` (the original), not `chat_handler_new`. Let me check if `chat_handler_new.py` is used anywhere, or if it's just sitting there as a draft.So here's the full picture:

1. **`procedure_compiler.py`** (289 lines) — BUILT. Parses markdown procedure notes into structured `Procedure`/`Step` objects with validation, condition, and branch annotations.

2. **`step_gate_runtime.py`** (354 lines) — BUILT. Executes compiled procedures one step at a time with active-frame paging (current step first), deterministic word-overlap validation, and structured logging.

3. **`chat_handler_new.py`** — DRAFT. Contains the integration code that wires the compiler + runtime into the chat flow. But it's NOT being used — the live system still uses `chat_handler.py` (the original), which has NO references to step_gate or procedure_compiler.

So the status is: **both modules are built and tested, but the integration is a draft that was never wired in.** The last session ended after I laid out the implementation plan and Sean said "if the research checks out then yes plan out exactly what changes you intend." I wrote the plan, then apparently built the modules and the draft integration, but never flipped the switch to make it live.

Let me also check chat_handler_new.py to see what the integration looks like — how complete is it?Now I have the full picture. Let me summarize for Sean.

Here's where we are:

1. **Research** — DONE. Found the "gold lead" paper "Compile, Then Page" (arXiv 2607.11346) + procedural hallucination research (arXiv 2602.19239). Key findings: compile markdown SOPs into structured objects, execute one step at a time with active-frame paging (current step first to reset evidence decay), deterministic validation per step.

2. **`procedure_compiler.py`** (289 lines) — BUILT. Parses markdown procedure notes into structured `Procedure`/`Step` objects. Handles both existing format (numbered steps with bold verbs) and new format (with `[validate:]`, `[condition:]`, `[branch:]` annotations).

3. **`step_gate_runtime.py`** (354 lines) — BUILT. Executes compiled procedures one step at a time. Active-frame paging puts current step first. Deterministic word-overlap validation. Structured logging. Progress callbacks for WebSocket updates.

4. **`chat_handler_new.py`** (1127 lines) — DRAFT INTEGRATION. Contains the full wiring: after FUSED retrieval, finds procedure notes, compiles them, runs step-gate runtime, logs results to procedure_tracker, sends progress to user. Falls through to normal generation on any error. BUT: this file is NOT live. The system still uses `chat_handler.py` (original) which has zero references to step_gate or procedure_compiler.

So the status is: **modules built and tested, integration written as a draft, but never flipped on.** The next step is to wire `chat_handler_new.py` into the live system (or merge its step-gate code into `chat_handler.py`), test it, and verify it doesn't break anything.

</details>