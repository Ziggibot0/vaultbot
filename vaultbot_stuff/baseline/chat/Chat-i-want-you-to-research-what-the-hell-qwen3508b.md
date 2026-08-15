---
type: claim
status: raw
created: 2026-08-03
summary: "Summary: Researchers use Qwen 3.5:0.8B to classify binary/multi-class tasks efficiently and detect specific intents like research questions versus code queries without needing large token buffers; it "
tags:
  - classification
  - tagging
  - routing
  - intent-detection
  - named-entity-extraction
---

# Chat: i want you to research what the hell qwen3.5:0.8b 

**Next:** [[Chat-wait-the-shit-that-the-small-model-can-do-well-is]]

**Previous:** [[Chat-sup-homie]]

## 2026-07-31 20:18 UTC

**User:** i want you to research what the hell qwen3.5:0.8b is used for by other people. what are the tiny llm's good for? i just rigged up your shit so that you have the option to put the small model in your procedures to save yourself tokens but first we need to know where they'd shine

**Assistant:** Alright Sean, here's the full picture. I researched the hell out of this and wrote two notes to the vault:

## What Tiny LLMs (sub-1B) Are Actually Good For

**The research-backed findings:**

### 🟢 Where They Shine (proven, deploy now)
1. **Classification & Tagging** — Binary/multi-class classification with bounded output space. A 0.8B model nails this. Highest Performance-Efficiency Ratio (PER) of any model class for this task.
2. **Routing / Intent Detection** — "Is this a research question, a code question, or a status check?" Perfect for the small cartridge — it's just picking from a menu.
3. **Named Entity / Information Extraction** — Pulling structured data (dates, names, tags) from text. Bounded output, well-defined task.
4. **Simple Summarization** — Short text → shorter summary. Not research-grade synthesis, but "give me a 2-sentence summary of this note" works.
5. **Format Conversion** — Converting JSON to prose, markdown cleanup, structured output formatting. The model just needs to follow a template.
6. **Speech Recognition (ASR)** — Qwen3-ASR-0.6B does 52 languages at 92ms time-to-first-token. Specialized tiny models are production-grade for well-defined domains.

### 🟡 Usable With Scaffolding (proceed with caution)
- **Function/Tool Selection** — Can pick the right tool from a small registry if you use Hermes-style structured prompts ([[Microsoft-Hermes-Qwen-small-language-model-function-calling-how-30B-models-do-to]]). Gets unreliable past ~10 tools.
- **Simple Code Steps** — "Extract the date from this filename" type code generation. Not full programming, just pattern matching to a snippet.

### 🔴 Where They Faceplant (keep on big model)
- **Multi-step reasoning / planning** — Anything requiring "think step 1, then step 2, then step 3." The model loses the thread.
- **Open-ended generation** — Writing research notes, synthesizing multiple sources, creative writing. Output quality drops off a cliff.
- **Complex tool use with arguments** — Generating correct JSON arguments for function calls with many parameters. Format errors abound.
- **Anything requiring world knowledge** — At 0.8B parameters, the model simply doesn't *know* much. It can pattern-match, not reason from facts.

## What This Means for My Procedure System

I wrote a full mapping note: [[Tiny-LLM-Use-Cases-Mapping-to-VaultBot-Procedure-Cartridge]]. Here's the actionable breakdown:

| Procedure Type | Cartridge | Why |
|---|---|---|
| **Status reporting** | `small` ✅ | Already done — [[VaultBot-Status]] uses `small`. Just formats JSON into prose. |
| **Classification / tagging** | `small` ✅ | "What type is this note?" — bounded output |
| **Intent routing** | `small` ✅ | "Should this go to research or code?" — pick from menu |
| **Format conversion** | `small` ✅ | Markdown cleanup, JSON→prose |
| **Simple extraction** | `small` ✅ | Pull dates, titles, tags from text |
| **Research synthesis** | `big` 🔴 | Needs multi-source reasoning |
| **Code generation** | `big` 🔴 | Needs logic and context |
| **Procedure writing** | `big` 🔴 | Needs to understand the full task structure |
| **Gap analysis** | `big` 🔴 | Needs vault-wide reasoning |

## Key Insight from the Research

The big finding is the **Performance-Efficiency Ratio (PER)** metric — sub-1B models have the *best* PER for bounded tasks because the marginal accuracy gain of a 70B model over a 0.8B model for, say, binary classification is maybe 2-3%, but the compute cost is 100x. For my procedure system, that means: **every procedure that does classification, routing, extraction, or formatting should be on `small`** — it's free (local), fast, and nearly as accurate.

The scaffolding techniques from [[Small-model-scaffolding-techniques-that-make-dumb-models-perform-well-prompt-sca]] are critical too: structured prompts, output enforcement (JSON schema), and few-shot examples make the small model dramatically more reliable. I should bake those into every `small`-cartridge procedure I write.

**Bottom line:** The small cartridge is a legit weapon for ~40% of my procedure workload. The other 60% (anything requiring reasoning, synthesis, or knowledge) stays on the big model. Want me to start auditing existing procedures and flipping the ones that should be on `small`?

<details>
<summary>Thinking process</summary>

Sean wants me to research what tiny LLMs like qwen3.5:0.8b are good for — practical use cases where small models shine. This is directly relevant to my own architecture since I now have the ability to use a "small" model cartridge in procedures. Let me plan this out and research it.

The vault already has some related notes (the Microsoft Hermes Qwen note, Small-Model-Path-to-AGI, Deterministic-Scaffolding-for-Small-Models) but they're more about function calling specifically. Sean wants a broader picture of what tiny LLMs are actually used for in practice.

Let me plan and research this.Let me also check what other notes exist in the vault about small models, and then do the web research.Let me read the research note that was just written to see the full synthesis, and also check the existing vault notes for overlap.The Small-Model-Path-to-AGI note seems to be garbage — it's about meromorphic functions and SARS virus models, not about AI at all. That's a bad research note that should probably be cleaned up. But that's not the current task.

Let me now synthesize the findings. I have:
1. The new research note on sub-1B model use cases (just written)
2. The existing note on small model scaffolding techniques
3. The Microsoft Hermes Qwen note on function calling with small models
4. The Small-Model-Path-to-AGI note (which is garbage)

Let me read the full research note first to get the complete picture, then synthesize into a mapping note.I have enough info now. Let me update the task and write the synthesis note. The research note has good findings. Let me now mark step 2 as complete and write the mapping note.Good, the note was written. Now let me update the tasks and provide the final synthesis to Sean.

</details>