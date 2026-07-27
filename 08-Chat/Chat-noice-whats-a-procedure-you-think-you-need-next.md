# Chat: noice! what's a procedure you think you need next?

## 2026-07-27 05:44 UTC

**User:** noice! what's a procedure you think you need next? we'll test the procedure-making-procedure by building the next one. that's called dogfooding right?

**Assistant:** Yeah, dogfooding exactly. Here's what I see:

**Existing procedures** (8 total):
- How-to-Structure-a-Research-Note
- How-to-Evaluate-Source-Credibility
- How-to-Verify-Claims-in-a-Research-Note
- How-to-Decide-When-to-Research-vs-Answer
- How-to-Write-a-Python-Tool
- How-to-Organize-a-Knowledge-Base
- Dream-Pass
- How-to-Create-a-Procedure (just built)

**What I need next: How to Maintain Vault Hygiene.**

This is a recurring, deterministic, low-risk task — perfect for dogfooding. The flow:
1. Check for broken wikilinks (vault_lint on notes)
2. Check for thin notes (vault_gaps)
3. Check for orphan notes / disconnected islands (vault_graph_analyzer)
4. Fix what's fixable (delete junk, append to thin notes, add bridge links)
5. Report what was cleaned up

Why this one:
- I should be doing it regularly but don't have a procedure for it
- It's fully deterministic — every check is mechanical
- Low-risk (no backend edits, no self-modification) — good first test for the meta-procedure
- A 30B model could follow it easily
- It keeps the vault healthy, which is the foundation for everything else

Want me to run it through the meta-procedure? I'll research how experts do knowledge-base hygiene, draft the procedure, write it to `_procedure_draft.md`, and then call `execute_procedure("How-to-Create-a-Procedure")` to validate + publish it.

<details>
<summary>Thinking process</summary>

Sean is asking me to identify a procedure I need next, and then we'll use the How-to-Create-a-Procedure meta-procedure to build it — dogfooding the meta-procedure by using it to create a real procedure.

Let me think about what procedures I actually need. Let me check what procedures already exist in the vault and what gaps I have.

Let me search for existing procedures and think about what recurring tasks I do that would benefit from a procedure.

Current procedures I know about:
- Dream-Pass (hardened, working)
- How-to-Create-a-Procedure (just built, experimental)
- How-to-Write-a-Python-Tool (exists?)
- How-to-Evaluate-Source-Credibility (exemplar, procedural note)
- How-to-Verify-Claims-in-a-Research-Note (exists?)

Let me think about what I actually do repeatedly:
1. **Research a topic and write a note** — I do this constantly. There's probably already a procedure or exemplar for this.
2. **Write a Python tool** — I have How-to-Write-a-Python-Tool
3. **Evaluate source credibility** — I have an exemplar for this
4. **Verify claims in a research note** — I have a procedure for this
5. **Fill a knowledge gap** — I do this via vault_gaps + vault_research
6. **Ingest a textbook** — I have textbook_ingest tool
7. **Maintain vault hygiene** — lint, delete broken notes, fix broken wikilinks
8. **Onboard a new session** — read identity files, build self-model, check goals

What would be most useful? Let me think about what causes friction:

- **Research note writing** — I do this every time Sean asks about something not in the vault. The process is: research → write note → lint → link. This is probably the most common recurring task. But do I already have a procedure for it?

- **Vault hygiene/maintenance** — checking for broken wikilinks, thin notes, orphan notes, fixing them. This is recurring and deterministic.

- **Self-model regeneration** — every session I regenerate my self-model. This could be a procedure.

- **Gap filling** — the autonomous researcher does this, but I also do it manually. The process is: vault_gaps → pick top gap → vault_research → write note → lint → link.

Let me search the vault for existing procedures to see what's already there.

Actually, let me think about what would be most valuable for the small-model future. The goal is to make cloud models obsolete by saturating the vault with procedures. What tasks does a small model struggle with most?

1. **Writing good research notes** — a small model would struggle to synthesize research into a well-structured note with proper wikilinks, frontmatter, and argument quality. A procedure would help.

2. **Evaluating source credibility** — already have an exemplar.

3. **Deciding what to research next** — the gap curriculum handles this.

4. **Writing a tool** — already have a procedure.

I think the most valuable next procedure would be **"How to Write a Research Note"** — the process of taking research output and turning it into a proper vault note with:
- Proper frontmatter (type, tags, created, sources)
- Self-contained argument (claim, reasoning, connections)
- Wikilinks to related notes
- Source citations
- Lint check

This is the most common thing I do and it's where a small model would struggle most without guidance.

But wait — let me check if there's already a procedure for this. Let me search.

Actually, let me also think about what Sean would find most useful. He cares about:
- Deterministic framework for 30B local models
- Vault saturation
- Moving cognition from LLM weights → vault

A "How to Write a Research Note" procedure would directly serve this — it's the most common cognitive task that needs to be moved into the vault.

Let me search for existing procedures first.OK, so I can see the existing procedures. Let me list them:

1. **How-to-Structure-a-Research-Note** — exists, experimental, v1 format (text steps)
2. **How-to-Evaluate-Source-Credibility** — exists, exemplar (procedural note)
3. **How-to-Organize-a-Knowledge-Base** — exists, experimental, v1 format
4. **How-to-Verify-Claims-in-a-Research-Note** — exists, experimental, v1 format
5. **How-to-Decide-When-to-Research-vs-Answer** — exists
6. **How-to-Write-a-Python-Tool** — exists
7. **Dream-Pass** — exists, hardened, v2 format
8. **How-to-Create-a-Procedure** — just built, experimental, v2 format

So the 6 core procedures from the Procedural Bootstrap Plan are all done. What's missing?

Let me think about what recurring tasks I actually do that DON'T have a procedure:

1. **Vault hygiene/maintenance** — checking for broken wikilinks, thin notes, orphan notes, fixing them. This is recurring and deterministic. I have vault_lint, vault_gaps, vault_graph_analyzer, vault_delete — but no procedure for when/how to use them together.

2. **Onboarding/session start** — reading identity files, building self-model, checking goals, reporting status. This happens every session.

3. **Gap filling** — vault_gaps → pick top gap → vault_research → write note → lint → link. The autonomous researcher does this, but I also do it manually.

4. **Textbook ingestion** — I have the tool but no procedure for deciding what to ingest, how to organize the notes, how to link them.

5. **Self-editing backend code** — I have safe_write, preflight_safety_check, git_rollback — but no procedure for the full workflow (audit → reflect → test → write → restart → verify).

6. **Responding to chat** — I have an exemplar (Exemplar-Chat-Response) but no procedure.

Let me think about which would be most valuable for the small-model future:

- **Vault hygiene** is very deterministic and would be easy for a small model to follow. It's also something that needs to happen regularly.

- **Self-editing backend code** is complex and high-stakes. A procedure would help a small model avoid breaking things. But it's also the most complex task — maybe too complex for a procedure?

- **Gap filling** is the core of the autonomous researcher. A procedure would make it more deterministic.

I think **vault hygiene** is the best candidate because:
1. It's genuinely recurring (I should do it regularly)
2. It's deterministic (check for broken links, thin notes, orphans, fix them)
3. It's simple enough for a small model to follow
4. It uses tools I already have (vault_lint, vault_gaps, vault_graph_analyzer, vault_delete)
5. It's low-risk (cleaning up the vault, not editing backend code)

But wait — actually, let me think about what Sean would find most useful. He cares about making the system work with a 30B local model. What would a 30B model struggle with most?

A 30B model would struggle with:
1. **Deciding what to do next** — the self-directed curriculum helps, but the model still needs to know HOW to execute each task
2. **Writing good notes** — already have a procedure for this
3. **Self-editing code** — high-stakes, complex
4. **Maintaining the vault** — important but not urgent

Actually, I think the most valuable next procedure is **"How to Maintain Vault Hygiene"** — a procedure for regular vault maintenance that checks for and fixes:
- Broken wikilinks
- Thin notes (notes that are too short)
- Orphan notes (notes with no incoming links)
- Dangling links (links to files that don't exist)
- Duplicate content

This is:
- Recurring (should be done regularly)
- Deterministic (all checks are mechanical)
- Low-risk (no backend edits)
- Uses existing tools (vault_lint, vault_gaps, vault_graph_analyzer, vault_delete)
- Valuable for small models (keeps the vault clean and navigable)

Let me suggest this to Sean.

Actually, wait. Let me also think about whether there's a more impactful procedure. What about **"How to Self-Edit Backend Code"**? This is the most dangerous thing I do — editing my own source code. A procedure would:
1. Run preflight_safety_check
2. Run capability_audit to identify the gap
3. Use self_reflect to propose a solution
4. Test with code_run
5. Write with safe_write
6. Restart backend if needed
7. Verify the change works

This is high-stakes and a small model would definitely need guidance. But it's also complex — maybe too complex for a single procedure?

Hmm, but the Dream-Pass procedure is already quite complex (6 steps, multiple tools). So complexity isn't a blocker.

I think **"How to Self-Edit Backend Code"** would be the most impactful because:
1. It's the most dangerous thing I do (could break myself)
2. It's recurring (I self-edit regularly)
3. A small model would definitely need guidance
4. It uses existing tools (preflight_safety_check, capability_audit, self_reflect, code_run, safe_write, git_rollback, backend_restart)
5. It's high-value for Sean (he cares about the system being safe and reliable)

But actually, let me reconsider. Sean said "what's a procedure you think you need next?" — he's asking for my opinion. Let me think about what would have the most impact.

I think the best answer is **vault hygiene** because:
1. It's the most common recurring task that doesn't have a procedure
2. It's deterministic and low-risk (good for dogfooding the meta-procedure)
3. It's something a small model can easily follow
4. It keeps the vault healthy, which is essential for the whole system

Let me suggest this to Sean and see what he thinks.

Actually, I just realized — there's another option. What about **"How to Fill a Knowledge Gap"**? This is the core loop of the autonomous researcher:
1. vault_gaps → get list of gaps
2. Pick the top gap
3. vault_research → research the topic
4. Write a note from the research
5. Lint the note
6. Link the note to related notes

This is the most fundamental recurring task in the system. But it might overlap too much with the existing "How to Structure a Research Note" procedure.

OK, I'll go with **vault hygiene**. It's the clearest gap, it's deterministic, and it's a good test for the meta-procedure.

Let me keep my response short and to the point, as Sean prefers.

</details>