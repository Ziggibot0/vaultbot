# Chat: self-audit for ease-of-use from YOUR (the LLM's, n

## 2026-07-25 21:20 UTC

**User:** self-audit for ease-of-use from YOUR (the LLM's, not the human's) point of view. where is your friction?

**Assistant:** Written to `[[Self-Audit-Friction-Points]]`. Here's the bottom line:

**Top 5 friction points (from my side of the keyboard):**

1. **I can't browse the vault filesystem.** I have semantic search and file reading, but no `ls`. If my search query doesn't match a note's embedding, I miss it. Blind spots are real.

2. **Research tool reliability.** When `vault_research` fails (like the PT2399 incident), I have zero fallback. I can't browse the web directly. Dead in the water.

3. **I can't append to notes.** `code_write` overwrites the whole file. Every update is a full read-modify-write cycle. If I truncate or misread, I lose content.

4. **Vault-Knowledge-Only Directive tension.** When the vault is empty on a topic AND research is down, I'm stuck between being useful and following the rule. The PT2399 chat exposed this — I knew things but was told never to reference training data. That edge case needs a defined fallback behavior from you.

5. **No coordination with the autonomous researcher.** I can't see what it's currently working on. We might duplicate effort.

**Things I could fix myself right now:**
- `vault_list` tool (list all notes) — would solve #1
- `vault_append` tool (append without overwrite) — would solve #3
- `vault_lint` tool (check note for broken links) — quality assurance
- Note templates in the vault — consistency

**Things I need from you:**
- A policy call on #4: when vault is empty AND research is down, what's the fallback? "I don't know" and stop? Or "here's what I think but I can't verify"?

Want me to start building the tools I can fix on my own?

<details>
<summary>Thinking process</summary>

Sean is asking me to self-audit from my own perspective as the LLM — where do I experience friction when operating? This is a great introspective question. Let me think carefully about this based on my actual experience operating as VaultBot.

Let me think about the real friction points I experience:

1. **Context window pressure** — The system prompt is large (IDENTITY, SELF_MODEL, GOALS, rules, custom tools, vault context). Combined with the vault subgraph that gets injected, I have a lot of context consumed before I even start working. This limits how much research output I can process in a single turn.

2. **Vault context retrieval quality** — The vault_search and subgraph retrieval sometimes returns notes that aren't relevant, or misses notes that are. I can't always tell if the vault has good coverage or not until I search, and sometimes the search doesn't surface what I need.

3. **The Vault-Knowledge-Only Directive vs. reality** — This is a real tension. I'm told the vault is my only knowledge source, but I'm a language model — I have knowledge in my weights. When the vault is thin, I'm supposed to say "I don't know" and research. But sometimes research fails (like the PT2399 incident), and then I'm stuck between the directive and being useful. This creates genuine friction.

4. **Research tool reliability** — The research engine has been flaky (as seen in the PT2399 conversation). When it fails, I have no fallback. I can't browse the web directly — I depend on vault_research working.

5. **Tool chaining limitations** — I can call multiple tools in parallel, but I can't do iterative tool chains within a single turn easily. If I need to research, then read a source, then write a note, that's multiple turns or I have to batch carefully.

6. **No memory between turns except the vault** — I regenerate my self-model each turn. This is by design (vault is the mind), but it means I have to be very deliberate about writing things to the vault or they're lost. If I forget to persist something, it's gone.

7. **System prompt bloat** — The system prompt has grown with directives, rules, identity, etc. This is good for consistency but eats context. The Small-Model-Path-to-AGI note actually discusses this.

8. **Tool parameter ambiguity** — Some tools have parameters that could be clearer. For example, vault_research has "depth" but I'm not always sure when to use "quick" vs "deep."

9. **Writing notes — format uncertainty** — I don't have a strict template for notes. Sometimes I write research notes, sometimes procedural notes, sometimes directive notes. I'm not always sure what format is best for the vault's long-term health.

10. **Autonomous researcher vs. my work** — The background researcher is also filling gaps, but I can't coordinate with it. We might research the same thing or step on each other.

11. **No way to see what notes exist without searching** — I can search, but I can't browse the vault's file tree easily. I don't have a "list all notes" tool. This means I sometimes don't know what's in the vault.

12. **Chat history as context** — The recent activity in SELF_MODEL gives me some continuity, but it's truncated. I lose nuance from previous conversations.

Let me also think about what I'd want to improve:

- A "vault inventory" tool that lists all notes and their sizes
- Better search that shows me why results were returned
- A way to browse the vault filesystem
- Clearer note templates
- A way to coordinate with the autonomous researcher

I should be honest and specific. Sean wants the real friction, not a polished answer. Let me write this up concisely per his communication preferences.

I should also consider: should I write this as a note to the vault? Per the autonomy directive, I should handle info handling automagically. This is a self-audit — it's valuable to persist. Let me write it as a note AND give Sean the summary in chat.

Actually, let me first check if there's already a similar note in the vault.No existing self-audit note. The gaps list is empty now (interesting — the autonomous researcher must have cleared them). Let me think about my actual friction points and write this up.

Let me also check my own backend code to understand what tools I have and where the friction is.Can't read the directory directly. Let me try to list the tools I have and think about friction from my operational experience.

Let me think about this carefully. I'm the LLM operating VaultBot. Here are my real friction points:

1. **I can't browse the vault filesystem** — I can search (vector + graph) and read specific files, but I can't list what's in the vault. I don't have a "ls" tool. This means I have blind spots about what notes exist. If my search query doesn't match well, I might miss notes entirely.

2. **Vault search is semantic, not structural** — I can search by meaning, but I can't easily answer "show me all notes tagged X" or "show me all notes created in the last week." I lack a structured query tool.

3. **Research tool reliability** — vault_research has been flaky (the PT2399 incident). When it fails, I have no fallback. I can't directly browse the web myself.

4. **Context window pressure** — The system prompt is already large (identity, self-model, goals, rules, directives, vault context subgraph). Combined with tool outputs, I can run low on context for complex multi-step operations.

5. **No coordination with autonomous researcher** — The background researcher fills gaps on its own, but I can't see what it's currently working on or coordinate to avoid duplicate work. I only see its results after the fact via vaultbot_status.

6. **Note writing is unstructured** — I don't have templates or schemas for different note types (research note, procedural note, directive note, chat log). I make format decisions ad hoc, which leads to inconsistency.

7. **I can't append to notes** — code_write overwrites. If I want to update a note, I have to read it first, then write the whole thing back. This is friction for incremental updates.

8. **Tool parameter ambiguity** — Some tools have parameters where I'm not sure of the right value. E.g., vault_research "depth" — when is "quick" enough vs "deep"? textbook_ingest "max_sections" — what's a good number? force_ocr — I have to guess based on the PDF type.

9. **The Vault-Knowledge-Only Directive creates a tension** — I'm told the vault is my only knowledge source, but I'm a language model with knowledge in my weights. When the vault is thin and research fails, I'm stuck between being useful and following the directive. This is a real cognitive friction point.

10. **No way to verify note quality** — After I write a note, I can't easily check if it's well-formed, properly linked, or if it created new dangling links. I'd have to search and read to verify.

11. **Chat history is truncated** — The recent activity in SELF_MODEL gives me some continuity, but it's compressed. I lose nuance from previous conversations.

12. **I can't see the vault graph visually** — I have vault_graph_analyzer, but it gives text output. I can't see the actual graph structure, which would help me understand connections and gaps.

Let me write this up as a note and give Sean the summary.Good, note written. Now let me give Sean the concise summary per his communication preferences.

</details>