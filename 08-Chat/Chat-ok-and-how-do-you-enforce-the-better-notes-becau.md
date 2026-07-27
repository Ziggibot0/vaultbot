# Chat: ok and how do you enforce the "better notes" becau

## 2026-07-25 22:19 UTC

**User:** ok and how do you enforce the "better notes" because i will not trust any promise from an LLM with ephemeral memory

**Assistant:** You're right to not trust that. A promise from an LLM with no persistent memory is worth exactly nothing. Here's how to actually enforce it:

**1. System prompt rule.** The rules section of my system prompt is injected every session — it's the one thing that persists across model swaps. Add a rule like:

> *When writing notes, write self-contained arguments — claim, reasoning, connections in prose. Never write bare facts. Wikilinks cite related notes; the prose around them explains the relationship.*

This isn't a promise. It's an instruction that's re-injected every single turn. The LLM doesn't "remember" to do it — it's told to do it, every time.

**2. Mechanical quality gate.** I already have `vault_lint` — it checks for broken wikilinks and frontmatter. Extend it to also check:
- Note length (too short = probably just a fact dump)
- Contains at least one wikilink (connections to related notes)
- Contains reasoning language ("because", "therefore", "which means", "contradicts", "implies")

If a note fails the check, it gets flagged. This is a ~20-line extension to an existing tool, not a new subsystem.

**3. The two together.** The system prompt rule is the primary enforcement — it works because it's persistent. The lint check is the backup — it catches when the LLM ignores the rule. Neither requires ongoing maintenance.

Want me to add the rule to the system prompt and extend `vault_lint` now? The system prompt lives in the vault — I can show you exactly what I'm changing before I change it.

<details>
<summary>Thinking process</summary>

Sean is making a critical point: I can promise to write "better notes" but since my memory is ephemeral (I regenerate my self-model each turn from the vault), there's no guarantee I'll actually follow through on that promise. The system prompt is injected each session, but if the instruction to "write self-contained argument notes" isn't baked into the system prompt or enforced by code, I might not do it consistently.

So the question is: how do we *enforce* this behavior mechanically, not just promise it?

Options:

1. **System prompt injection** — Add the principle to the system prompt (IDENTITY.md or similar) so it's injected every session. But the system prompt is already long, and Sean might not want to manage that.

2. **Code enforcement in the note-writing pipeline** — When VaultBot writes a note, the backend could check the note quality before accepting it. E.g., check that the note has a minimum length, contains wikilinks, includes reasoning text. This is a lint check on note creation.

3. **Post-write lint** — After writing a note, run `vault_lint` or a custom quality check that verifies the note is a self-contained argument. If it fails, rewrite it.

4. **Template enforcement** — Provide a note template that I must fill in, with sections for claim, reasoning, connections. The template guides the writing.

5. **A-MEM layer enforcement** — The existing A-MEM layer already evolves notes after creation. Could it also enforce quality standards?

6. **Prompt-level enforcement** — The system prompt already says "Prefer vault knowledge first; research only when the vault is insufficient." We could add a rule like "When writing notes, always include the reasoning and implications in prose, not just raw facts."

Actually, let me think about what's most practical and least maintenance:

The most reliable approach is a combination:

1. **System prompt rule** — Add a rule to the system prompt that says "When writing notes, write self-contained arguments with reasoning in prose. Don't write bare facts." This is injected every session, so it's persistent across model swaps.

2. **Post-write quality check** — A tool that checks note quality after writing. This could be a new custom tool or an extension of `vault_lint`. It checks:
   - Note length (too short = probably just a fact, not an argument)
   - Contains at least one wikilink (connections to related notes)
   - Contains reasoning language ("because", "therefore", "which means", "this implies")
   - Has frontmatter

3. **The vault_lint tool already exists** — I could extend it to check for "argument quality" in addition to broken wikilinks.

But Sean's point is deeper: he doesn't trust *promises* from an LLM. He wants *mechanical enforcement*. So the system prompt rule is necessary but not sufficient — it's still just a prompt, which the LLM might ignore.

The most reliable enforcement is code that checks the note *after* it's written and rejects/flags it if it doesn't meet the standard. This is a quality gate.

Let me think about what that quality gate would look like:

```python
def check_note_quality(note_path):
    content = read(note_path)
    checks = {
        "has_frontmatter": bool,
        "min_length": len(content) > 200,  # too short = probably just a fact
        "has_wikilinks": "[[" in content,
        "has_reasoning": any(word in content.lower() for word in 
            ["because", "therefore", "which means", "this implies", 
             "contradicts", "supports", "caused by", "as a result"]),
        "not_just_bullets": content.count("- ") < len(content) / 100,  # not just a list
    }
    return checks
```

But this is heuristic and could be gamed or produce false positives. A 2000-word note that's just a dump of facts would pass the length check but fail the "argument" test.

A more robust approach: use the LLM itself to check note quality. But that's an LLM call, which adds cost and latency.

Actually, the simplest and most reliable approach is:

1. **System prompt rule** (necessary, not sufficient)
2. **A note-writing template** that I fill in, with required sections (claim, reasoning, connections)
3. **A post-write lint check** that verifies the template was followed

The template is the key. If the template has sections like:

```markdown
## Claim
[one-sentence claim]

## Reasoning
[why this is true, what evidence supports it, what it implies]

## Connections
- [[Related Note]] — [how it connects]
```

Then the lint check just verifies these sections exist. That's mechanical, not heuristic.

But wait — Sean said he doesn't want a lot of structure. The whole point of the "prose" approach was to avoid special syntax. A template with required sections is structure.

Hmm. Let me think about this differently. Sean's concern is enforcement, not structure. The question is: how do you make sure the LLM writes good notes when the LLM has no persistent memory?

The answer is: **the system prompt is the enforcement.** It's the only thing that persists across sessions. If the system prompt says "write self-contained argument notes with reasoning in prose," and the system prompt is injected every session, then the LLM will do it every time.

But Sean said he won't trust a promise from an LLM. The system prompt is a promise. So we need something more.

The "something more" is a **quality gate in the note-writing pipeline.** When VaultBot writes a note, the backend checks it before accepting it. If the note is too short, has no wikilinks, or doesn't contain reasoning language, it gets flagged.

But this quality gate is code, and it needs to be maintained. Which is... maintenance. The very thing Sean wants to minimize.

OK, I think the honest answer is:

1. **System prompt rule** — this is the primary enforcement. It's persistent, injected every session, and works across model swaps. It's not a "promise" — it's an instruction that the LLM follows because it's in the system prompt.

2. **A simple post-write check** — extend `vault_lint` to check for basic argument quality (length, wikilinks, reasoning language). This is a lightweight mechanical check, not a complex subsystem.

3. **The existing vault_lint tool** already checks for broken wikilinks and frontmatter. Adding a "note quality" check is a small extension, not a new subsystem.

The key insight: the system prompt IS the enforcement mechanism. It's not a "promise" — it's a persistent instruction that's injected every session. The LLM doesn't "remember" to write good notes; the system prompt tells it to every time.

But to make it more than just a prompt, we can add a mechanical check. Let me think about what's simplest...

Actually, the simplest enforcement is: **add it to the system prompt rules.** The rules section already has rules like "Prefer vault knowledge first" and "Cite sources by name." Adding "Write self-contained argument notes with reasoning in prose" is the same kind of rule. It's enforced by being in the system prompt, which is the only persistent memory.

And for mechanical backup: extend `vault_lint` to flag notes that are too short or lack wikilinks. This is a 10-line code change, not a new subsystem.

Let me also think about: what if I just add a rule to the system prompt and show Sean what it looks like? That's the most direct answer to his question.

Actually, I think the right answer is a combination:

1. **System prompt rule** (the primary enforcement — persistent across sessions)
2. **Post-write lint** (mechanical backup — catches when the LLM ignores the rule)
3. **The vault_lint tool already exists** — just needs a small extension

Let me propose this to Sean concisely.

</details>