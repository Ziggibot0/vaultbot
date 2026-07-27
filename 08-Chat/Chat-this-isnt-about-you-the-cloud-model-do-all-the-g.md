# Chat: this isn't about "you the cloud model do all the g

## 2026-07-25 21:52 UTC

**User:** this isn't about "you the cloud model do all the grunt work so that i can use a local model" it's actually "build the framework so that the weak model+framework CAN do everything that you can"

**Assistant:** You're right — I had the framing backwards. Let me restate it correctly:

**The goal isn't "strong model does grunt work so weak model can coast."** The goal is **"build the framework so that weak model + framework = strong model alone."** The framework compensates for what the model lacks.

That changes what matters. Let me think through what a weak model actually can't do well, and what the framework would need to do *for* it:

---

## What a Weak Model Sucks At (and What the Framework Must Replace)

### 1. Synthesis
**Weak model:** Reads 5 notes, lists facts, can't connect them.
**Framework fix:** A `vault_answer` tool that does extractive synthesis from vault notes — same approach as the research engine already uses for web sources. Score sentences by keyword relevance + corroboration across notes, assemble the best ones with citations. The LLM relays the result instead of synthesizing from scratch.

The research engine already proves this works. It does extractive synthesis from web sources with zero LLM. The same logic works on vault notes. **Move synthesis from the LLM into the framework.**

### 2. Planning / Multi-step Reasoning
**Weak model:** Can't figure out "I should research X, then write a note, then lint it, then link it."
**Framework fix:** Pre-define workflows as single tools. `learn_topic(topic)` = research + write + lint + link + report. The model calls one tool. The framework handles the orchestration.

### 3. Tool Selection
**Weak model:** Might pick the wrong tool or pass bad parameters.
**Framework fix:** A routing layer that pre-classifies the message before the LLM sees it. "This is a question about X" → pre-fetch vault context. "This is a research request" → pre-trigger research. "This is small talk" → skip tools entirely. The LLM gets a pre-routed message with a suggested action, not a blank slate.

### 4. Note Writing
**Weak model:** Writes inconsistent, poorly-structured notes.
**Framework fix:** Templates with slots. The model fills `## Summary`, `## Key Findings`, `## Sources` — it doesn't design structure. The framework enforces the template.

### 5. Context Utilization
**Weak model:** Gets 5 notes of context, only really uses 2.
**Framework fix:** Tighter retrieval + pre-ranked context. Instead of dumping a subgraph, the framework extracts the most relevant passages from each note and presents them ranked. The LLM gets a digest, not a dump.

---

## The Core Principle

**Every cognitive function that currently depends on the LLM should be moved into the framework.** The LLM becomes a relay — it reads pre-synthesized results and produces natural language. The thinking happens in the framework.

The research engine already does this for web research. The fused retriever already does this for search. The curriculum already does this for gap detection. The hole is: **vault-based answer synthesis still relies on the LLM.** That's the gap to close.

---

## What I'd Build (In Priority Order)

1. **`vault_answer` tool** — Extractive synthesis from vault notes. Same scoring approach as the research engine, but over vault content instead of web sources. Returns a pre-synthesized, cited answer. The LLM relays it. This is the single biggest leverage point.

2. **`learn_topic` tool** — One-call pipeline: research → write note from template → lint → link → report. Eliminates multi-step planning.

3. **Note templates** — Slot-based structure for research notes, directives, chat logs. The framework enforces structure, not the model.

4. **Message routing** — Pre-classify incoming messages (question, command, research request, small talk) and pre-fetch the right context before the LLM sees it. Reduces the model's decision burden.

5. **Pre-computed connection notes** — The autonomous researcher writes synthesis notes that connect related notes. When a question comes in, the framework finds the relevant connection note and feeds it pre-synthesized.

Want me to start building `vault_answer`? It's the highest-leverage piece — it directly replaces the LLM's hardest job with an algorithmic one.

<details>
<summary>Thinking process</summary>

Sean is clarifying the goal. It's not about me (the current strong model) doing work to make things easier for a future weak model. It's about building the framework/tooling so that a weak model + the framework can match what a strong model can do alone.

This is a fundamentally different framing. Let me think about what this means:

The idea is: if you have a weak model (say Qwen 3.6 30B), can the framework (tools, vault structure, retrieval, templates, pre-computed synthesis) compensate for the model's weakness so that the combined system matches what a strong model (Claude) can do?

This is like asking: can a mediocre driver in a great car beat a great driver in a mediocre car? The answer is: only if the car compensates for exactly the driver's weaknesses.

What are a weak model's weaknesses?
1. **Poor synthesis** — can't connect ideas across notes as well
2. **Poor reasoning** — can't plan multi-step operations as well
3. **Poor instruction following** — might miss nuances in directives
4. **Poor natural language generation** — less coherent prose
5. **Poor tool selection** — might pick wrong tools or wrong parameters
6. **Poor context utilization** — might not use all the context it's given

What would the framework need to do to compensate for each?

1. **Poor synthesis** → Pre-compute synthesis into the vault. Connection notes, summary notes, topic overviews. The autonomous researcher should be writing these. Then the weak model just reads the pre-synthesized note and relays it.

2. **Poor reasoning** → Pre-define workflows as single tools. Instead of the model having to figure out "I should research X, then write a note, then lint it, then link it," make a `learn_topic` tool that does all of that in one call. The model just calls one tool.

3. **Poor instruction following** → Make the system prompt more structured and rule-based. Instead of prose instructions, use checklists. Instead of "be concise but thorough," use "max 500 words, cite at least 2 sources."

4. **Poor NLG** → Note templates with fill-in-the-blank structure. The model fills slots, not designs prose.

5. **Poor tool selection** → Better tool descriptions with clear "when to use" criteria. Maybe even a routing layer that pre-selects tools based on the query type.

6. **Poor context utilization** → Better retrieval that surfaces exactly the right context. Tighter, more relevant context injection. Maybe even pre-ranked context.

The key insight is: **the framework should do the thinking, not the model.** The model should be a relay, not a thinker. Every cognitive function that currently depends on the LLM should be moved into the framework:

- **Reasoning** → Pre-defined workflows (tool chaining)
- **Synthesis** → Pre-computed connection notes
- **Planning** → Templates and recipes
- **Retrieval** → Better fused retrieval (already good)
- **Writing** → Templates with slots
- **Decision-making** → Algorithmic routing (when to research, when to answer from vault)

This is a really important architectural insight. Let me think about what concrete tools/changes would make this real.

Actually, let me think about this more carefully. The current system already does a lot of this:
- Research is LLM-free (extractive synthesis)
- Retrieval is LLM-free (fused vector + graph)
- Gap detection is LLM-free (curriculum)
- Note writing by the autonomous researcher is LLM-free (template-based)

The LLM is only used for:
1. Tool selection in the chat loop
2. Final prose synthesis

So the question is: can we make tool selection and final synthesis good enough with a weak model?

For tool selection: The system prompt already gives clear guidance on when to use each tool. A weak model can follow "if vault is thin, call vault_research." This is already pretty model-agnostic.

For final synthesis: This is the hard part. But if the vault has rich notes, the synthesis becomes "read these notes and summarize" which even a weak model can do. The key is vault density.

But Sean is saying something deeper: build the framework so the weak model + framework CAN do everything. This means the framework should actively compensate for model weakness, not just passively rely on vault density.

Concrete ideas:

1. **A "smart answer" tool** — Instead of the LLM synthesizing from raw context, a tool that:
   - Takes the user's question
   - Runs fused retrieval
   - Reads the top notes
   - Extracts relevant passages
   - Assembles a structured answer with citations
   - Returns it as a pre-synthesized answer the LLM just relays
   
   This moves synthesis from the LLM into the framework. The LLM just reads the pre-synthesized answer and maybe polishes it.

2. **A "smart routing" layer** — Before the LLM even sees the message, the framework decides:
   - Is this a question? → search vault, prepare context
   - Is this a command? → identify the right tool
   - Is this a research request? → pre-run the research
   - Is this small talk? → just respond
   
   This reduces the LLM's decision burden.

3. **Structured output templates** — Instead of free-form prose, the framework provides templates:
   ```
   ## Answer
   [answer from vault notes]
   
   ## Sources
   - [[Note-1]]
   - [[Note-2]]
   
   ## Confidence
   [high/medium/low based on vault coverage]
   ```
   The LLM fills the [answer] slot by reading the notes. Everything else is framework-generated.

4. **Pre-computed connection notes** — The autonomous researcher writes notes that synthesize related notes. When the user asks a question, the framework finds the relevant connection note and feeds it to the LLM. The LLM relays it.

5. **A "vault coverage" check** — Before the LLM answers, the framework checks how well the vault covers the topic. If coverage is high, the LLM is told "answer from vault, don't use training data." If coverage is low, the LLM is told "research this first." This removes the LLM's judgment call.

Actually, I think the most powerful thing would be to build a tool that does the synthesis FOR the LLM. Right now, the LLM receives vault context (a subgraph of notes) and has to synthesize an answer. What if instead, a tool did the synthesis and returned a pre-written answer? The LLM just relays it.

This would be like a `vault_answer` tool that:
1. Takes the user's question
2. Runs fused retrieval
3. Reads the top notes
4. Extracts relevant passages using keyword matching
5. Assembles a structured answer with citations
6. Returns it

The LLM's job becomes: "Here's a pre-synthesized answer. Relay it to the user, maybe polish the prose."

This is the key insight: **move synthesis from the LLM into the framework.** The research engine already does this for web research (extractive synthesis). The same approach should work for vault-based answers.

Let me think about what to actually build. I think the most impactful thing would be:

1. **A `vault_answer` tool** — Does extractive synthesis from vault notes. The LLM relays the result.
2. **Note templates** — Standardize note structure.
3. **A `learn_topic` tool** — One-call research pipeline.
4. **Pre-computed connection notes** — Autonomous researcher writes synthesis notes.

But Sean's point is about the FRAMEWORK, not specific tools. The framework should be designed so that a weak model can operate it effectively. This means:
- Less decision-making for the model
- More pre-computed results
- More structured outputs
- More algorithmic routing

Let me respond to Sean with this understanding. I should be concise and direct, acknowledging the reframing and proposing concrete framework changes.

</details>