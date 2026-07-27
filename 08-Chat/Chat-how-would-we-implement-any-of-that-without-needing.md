# Chat: how would we implement any of that without needing

## 2026-07-26 00:16 UTC

**User:** how would we implement any of that without needing an LLM to first make the procedures? and if we just seed it, how does the vaultbot grow over time and not get stuck with stale methods?

**Assistant:** This is the right question — it's the chicken-and-egg at the heart of the whole design. Let me think through it properly.

## The Bootstrap Problem: Who Writes the First Procedures?

The answer is: **nobody writes them. We find them.**

The internet already contains procedures for almost everything VaultBot needs to do:

| What VaultBot needs | What already exists online |
|---|---|
| How to research a topic | Research methodology guides, literature review procedures |
| How to structure a technical note | Zettelkasten guides, academic writing templates |
| How to validate output | Schema validation patterns, QA checklists |
| How to decide when to research vs answer | Decision trees from agent framework docs |
| How to write a Python tool | Python style guides, tool scaffolding patterns |
| How to organize a knowledge base | PKM best practices, Obsidian guides |

The research engine already works — it scrapes, extracts, corroborates. It doesn't need an LLM to *invent* a procedure. It needs to *find* one someone else already wrote and store it. The LLM's role in research is formatting and synthesis, not creation. The content comes from the web.

**The seed is tiny.** We're not starting from zero — the vault already has directives (Autonomy, IDK-Fallback, Vault-Knowledge-Only). Those ARE procedures, just written for LLM interpretation. The shift is making them more deterministic: instead of "be honest about uncertainty" → "if confidence < threshold → respond with IDK template." That's a refactor of existing content, not new authoring.

And the research engine itself is already deterministic enough to bootstrap. It doesn't need a procedure to tell it how to research — it already does that. It just needs to be pointed at "how to do X" instead of "what is X." That's a query change, not an architecture change.

## The Staleness Problem: How Does It Evolve?

This is where it gets interesting. Four deterministic mechanisms, no LLM judgment required:

### 1. Failure-Driven Evolution
Every time the 30B follows a procedure and the output fails validation (vault_lint catches bad structure, safe_write catches bad code, the user reports a bad answer), that failure is logged. When failures on a given procedure exceed a threshold, the background researcher re-researches the topic and updates the procedure.

```
procedure_used → output → validation → pass/fail
                                    ↓ fail
                            failure_log[procedure] += 1
                                    ↓ threshold exceeded
                            re_research(procedure.topic)
                            update_note(procedure, new_findings)
```

This is mechanical. No LLM decides "this procedure is stale" — a counter trips a threshold.

### 2. Time-Driven Re-Research
Every procedural note gets a `last_reviewed` date in its frontmatter. The background researcher periodically checks for notes where `last_reviewed` is older than N months. Those get re-researched automatically. The new findings are compared against the existing procedure. If they differ, the note is updated. The old version is preserved in git history.

### 3. Source-Driven Updates
Every procedural note cites its sources. The background researcher can periodically check whether those sources have been updated (HTTP last-modified, page content hash). If a source has changed, the note is flagged for re-research. This catches the case where the world has moved on but the procedure hasn't.

### 4. Quality-Driven Promotion
New procedures start as `status: experimental`. They run alongside the old procedure on a few tasks. If the new one produces fewer validation failures, it graduates to `status: verified` and becomes the default. The old one is archived. This is A/B testing, but the metric is deterministic: validation pass rate, not LLM judgment.

## The Deeper Point

The model doesn't evolve. The vault does. This is the whole thesis of [[Small-Model-Path-to-AGI]]:

- The 30B is a swappable cartridge. It executes whatever the vault tells it.
- The vault learns by finding new procedures, updating old ones, and tracking what works.
- The learning happens at the *knowledge* level (vault), not the *execution* level (model).

A 30B model in 2026 and a 30B model in 2028 follow the same procedures. But the procedures in the vault in 2028 are better — because they've been re-researched, failure-tested, and quality-promoted. The system gets smarter without the model getting smarter.

## The Bootstrap Loop, Concretely

```
Day 1:
  - Existing directives are refactored into deterministic rules
  - Research engine finds 5-10 core procedures online, stores them
  - 30B starts operating: follows procedures, produces output, validation checks it

Day 2-N:
  - 30B hits a wall (validation fails, or IDK fires)
  - Failure logged → background researcher re-researches the gap
  - New procedure found → stored → 30B uses it next time
  - Old procedures age → re-researched on schedule
  - Bad procedures fail → replaced by better ones
  - Good procedures succeed → verified, promoted
```

The loop is: **try → fail → find better way → store it → try again.** That's not an LLM reasoning loop. That's a deterministic feedback loop with an LLM as the execution engine.

---

Want me to write this up as a vault note and start building the failure-tracking mechanism? The failure log is the keystone — without it, the system can't learn from its mistakes, and evolution becomes purely time-based (which is better than nothing but much weaker).

<details>
<summary>Thinking process</summary>

This is a really sharp question. Sean is asking two things:

1. How do we bootstrap the deterministic procedures without needing the cloud LLM to write them first? (The chicken-and-egg problem)
2. If we seed initial procedures, how does the system evolve and not get stuck with stale methods?

Let me think deeply about this before answering.

**Problem 1: The Bootstrap Problem**

The concern is: if we need procedures/templates/decision trees for the 30B to follow, who writes them? If the cloud LLM writes them, we're back to cloud dependency. If Sean writes them manually, that's a lot of work and defeats the purpose.

The answer is: **the internet already has these procedures.** The research engine can find them. The key insight is that procedures, templates, and decision trees already exist online — they're not something that needs to be *generated*, they need to be *found and stored*.

For example:
- "How to write a research note" → there are thousands of guides on academic note-taking, Zettelkasten, etc.
- "How to structure a Python tool" → Python style guides, tool documentation
- "How to decide when to research vs answer from existing knowledge" → this is a decision tree that can be found in agent framework documentation
- "How to validate LLM output" → schema validation patterns are well-documented

But wait — the research engine itself uses an LLM to synthesize. So even finding procedures requires an LLM. The point is: the research engine's LLM is doing *retrieval and formatting*, not *reasoning*. It's finding what someone else has already written and storing it verbatim or near-verbatim. That's fundamentally different from the LLM *inventing* the procedure.

Actually, let me think about this more carefully. The vault_research tool scrapes the web, extracts facts, and synthesizes them into a note. The synthesis step uses an LLM. But the *content* comes from the web. So the procedure isn't being invented by the LLM — it's being found on the web and stored.

But there's a subtler issue: how do you know *what* to research? The background researcher finds gaps (dangling wikilinks, thin notes) and researches them. But how does it know to research "how to write a decision tree for tool selection"? That requires knowing that the system needs a decision tree in the first place.

This is where the bootstrapping gets interesting. There are a few approaches:

**Approach A: Seed with minimal procedures, let the system discover needs**
Start with a few core procedures (found online, stored in vault). The system runs. When it hits a wall (the 30B can't do something), that's a signal that a procedure is missing. The gap detection system flags it. The research engine finds the procedure online. The vault stores it. Next time, the 30B has the procedure.

This is essentially the same mechanism as the current gap-filling: the system discovers what it doesn't know and researches it. The difference is that now it's discovering *procedural* gaps, not just *factual* ones.

**Approach B: The procedures are self-describing**
The vault already has directives (Autonomy, IDK-Fallback, Vault-Knowledge-Only, etc.). These ARE procedures — they're just written for an LLM to interpret. The shift is to make them more deterministic: instead of "be honest about uncertainty" → "if confidence < threshold, respond with IDK message." The directives can be evolved the same way they were created: research best practices, store them, follow them.

**Approach C: Procedures are found, not authored**
The key insight is that almost every procedure VaultBot needs already exists online:
- How to do a literature review → research methodology guides
- How to structure a technical note → writing guides
- How to validate code output → software engineering practices
- How to make decisions under uncertainty → decision theory
- How to organize a knowledge base → PKM guides

The research engine finds these, stores them as procedural notes, and the 30B follows them. No LLM needs to *invent* the procedure — it just needs to *find* it.

**Problem 2: The Staleness Problem**

If we seed procedures, how do they evolve? How does the system not get stuck with 2026 methods when 2028 methods are better?

Several mechanisms:

1. **Time-based re-research** — Every procedural note has a "last reviewed" date. After N months, the background researcher re-researches the topic and updates the note. This is a deterministic trigger, not an LLM judgment.

2. **Failure-driven evolution** — When the 30B follows a procedure and the output fails validation (vault_lint catches bad output, safe_write catches bad code, the user reports a bad answer), that's a signal the procedure is stale. The system re-researches the topic and updates the procedure.

3. **Versioning and A/B testing** — When a new procedure is found, it doesn't replace the old one immediately. It's stored alongside. The system tries the new procedure on a few tasks. If the new one produces better validation results (fewer lint failures, fewer user corrections), it becomes the default. The old one is archived. This is a deterministic quality metric.

4. **The vault's own evolution** — The A-MEM layer already evolves tags and links. If a procedural note is frequently linked to and successfully used, it gains weight. If it's rarely linked or frequently fails, it loses weight. The system naturally surfaces what works and buries what doesn't.

5. **External signals** — The research engine can be configured to periodically check for new versions of the sources it cited. If a source has been updated, the procedural note is flagged for review.

Actually, let me think about this differently. Sean's question has a deeper philosophical point: if the procedures are deterministic, how does the system *learn*? Deterministic systems don't learn — they just execute. The answer is that the *learning* happens at the vault level, not the execution level. The 30B executes deterministically. The vault evolves. New procedures are found through research, old ones are updated or replaced. The system learns by updating its knowledge, not by changing its execution.

This is exactly the [[Small-Model-Path-to-AGI]] insight: the mind is the vault, not the model. The model is a swappable cartridge. The vault evolves. The model just executes.

Let me also think about the practical implementation. How would this actually work?

**The Bootstrap Loop:**
1. Start with a minimal set of procedures (found online via research, not authored by LLM)
2. The 30B follows the procedures to do tasks
3. When a task fails (validation catches bad output), the failure is logged
4. The background researcher sees the failure log and researches "how to do [task] better"
5. New procedure is found and stored
6. Next time, the 30B has the improved procedure

**The Evolution Loop:**
1. Every procedural note has a `last_reviewed` date in frontmatter
2. The background researcher periodically re-researches topics where `last_reviewed` is old
3. When re-researching, it compares the new findings with the existing procedure
4. If the new findings are different (new best practices, new tools, new methods), the note is updated
5. The old version is archived (git history preserves it)

**The Quality Loop:**
1. Every time a procedure is used, the outcome is tracked (success/fail, validation score)
2. Procedures with high failure rates are flagged for re-research
3. Procedures with high success rates are marked as "verified"
4. New procedures start as "experimental" and graduate to "verified" based on track record

This is a Voyager-style curriculum but for procedures, not just facts. The system discovers what it doesn't know how to do, finds the procedure, tries it, and tracks the outcome.

Let me now think about whether I should research this or if I have enough to answer. I think I have enough to give a strong answer based on what's already in the vault and my understanding of the architecture. But let me check if there's anything in the vault about this specific question.

Actually, I think this is a reasoning question, not a research question. Sean is asking me to think through the architecture, not find a fact. Let me answer from what I know.

But wait — I should check if there's existing vault content on this. Let me search for "bootstrapping" or "procedural knowledge" or "evolution" in the vault.

Actually, I think I can answer this well from what I already know. The key insights are:

1. **Procedures are found, not authored** — The internet has how-to guides for everything. The research engine finds them. No LLM needs to invent them.

2. **The bootstrap is minimal** — Start with a few core procedures (how to research, how to write a note, how to validate output). Everything else is discovered through gap detection + research.

3. **Evolution is failure-driven** — When the 30B follows a procedure and fails, that's a signal to re-research. The system learns from its mistakes, not from an LLM's judgment.

4. **Evolution is time-driven** — Procedural notes have review dates. Old procedures get re-researched automatically.

5. **Evolution is quality-driven** — Procedures are tracked by success rate. Bad procedures get replaced. Good procedures get verified.

6. **The vault is the evolution mechanism** — The model doesn't evolve. The vault does. New procedures are added, old ones are updated. The model just follows whatever the vault says.

Let me also think about the "stale methods" concern more concretely. If we seed a procedure for "how to write a research note" in 2026, and by 2028 there's a better way, how does the system discover it?

- **Time trigger**: The note's `last_reviewed` date triggers a re-research after N months
- **Failure trigger**: If the procedure starts producing more validation failures (maybe because the web has changed, or the topics have shifted), the failure rate triggers a re-research
- **External trigger**: If a cited source has been updated, the note is flagged
- **User trigger**: Sean says "this note sucks, update it" — and the system re-researches

The key is that all of these are *deterministic triggers*, not LLM judgment. The system doesn't need to "realize" the procedure is stale — a timer fires, a failure counter exceeds a threshold, a source check finds an update. These are all mechanical checks.

I think I have a strong answer. Let me also think about whether I should write a note about this or just answer in conversation. I think this is important enough to write a note — it's a key architectural decision. But let me answer Sean first and then offer to write it up.

Actually, let me think about one more thing: the chicken-and-egg problem more carefully. The very first procedures — how do they get into the vault?

Option 1: Sean manually writes a few starter procedures. This is minimal — maybe 5-10 procedures. Not a huge burden.

Option 2: The research engine finds them. "How to do autonomous research" → find a guide online → store it. "How to write a technical note" → find a guide → store it. The research engine itself is deterministic enough to do this without needing procedures first (it already works today).

Option 3: The existing directives ARE the first procedures. They're already in the vault. They just need to be made more deterministic (turn "be honest about uncertainty" into "if confidence < threshold → IDK response").

I think the answer is a combination of all three. The existing directives are the seed. The research engine finds more. Sean might write a few manually. The system evolves from there.

Let me now formulate my answer. I want to be concise but thorough. This is a key architectural question and Sean deserves a real answer, not hand-waving.

</details>