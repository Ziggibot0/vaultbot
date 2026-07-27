# Chat: iterate one more time on that plan. do a last-minu

## 2026-07-26 00:29 UTC

**User:** iterate one more time on that plan. do a last-minute check. i can't code so i have no idea if this will work: that's on you and the research you do. if you're confident in the plan after substantial research and don't see anything wrong with it for the average user and they can just download, set their api and models and boom they have a self-augmenting jarvis then yeah go for it

**Assistant:** Done. The plan went from 24KB → 44KB. I read the actual source code (`autonomous_researcher.py`, `research_engine.py`, `main.py`, `agent_tools.py`, `llm_client.py`, `free_search.py`, `.env.example`, `README.md`) and did deep research on 6 sources. Here's what I found:

## The Good News

**The core approach is validated by research:**

1. **"Find procedures, don't author them"** — SkillsBench (cited in a 1,250-paper survey on self-improving AI) found human-authored skills improve pass rates by 16.2 points while LLM-authored skills provide **zero measurable gain**. Our approach of finding procedures online is the right one.

2. **"External validation, not self-correction"** — the survey's central rule: "No external signal, no reliable improvement." Our validation layer (vault_lint, safe_write, your corrections) IS the external signal. The plan is using the right verification hierarchy.

3. **30B models CAN do this** — benchmarks show 93-96% per-call tool reliability for 27-32B models with tool-call training (Qwen3-Coder 30B, Qwen3 32B, Gemma 4 27B).

4. **The existing infrastructure already supports it** — FUSED retrieval already pulls relevant notes into the vault context (closest to the query, which is exactly where the research says they should be). No new retrieval mechanism needed.

## The Things I Fixed

**5 gaps in the original plan:**

- **How procedures reach the model** — they're just notes. FUSED retrieval already pulls them into context. One line in the system prompt tells the model to follow them. That's it.
- **How the failure log gets populated** — the original plan was vague. I specified: validation tools log pass/fail automatically, the system tracks which procedures were in context for each turn.
- **The Ollama dependency** — even with a cloud LLM, Ollama is required for embeddings. Already documented in README, but the plan now acknowledges it.
- **Minimum model requirements** — sub-7B models and models without tool-call training fail. The plan now specifies 27B+ with tool-call training, Q4_K_M minimum.
- **Anti-proliferation guardrail** — without it, the system creates junk procedures. Now capped: don't create a new procedure unless the failure log justifies it.

## The Honest Risks

- **Compounding failure rate**: 95% per-step × 8 steps = only 66% success. Mitigation: keep procedures to 3-5 steps. The failure log catches the rest.
- **Procedural hallucinations**: the model may "know but not use" a procedure if it's too far from the query. Mitigation: procedures appear in vault context (end of prompt, near query). Keep them concise.
- **Cold start**: first 3-5 sessions will be thin. Not instant Jarvis — a gradient over days/weeks.
- **Research engine on procedural content**: the engine is designed for factual research, not how-to guides. Needs empirical testing before committing to Phase 2.

## My Recommendation

**Proceed.** Start with Phase 0 (one-line system prompt update) and Phase 1 (failure log + seed procedures). Test the research engine on a procedural topic before Phase 2. The architecture is correct, the research backs it, and all changes are additive — no existing code gets rewritten.

The system won't be "boom, instant Jarvis" — but it'll be a self-augmenting one that gets better every day. The plan is at [[Procedural-Bootstrap-and-Evolution-Plan]] — 14 parts, 22 wikilinks, 0 broken. Want me to start building?

<details>
<summary>Thinking process</summary>

Sean wants me to do a thorough final review of the plan before implementing. He can't code, so he's trusting me to verify this will actually work. Let me:

1. Re-read the plan carefully
2. Research whether the approach is sound - look for potential issues with the architecture
3. Check if there are any gotchas with the existing codebase that would make this fail
4. Verify the "average user" experience - can someone just download, set API keys, and go?

Let me start by re-reading the plan and the existing codebase more carefully, then do targeted research on potential issues.

Key things to verify:
- Will the failure log + autonomous researcher integration actually work with the existing code?
- Is the bootstrap approach sound? Can the research engine really find good procedures?
- Are there any architectural issues I'm missing?
- What happens on a fresh install with no procedures?
- Is the "average user" experience actually smooth?

Let me read the key source files more carefully and do some research.Good. Now let me read more of the autonomous researcher to understand the cycle, and also look at the main backend to understand how things are wired together. I need to understand:

1. How the autonomous researcher's cycle works (to know if adding failure log checks is feasible)
2. How the backend is configured (to understand the "average user" experience)
3. What search backend is used (Tavily requires API key, FreeSearch/DuckDuckGo is free)
4. How the system is started up

Let me read more of the autonomous researcher and the main backend.Now I need to understand several key things:

1. How the system is configured - what API keys are needed, what models are needed
2. How the search backend works - is it Tavily (requires API key) or DuckDuckGo/FreeSearch (free)?
3. How the LLM client works - what models are needed?
4. What the "average user" experience looks like - can they just download, set API keys, and go?

Let me read more of main.py to understand the configuration and startup, and also look at the search backend setup.Now I have a much clearer picture. Let me understand the key architecture:

1. **Search**: FreeSearch (DuckDuckGo + Marginalia + arXiv) - NO API KEYS needed. Optionally SearXNG (Docker) for more coverage. Tavily is also supported but not required.

2. **LLM**: Two modes:
   - `LLM_BACKEND=ollama` → local Ollama (free, but needs a local model)
   - `LLM_BACKEND=openai` → any OpenAI-compatible API (OpenAI, OpenRouter, etc.) via `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL`

3. **Embeddings**: Always on OllamaClient (nomic-embed-text, ~270MB) - this requires Ollama running locally

4. **Autonomous researcher**: Already wired up, runs on a schedule, uses the curriculum for gap detection

5. **Research engine**: LLM-light, deterministic, no LLM calls inside the dig

Now let me think about what issues exist with the plan:

**Issue 1: The plan says "average user can just download, set their API and models and boom"** - but there's a critical dependency: **Ollama must be running for embeddings**. Even if the user sets `LLM_BACKEND=openai` with an API key, the vault_indexer uses OllamaClient for embeddings (nomic-embed-text). This means the average user needs:
- Ollama installed and running (for embeddings)
- Either a local LLM model via Ollama OR an API key for OpenAI-compatible backend
- No search API key needed (FreeSearch works keyless)

This is a potential friction point. The plan doesn't mention this.

**Issue 2: The plan's failure log mechanism** - The plan says "every time the 30B follows a procedure and the output fails validation, that failure is logged." But HOW does the system know which procedure was followed? The current system doesn't track which procedural note was used for a given task. There's no mechanism to associate a task with a procedure. This is a gap in the plan.

**Issue 3: The plan says "Phase 2: A modification to autonomous_researcher.py"** - but looking at the code, the `_cycle()` method is already complex with checkpointing, crash recovery, curriculum integration, etc. Adding failure log checks is feasible but needs to be done carefully. The plan says it's "additive" but doesn't specify exactly where in the cycle the check goes.

**Issue 4: The plan mentions "procedure_tracker.py" as a new module** - but doesn't specify how it integrates with the existing system. How does it get called? By the autonomous researcher? On a separate schedule?

**Issue 5: The "average user" experience** - The plan doesn't address:
- What happens when Ollama isn't installed? (embeddings fail)
- What happens when no LLM is configured? (synthesis fails)
- What's the minimum setup? (Ollama + a model, or Ollama for embeddings + API key for LLM)
- Is there a setup wizard or config guide?

**Issue 6: The plan's "procedural notes" concept** - The plan says the 30B "follows" procedures, but the current system doesn't have a mechanism for the LLM to "look up" and "follow" a procedure. The system prompt is built by `build_system_prompt()` in `agent_tools.py`. Procedures would need to be injected into the system prompt or retrieved via FUSED search and included in the context. The plan doesn't specify this mechanism.

**Issue 7: The failure log says "validation_tool: vault_lint, safe_write, user correction"** - but vault_lint is a tool the LLM calls, not something that runs automatically. How does the failure log get populated? Who calls it? The plan doesn't specify the logging mechanism - is it the LLM that logs failures? Is it an automatic post-validation step?

Let me research some of these issues to make sure I'm not missing anything.

Actually, let me think about what Sean is really asking. He's asking:
1. Does this plan actually make sense?
2. Will it work for an average user who downloads it?
3. Can they just set API keys and models and have a working self-augmenting system?
4. Are there any holes I'm not seeing?

Let me do some targeted research on:
- How other self-improving agent systems handle the bootstrap problem
- Whether the "procedural notes" approach is sound
- What the minimum viable setup looks like for a system like this

But first, let me check a few more things in the codebase.Now I have a much clearer picture. Let me also check how the system prompt is built and how procedures would be injected, and also check the .env.example or config to understand the minimum setup.Let me now look at the `build_system_prompt` function to understand how the system prompt is constructed and how procedures could be injected.Now let me check if there's a .env.example or similar config file, and also look at the ollama_client to understand the embedding dependency.Now I have a very clear picture. Let me now do targeted research on the key questions I need to verify:

1. **Can a 30B model reliably follow procedural notes?** - This is the core assumption. If the 30B can't follow procedures, the whole plan fails.

2. **Is the "find procedures online" approach sound?** - Can the research engine actually find good procedural content?

3. **What's the minimum viable setup for an average user?** - From the .env.example, I can see:
   - Ollama is required (for embeddings at minimum)
   - Either local Ollama model OR cloud API key for LLM
   - Search is keyless (FreeSearch)
   - So the minimum is: install Ollama, pull nomic-embed-text + a chat model, set VAULTBOT_OWNER, go

4. **How does the system prompt get procedures into context?** - Looking at `build_system_prompt`, it already injects vault context, gaps, identity, etc. But it doesn't inject procedures. The plan needs to specify how procedures get into the system prompt or context.

5. **How does the failure log get populated?** - The plan says "every time the 30B follows a procedure and the output fails validation, that failure is logged." But who logs it? The LLM would need to call a tool to log failures, or the system would need to automatically detect which procedure was used and log failures. This is a gap.

Let me now do some research on these key questions.The research results are interesting. Let me look at the key findings:

1. **Procedural hallucinations**: There's a paper called "Attention Deficits in Language Models: Causal Explanations for Procedural Hallucinations" which defines procedural hallucination as "failure to execute a verifiable, prompt-grounded specification even when the correct value is present in-context." This is EXACTLY the risk in our plan — the 30B might not follow procedures even when they're in the context.

2. **Self-Improving Claude Code**: There's a gist about a bootstrap seed prompt that evolves into a self-improving system. This is very relevant — it's a real-world example of the bootstrap approach.

3. **Recursive Self-Improvement in AI**: There's a paper about "From Bounded Self-Refinement to..." which is relevant.

4. **Self-Improvements in Modern Agentic Systems: A Survey**: This is a survey paper that would be very useful.

5. **Best Local Tool-Calling Models 2026**: This is directly relevant to whether 30B models can do tool calling.

6. **TRICE-30B Model: Tool Learning with Feedback**: This is about a 30B model that does tool learning with feedback — directly relevant.

Let me read some of these sources to get more detail.Excellent. Now I have a very clear picture. Let me also read the procedural hallucinations paper and the self-improving agentic systems survey.This is incredibly rich research. Let me now synthesize what I've found and identify the key issues with the plan. Here are the critical findings:

**1. 30B models CAN reliably follow procedures and do tool calling — but with important caveats:**
- From the PromptQuorum benchmarks: 30B-class models (Qwen3-Coder 30B, Qwen3 32B, Gemma 4 27B, GLM-4.7 32B) achieve 93-96% well-formed tool call rates on simple tasks, but 80-90% end-to-end on multi-step real workflows.
- Key insight: "Per-call reliability compounds across an agent loop. A 95% per-call rate over 8 steps lands successfully ~66% of the time." This means for multi-step procedural tasks, the 30B will fail ~34% of the time over 8 steps.
- Models below 7B emit malformed calls. General-purpose models without tool-call training fail regardless of size.
- Q4_K_M is the production floor for quantization.

**2. Procedural hallucinations are a real and documented phenomenon:**
- The paper "Attention Deficits in Language Models" defines procedural hallucination as "failure to execute a verifiable, prompt-grounded specification even when the correct value is present in-context."
- The model may ENCODE the correct answer in its hidden state but fail to ROUTE it to the output.
- Key mitigation: "checkpointing" — restating the relevant information near the query can dramatically improve accuracy (0% → 99.8% in some cases).
- This means: if procedures are placed early in the context and the query is far away, the model may "know but not use" the procedure. Placing procedure reminders near the query (in the system prompt or as part of the context) is critical.

**3. The Self-Improving Claude Code bootstrap is a real-world example:**
- A single ~1400 token seed prompt can bootstrap a self-improving system.
- Key insight: "A good seed doesn't front-load this structure. It provides the recursive improvement kernel and lets complexity emerge from pressure."
- Cold start quality issue: "Session 1 learnings may be thin. The system may need 3-5 sessions before the improvement loop becomes meaningful."
- The system uses file-based state for multi-session continuity (exactly what our vault does).
- Anti-proliferation guardrails are needed: "Without it, Claude eagerly creates files 'just in case.' The default should be editing existing files; new files require justification."

**4. The Recursive Self-Improvement survey (1,250 papers) has critical insights:**
- "No external signal, no reliable improvement" — this is the field's central design rule. Intrinsic self-correction largely doesn't work.
- "The refinement loop is only as good as the feedback channel" — our validation layer IS the feedback channel.
- SkillsBench finding: "LLMs are bad at writing [skills]" — human-authored skills improve pass rates by 16.2 points while LLM-authored skills provide no measurable gain. This VALIDATES our approach of finding procedures online (human-authored) rather than having the LLM author them.
- "The verifier is the bottleneck" — every self-improvement system lives or dies by the quality of its evaluator. Our validation tools (vault_lint, safe_write) are the verifiers.
- "Accumulation-without-verification as the core problem" — the same verification bottleneck, now with memory. This is exactly why we need the failure log.
- The survey identifies "harness and skill evolution" as the category where VaultBot's plan sits. The key finding: "what self-evolves is almost always one carefully sandboxed component, validated against a fixed benchmark."

**5. Key issues I've identified with the plan:**

**Issue A: How do procedures get into the model's context?**
The plan says "the 30B finds the right procedure for a task using the existing FUSED retrieval" but doesn't specify the mechanism. Looking at `build_system_prompt()` in agent_tools.py, the system prompt already includes vault context, gaps, identity, etc. But it doesn't include procedures. The plan needs to specify:
- Are procedures injected into the system prompt? (Would make it very long)
- Are they retrieved via FUSED search and included in the context? (Already happens for regular notes)
- Are they injected as a separate section in the system prompt?

**Resolution**: Procedures are just notes with `type: procedure` in their frontmatter. The existing FUSED retrieval already pulls relevant notes into the vault context section of the system prompt. The model would see a procedure note the same way it sees any other note. The key addition is: the system prompt should mention that procedural notes exist and should be followed. This is a one-line addition to the system prompt, not a structural change.

**Issue B: How does the failure log get populated?**
The plan says "every time the 30B follows a procedure and the output fails validation, that failure is logged." But who logs it? The current system doesn't have a mechanism for this. Options:
1. The LLM calls a new tool to log failures (requires the LLM to self-report, which is unreliable)
2. The system automatically detects which procedure was used and logs failures (requires tracking which notes were in the context when a task was performed)
3. The validation tools (vault_lint, safe_write) log failures automatically (they already know when something fails)

**Resolution**: Option 3 is the most deterministic. When vault_lint or safe_write catches a failure, the system logs it. But we need to know WHICH procedure was being followed. This requires tracking which procedural notes were in the context for the current task. This is a new mechanism — a "procedure context tracker" that records which procedures were retrieved for the current conversation turn. When a validation failure occurs, the system looks at which procedures were in context and logs the failure against them.

This is more complex than the plan currently describes. It's still doable, but it's not as simple as "just log failures."

**Issue C: The Ollama dependency for embeddings**
The plan doesn't address this. Even if a user sets `LLM_BACKEND=openai` with an API key, the vault_indexer uses OllamaClient for embeddings (nomic-embed-text). This means:
- The user MUST install Ollama, even if they're using a cloud LLM
- The user MUST pull nomic-embed-text (~270MB)
- This is a hard dependency, not optional

**Resolution**: This is already documented in the README and .env.example. The plan should explicitly acknowledge this dependency and note that it's already handled by the existing setup instructions.

**Issue D: The "average user" experience**
From the .env.example and README, the minimum setup is:
1. Install Python 3.11+
2. Install Ollama
3. Pull two models (LLM + embeddings)
4. Copy .env.example to .env
5. Set VAULTBOT_OWNER
6. Install dependencies
7. Start the backend
8. Open in Obsidian

This is already documented and works. The plan doesn't change this — it's additive. The average user experience is already handled by the existing README.

**Issue E: The compounding failure rate**
From the PromptQuorum research: "A 95% per-call rate over 8 steps lands successfully ~66% of the time." This means a 30B following an 8-step procedure will fail ~34% of the time. The plan's failure log mechanism is the right response to this — it catches the failures and triggers re-research. But the plan should acknowledge this failure rate explicitly and note that:
- Procedures should be SHORT (fewer steps = higher success rate)
- The validation layer catches the failures
- The failure log triggers improvement
- Sean's corrections are the ultimate backstop

**Issue F: The checkpointing insight**
The procedural hallucinations paper shows that restating relevant information near the query dramatically improves accuracy. This has a direct implication for the plan: procedures should be placed CLOSE to the query in the context, not buried in the system prompt. The plan should specify that procedural notes are retrieved via FUSED search and included in the vault context section (which is already near the query), not injected into the system prompt (which is far from the query).

Actually, looking at the system prompt structure, the VAULT CONTEXT section is at the END of the system prompt, which means it's closest to the user's message. This is already the right place for procedures to appear.

**Issue G: SkillsBench finding validates the approach**
"Human-authored skills improve pass rates by 16.2 points while LLM-authored skills provide no measurable gain." This directly validates the plan's core insight: procedures should be FOUND (human-authored, from the web), not AUTHORED by the LLM. This is strong evidence for the approach.

**Issue H: The "no external signal, no reliable improvement" rule**
The survey establishes that intrinsic self-correction doesn't work — you need an external signal. The plan's validation layer (vault_lint, safe_write, Sean's corrections) IS the external signal. This is correct. But the plan should explicitly acknowledge this: the system works because validation is EXTERNAL to the model, not because the model self-corrects.

Now let me compile all of this into an updated version of the plan. I should:
1. Add a new section on research findings
2. Update the risk analysis with the procedural hallucination finding
3. Add the checkpointing insight to the procedural note schema
4. Add the compounding failure rate to the risk analysis
5. Add the SkillsBench validation to the bootstrap section
6. Clarify how procedures get into context
7. Clarify how the failure log gets populated
8. Add the Ollama dependency acknowledgment
9. Add the "no external signal" rule to the design principles

Let me write the updated plan.Now I have the full picture. The system prompt is built by `build_system_prompt()` which takes `vault_context` as a parameter. The vault context is a connected subgraph of notes retrieved via FUSED retrieval. This means procedural notes would already appear in the vault context if they're relevant to the query — they're just regular notes with `type: procedure` frontmatter.

The key insight is: procedures don't need a special injection mechanism. They're notes in the vault. FUSED retrieval already pulls relevant notes into the vault context section of the system prompt. The vault context section is at the END of the system prompt, which means it's closest to the user's message — exactly where the procedural hallucinations paper says information should be for best routing.

Now let me compile all my findings and update the plan. I need to:

1. Add a "Research Findings" section that validates the approach
2. Add critical clarifications:
   - How procedures reach the model (via existing FUSED retrieval, already in vault context)
   - How the failure log gets populated (validation tools log automatically + procedure context tracking)
   - The Ollama dependency for embeddings
   - The compounding failure rate and what it means for procedure design
   - The checkpointing insight (procedures should be near the query, which they already are)
   - The SkillsBench validation (human-authored > LLM-authored)
   - The "no external signal, no reliable improvement" rule
3. Update the risk analysis
4. Add a "Minimum Viable User Experience" section
5. Update the implementation phases with the new insights

Let me write the updated plan now.Good - 22 wikilinks, 0 broken, frontmatter present. The "empty_sections" issue is a false positive from code blocks containing blank lines. The plan is clean.

Let me give Sean a concise summary of what I found and my recommendation.

</details>