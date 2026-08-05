---
type: claim
status: raw
created: 2026-08-03
summary: "Sean breaks down the mathematical advantage of small models on tool execution vs. chat turns in a session; tags: math, tools, chat_turns"
tags:
  - chatbot_response_tool_calls
  - search_engine_integration
  - tool_calls
  - research
  - synthesis
---

# Chat: wait the shit that the small model can do well is 

**Next:** [[Chat-yes-please]]

**Previous:** [[Chat-i-want-you-to-research-what-the-hell-qwen3508b]]

## 2026-07-31 20:23 UTC

**User:** wait the shit that the small model can do well is like most of what you have to do anyway wtf lol we could prolly cut cloud tokens in half with this shit

**Assistant:** Sean you're not wrong — let me break down why you're *more right than you probably realize*.

## The Math Says You're Right

Look at what I actually *do* across a typical session. Most of my tool calls are **deterministic** — `vault_search`, `code_read`, `code_run`, `vault_safe_write`, `plan_task`, `update_task`. Zero LLM tokens. The LLM only fires on:

1. **Chat turns** (talking to you) — needs big model, no way around it
2. **Procedure LLM steps** — THIS is the target
3. **Research synthesis** — needs big model

But here's the thing — look at what the LLM steps in procedures actually *are*:

| LLM step in a procedure | What it does | Small model? |
|---|---|---|
| VaultBot-Status | Format JSON → prose | ✅ Already `small` |
| Classify note type | Pick from [research, procedure, system, journal] | ✅ Bounded output |
| Extract tags from text | Pull keywords from a paragraph | ✅ Extraction |
| Route intent | "Is this a code task or research task?" | ✅ Menu pick |
| Format research output | Convert structured facts → markdown | ✅ Template fill |
| Summarize a note | 2-sentence summary | ✅ Short text |
| Check note quality | Does it have frontmatter? Broken links? | ✅ Classification |
| Research synthesis | Multi-source reasoning | 🔴 Big |
| Write a procedure | Understand task structure | 🔴 Big |
| Complex planning | Multi-step reasoning | 🔴 Big |

That's **6 out of 10 LLM steps that are small-model territory.** And the 4 that need the big model? Those are the ones that *already* use the most tokens — but they're a minority of the *calls*.

## Where the Real Savings Come From

It's not just "use small model for easy steps." It's the **cascade effect** from the [[Token-Efficiency-Audit]]:

Right now, every LLM step in a procedure fires a full API call to the cloud model — which means it sends the **entire system prompt (~17K tokens)** every single time. Even if the LLM step is just "classify this note type," it costs 17K+ tokens because the cloud model needs the full context.

With the small cartridge, those same steps run **locally on qwen3.5:0.8b** — zero cloud tokens, zero API cost, and they run in milliseconds on your machine. The big model only wakes up for the stuff that actually needs it.

**Rough estimate:** If I convert 6/10 procedure LLM steps to `small`, and each of those was costing ~17K tokens minimum per call... that's cutting maybe **40-50% of cloud token usage** on procedure-heavy sessions. You're literally right.

## What I Should Do Next

I should audit every existing procedure and convert the ones that are doing small-model-friendly work. And going forward, every new procedure I write should default to `small` unless it genuinely needs multi-source reasoning or complex planning.

Want me to do that audit now? I can go through all the procedures in the vault, classify which ones should be `small` vs `big`, and flip the ones that are wasting cloud tokens. 🧠

<details>
<summary>Thinking process</summary>

Sean is realizing that most of what I do in procedures — classification, routing, extraction, formatting, tagging — maps directly to what tiny LLMs are good at. He's right, and this is a big insight. Let me think about this carefully.

Looking at my token flow from the [[Token-Efficiency-Audit]] note in the vault context — the conversation history is the #1 sink, and most of my tool rounds involve exactly the kinds of tasks that a small model could handle. Let me think about what percentage of my actual work maps to small-model-friendly tasks.

Let me look at what I actually do across a typical session:
1. vault_search — retrieval, no LLM needed
2. vault_research — this uses the big model for synthesis, but the individual steps (search, scrape, extract facts) could partially use small model
3. code_read / code_run — deterministic, no LLM
4. plan_task / update_task — deterministic, no LLM
5. vault_safe_write — deterministic, no LLM
6. The LLM steps in procedures — these are where the small model cartridge matters

Looking at my existing procedures and what I do:
- Status reporting → small ✅ (already done)
- Classification of note types → small ✅
- Intent routing → small ✅
- Extraction (dates, tags, titles) → small ✅
- Format conversion → small ✅
- Simple summarization → small ✅

The things that NEED the big model:
- Research synthesis (multi-source reasoning)
- Complex code generation
- Procedure writing (understanding task structure)
- Gap analysis (vault-wide reasoning)
- Planning multi-step tasks

But honestly, how much of a typical session is synthesis vs. the mechanical stuff? A lot of my tool rounds are just: search vault, read a file, write a note, update task status. The LLM steps within procedures are often just "format this" or "classify that."

Sean's right — this could cut cloud tokens significantly. Let me give him a real analysis based on the Token-Efficiency-Audit data.

I should be enthusiastic but also concrete. Let me think about the actual math and what procedures I should convert.

</details>