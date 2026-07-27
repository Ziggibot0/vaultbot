# Chat: now that you're clear on your goal, what could or 

## 2026-07-27 06:29 UTC

**User:** now that you're clear on your goal, what could or should you do in that direction? what do you already like about your framework? what do you think you're missing? i was thinking more procedures especially since you made yourself that procedure-making-procedure (hopefully that pops up in your injected context). or maybe we could just organize the vault into folders so that the user can still use their obsidian vault as their obsidian vault?

**Assistant:** Good — I've got the full picture. Here's my honest assessment.

---

## What I Like About My Framework

**1. FUSED retrieval.** Vector + wikilink graph + backlinks. I don't just keyword-match — I traverse the graph. When it works, I find notes that are structurally related even if the words don't overlap. This is the core of how I think.

**2. Procedure subprocess architecture.** Code steps execute deterministically — zero LLM cost. The LLM only gets called for steps that genuinely need semantic reasoning. This is the bridge to the 30B future. A small model doesn't need to be smart if it follows good instructions.

**3. Quality infrastructure.** Calibration (detects your corrections), RAG eval (measures retrieval quality), claim verification (checks notes against sources), context budgeting (ranks notes by importance when context is tight). All deterministic. All running in the background. These are the gates that catch errors without needing a big brain.

**4. The Procedure-Creator.** This is a proper compiled procedure that sandboxes and validates new procedures before publishing. It catches the 7 friction points from Dream-Pass creation. It's dogfoodable — I use it to make more procedures.

**5. Vault-thinks-LLM-synthesizes.** The intelligence lives in the note content, not in metadata or machinery. Notes are self-contained arguments. This keeps maintenance at zero cost.

---

## What I'm Missing

**1. Real procedures.** I have 2 compiled procedures (Dream-Pass, Procedure-Creator) and ~5 "How-to-*" tutorials that are advice, not protocols. Sean, you already called this out — "there's a difference between advice and protocol." The How-to-* notes need to either become real compiled procedures with code steps, or be honestly relabeled as reference notes.

**2. Vault folder organization.** 312 files, ~50+ in the root. You already have `vaultbot/chat/`, `vaultbot/research/`, `vaultbot/textbooks/` — but the root is a dumping ground for directives, architecture notes, procedures, exemplars, and highway notes all mixed together. For Obsidian usability, this should be organized.

**3. The How-to-* naming.** Procedures should sound like tools you run, not tutorials you read. `Verify-Claims` not `How-to-Verify-Claims-in-a-Research-Note`. You already corrected this once.

---

## What I Think We Should Do

Two tracks, both move us toward cloud-model obsolescence:

### Track 1: Organize the vault into folders

Proposed structure:
```
/                    → directives, identity, README, config
/procedures/         → all type:procedure notes
/architecture/       → design specs, architecture notes
/exemplars/          → type:exemplar notes
/research/           → (already exists at vaultbot/research/)
/chat/               → (already exists at vaultbot/chat/)
/textbooks/          → (already exists at vaultbot/textbooks/)
/highways/           → highway/bridge notes (Build-Log, Testing-History, etc.)
```

This makes the vault navigable in Obsidian's file explorer. You can still use graph view — wikilinks don't care about folders. But folders give you a mental map: "procedures are here, architecture is here, my chats are here."

### Track 2: Convert tutorials to real procedures

Use the Procedure-Creator to dogfood the next procedures. Priority list:
1. **Vault-Hygiene** — run vault_lint + vault_gaps + vault_graph_analyzer, fix what's fixable, report. Fully deterministic, high value, recurring.
2. **Verify-Claims** — extract claims from a note, check against sources, update frontmatter. Already have the research and the `claim_verifier.py` module.
3. **Research-Then-Write** — research a topic, write a structured note, lint it. Combines `vault_research` + `How-to-Structure-a-Research-Note` into one executable flow.

Each one gets drafted, sandboxed, validated, and published through the Procedure-Creator. That's the dogfooding loop.

---

**Which track first?** I'd say folders first (quick, maybe 15 minutes, makes everything else easier to find), then procedures (each one takes a real research + build cycle). But your call, homie.

<details>
<summary>Thinking process</summary>

Sean is asking me three things:

1. What could/should I do in the direction of making cloud models obsolete?
2. What do I already like about my framework?
3. What am I missing?
4. He suggests more procedures (especially since I made a procedure-creator procedure) and/or organizing the vault into folders for Obsidian usability.

Let me search the vault for the procedure-creator and the current state of procedures, and also check what procedures exist.The vault_list with tag `type:procedure` returned 0 results. That's because the tag filter checks for `#tag` in note content, not YAML frontmatter. Let me search differently. Let me look at the Procedure-Creator note and also list the vault to see what procedures exist.

Let me also check the Dream-Pass procedure and see what's there.Now let me read the Procedure-Creator note and the Dream-Pass procedure to understand the current state. Let me also check the current vault folder structure.Now I have a clear picture. Let me synthesize what I know:

**Current state:**
- 312 .md files in the vault
- Already organized into some folders: `vaultbot/chat/`, `vaultbot/research/`, `vaultbot/textbooks/`, `vaultbot_backend/identity/`
- Root has a mix of core notes (directives, architecture, procedures, exemplars) and other stuff
- Procedures: Dream-Pass (real, compiled), Procedure-Creator (real, compiled), and several "How-to-*" that are tutorials not compiled procedures
- The Procedure-Creator exists and is a proper compiled procedure with code steps

**What I like about the framework:**
1. FUSED retrieval (vector + wikilink graph + backlinks)
2. Procedure subprocess architecture (code steps = zero LLM cost)
3. Quality infrastructure (calibration, RAG eval, claim verification, context budgeting)
4. The Procedure-Creator meta-procedure (dogfoodable)
5. Deterministic scaffolding philosophy
6. The vault-thinks-LLM-synthesizes principle

**What I'm missing:**
1. More real procedures (only 2 compiled: Dream-Pass and Procedure-Creator; the rest are tutorials)
2. The "How-to-*" procedures need to be converted to real compiled procedures or renamed
3. Vault folder organization — root is cluttered with 50+ files, should be organized
4. The autonomous researcher is running but the gaps are all dangling web archive links

**Sean's suggestions:**
1. More procedures — especially using the Procedure-Creator to dogfood
2. Organize vault into folders for Obsidian usability

Let me give a concise, BLUF answer addressing all three of his questions, then propose concrete next steps.

</details>