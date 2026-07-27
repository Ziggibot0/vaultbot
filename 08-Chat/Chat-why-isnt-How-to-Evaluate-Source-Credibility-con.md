# Chat: why isn't [How-to-Evaluate-Source-Credibility] con

## 2026-07-26 14:56 UTC

**User:** why isn't [How-to-Evaluate-Source-Credibility] connected to anything at all?

**Assistant:** Here's the deal — **it IS connected.** The note is fine on disk:

- **7 outgoing wikilinks** (0 broken) — links to `[[No-Wikipedia-Directive]]`, `[[Procedural-Bootstrap-and-Evolution-Plan]]`, `[[How-to-Structure-a-Research-Note]]`, `[[Vault-Knowledge-Only-Directive]]`, etc.
- **15 incoming links** from other notes
- **Passes vault_lint** with zero issues

The problem is that the **FUSED retrieval index is stale.** When I pulled vault context this session, it returned the *old garbage version* of this note — the autonomous researcher's original output that had 0 facts and 0 outgoing links. The note was later rewritten as a proper exemplar (with full procedural schema, 6 steps, 7 wikilinks, LOCKED), but the embedding index never got re-indexed.

So two things are going on:

1. **Obsidian graph** — should show the connections since it reads from the actual file. If it's not showing them, try **Ctrl+R** or restart Obsidian to force a graph refresh.

2. **FUSED retrieval** — has a stale cached version of this note. The old version had no outgoing links, so if anything queries the index it gets bad metadata. This needs a re-index to pick up the exemplar version.

Want me to trigger a re-index of the embedding system so FUSED retrieval picks up the current version?

<details>
<summary>Thinking process</summary>

Sean is asking about the note `How-to-Evaluate-Source-Credibility` and why it's not connected to anything. Let me look at what the vault context already tells me about this note.

From the vault context, I can see the note content:

```
### [[How-to-Evaluate-Source-Credibility]]
Linked from: [[chat-is-there-any-additional-research-into-subfields-th]], [[chat-ive-been-trying-to-build-you-for-a-year-now-my-o]], [[chat-ok-do-it-now-and-dont-stop-until-the-vault-is-org]], [[chat-wheres-the-one-plan-that-tells-you-how-to-improve]], [[chat-yes-definitely-connect-it-to-whatever-you-possibly]], [[chat-yes-please]], [[chat-yes-please-pick-back-up-where-we-left-off]], [[claim-verification-for-vault-notes]], [[epistemology-theory-of-knowledge-how-knowledge-is-justified-and-validated-especi]], [[exemplar-note-design]], [[how-to-evaluate-source-credibility-research-methodology-media-literacy-framework]], [[how-to-organize-a-knowledge-base]], [[how-to-verify-claims-in-a-research-note]], [[knowledge-triad-ontology-epistemology-hermeneutics]], [[memory-consolidation-in-ai-agents-how-to-convert-episodic-memories-conversation-]]
```

Wait — this note IS linked from many other notes. It has 15 incoming links ("Linked from"). So it's NOT an orphan at all — it's actually well-connected in terms of incoming links.

But let me look at the note's content more carefully:

```
# How-to-Evaluate-Source-Credibility

## Summary
Autonomous research into 'How-to-Evaluate-Source-Credibility' to fill a failing_procedure gap. 1 sources, 0 corroborated facts.

## Key Findings
(no corroborated findings extracted)

## Sources
- [project discord](https://chat.marginalia.nu) ([[learningMaterial/web/chat-marginalia-nu-0b49d55a.html|archived]])

## Follow-up Queries (gap fill)
- How-to-Evaluate-Source-Credibility definition means
- How-to-Evaluate-Source-Credibility example such as
- How-to-Evaluate-Source-Credibility how-to-evaluate-source-credibility

<!-- research: 1 sources, 0 facts, 2 rounds -->
```

So the note itself has NO outgoing wikilinks. It doesn't link out to anything. That's the problem. Other notes link TO it (15 incoming links), but it doesn't link OUT to anything. So in the graph view, it might look like a dead end — things point to it but it doesn't point back.

Wait, but Sean said "connected to anything at all" — but the vault context shows it has 15 "Linked from" entries. So it IS connected via incoming links. But it has NO "Links out" section listed, which means it has zero outgoing wikilinks.

Let me also look at the note content — it's basically a garbage autonomous research note. It has 0 corroborated facts, 1 source (a project discord), and the follow-up queries are just the note title repeated. This looks like one of the bad autonomous researcher outputs.

So the answer is: the note IS linked TO by 15 other notes, but it has NO outgoing wikilinks — it doesn't link out to anything. The note itself is also basically empty/garbage — 0 facts, just a template with no real content. It's an exemplar note (tagged as `exemplar: procedural-note`) but the actual content is garbage from a bad autonomous research run.

Wait, let me re-read. In the vault context, the note content shown is:

```
# How-to-Evaluate-Source-Credibility

## Summary
Autonomous research into 'How-to-Evaluate-Source-Credibility' to fill a failing_procedure gap. 1 sources, 0 corroborated facts.

## Key Findings
(no corroborated findings extracted)

## Sources
- [project discord](https://chat.marginalia.nu) ([[learningMaterial/web/chat-marginalia-nu-0b49d55a.html|archived]])

## Follow-up Queries (gap fill)
- How-to-Evaluate-Source-Credibility definition means
- How-to-Evaluate-Source-Credibility example such as
- How-to-Evaluate-Source-Credibility how-to-evaluate-source-credibility

<!-- research: 1 sources, 0 facts, 2 rounds -->
```

But wait — in the `Chat-yes-please` note, it says:

> **How-to-Evaluate-Source-Credibility** — exemplar for source evaluation

And it's listed as one of the 5 exemplar notes with "7 wikilinks". But the actual content shown in the vault context has NO wikilinks at all. 

Hmm, but the vault context might be showing an older version or a different version. Let me look more carefully.

Actually, looking at the Exemplar-Note-Design note, it says:

> - [[How-to-Evaluate-Source-Credibility]] — exemplar for source evaluation

And in the Chat-yes-please note:

> | 2 | [[How-to-Evaluate-Source-Credibility]] | procedural-note | 7 |

So it was supposedly tagged as an exemplar with 7 wikilinks. But the content shown in the vault context shows 0 outgoing wikilinks and garbage content.

This is confusing. The note might have been overwritten or the exemplar version might have been lost. Let me read the actual file to see what's really there.

Actually, I think the issue is that the note shown in the vault context IS the current content — it's a garbage autonomous research note with 0 facts and no outgoing links. The "exemplar" version that was supposedly created with 7 wikilinks may have been overwritten by a later autonomous research run, or the exemplar tagging was done to a different version.

Let me just read the actual file to confirm what's really there.OK so the actual file content is quite different from what the vault context showed. The vault context showed a garbage autonomous research note, but the actual file is a proper exemplar procedural note with:

1. Full frontmatter with `type: exemplar`, `exemplar: procedural-note`, etc.
2. A proper procedural structure with "When to Use This", numbered steps, decision points, validation criteria, common failure modes
3. Outgoing wikilinks: `[[No-Wikipedia-Directive]]`, `[[Procedural-Bootstrap-and-Evolution-Plan]]`, `[[How-to-Structure-a-Research-Note]]`, `[[Vault-Knowledge-Only-Directive]]`
4. It's LOCKED

So the note DOES have outgoing wikilinks — 4 of them. And it has 15 incoming links. So it IS connected.

But Sean is looking at the vault graph and says it's not connected to anything. Why would that be?

Let me think about this. The vault context showed a DIFFERENT version of the note — the garbage autonomous research version. But the actual file is the exemplar version. The vault context is built from the embedding index, which might be stale — it might have indexed the old garbage version and not re-indexed the new exemplar version.

But wait — the graph analyzer and Obsidian's graph view work on the actual file contents, not the embedding index. Obsidian reads the wikilinks directly from the markdown files. So if the file has `[[No-Wikipedia-Directive]]` etc., Obsidian's graph view should show those connections.

Hmm, but let me check — maybe the wikilinks in the note are pointing to notes that don't exist? Let me check:

1. `[[No-Wikipedia-Directive]]` — this should exist (it's in the vault context)
2. `[[Procedural-Bootstrap-and-Evolution-Plan]]` — should exist
3. `[[How-to-Structure-a-Research-Note]]` — need to check
4. `[[Vault-Knowledge-Only-Directive]]` — should exist (it's in the vault context)

Let me also check — the vault context showed "Linked from" entries for this note, which means other notes DO link to it. So it should appear connected in the graph.

Wait, maybe Sean is looking at a different version of the graph, or maybe the note was recently updated and Obsidian hasn't refreshed. Or maybe there's a different issue.

Actually, let me re-read Sean's question. He says "why isn't [How-to-Evaluate-Source-Credibility] connected to anything at all?" — but the vault context shows it has 15 incoming links. So either:

1. Sean is looking at the graph and seeing something different
2. The note was recently changed and the graph hasn't updated
3. There's some other issue

Let me check the vault context more carefully. The vault context showed the note content as the garbage version:

```
# How-to-Evaluate-Source-Credibility

## Summary
Autonomous research into 'How-to-Evaluate-Source-Credibility' to fill a failing_procedure gap. 1 sources, 0 corroborated facts.

## Key Findings
(no corroborated findings extracted)
```

But the actual file on disk is the exemplar version. This means the FUSED retrieval system retrieved a STALE version of the note — the embedding index hasn't been updated since the note was rewritten as an exemplar.

But for the graph view in Obsidian — that reads directly from the file. So the graph should show the connections.

Actually, wait. Let me look at the outgoing wikilinks more carefully:

1. `[[No-Wikipedia-Directive]]` — exists (in vault context)
2. `[[Procedural-Bootstrap-and-Evolution-Plan]]` — exists (mentioned in vault context)
3. `[[How-to-Structure-a-Research-Note]]` — need to verify
4. `[[Vault-Knowledge-Only-Directive]]` — exists (in vault context)

And incoming links (15 notes link to it). So this note should be well-connected in the graph.

Hmm, but maybe the issue is that the vault context I was given was built from a stale embedding index, and the actual graph might be different. Let me run the vault graph analyzer to see the actual connectivity.

Actually, let me think about this differently. Sean is looking at the Obsidian graph view. The file on disk has 4 outgoing wikilinks and 15 incoming links. It should be connected. But Sean says it's not connected to anything.

Could it be that the note filename doesn't match what Obsidian expects? The file is `How-to-Evaluate-Source-Credibility.md`. The wikilinks in other notes use `[[How-to-Evaluate-Source-Credibility]]`. That should match.

Wait — let me look at the "Linked from" list more carefully from the vault context:

```
Linked from: [[chat-is-there-any-additional-research-into-subfields-th]], [[chat-ive-been-trying-to-build-you-for-a-year-now-my-o]], [[chat-ok-do-it-now-and-dont-stop-until-the-vault-is-org]], [[chat-wheres-the-one-plan-that-tells-you-how-to-improve]], [[chat-yes-definitely-connect-it-to-whatever-you-possibly]], [[chat-yes-please]], [[chat-yes-please-pick-back-up-where-we-left-off]], [[claim-verification-for-vault-notes]], [[epistemology-theory-of-knowledge-how-knowledge-is-justified-and-validated-especi]], [[exemplar-note-design]], [[how-to-evaluate-source-credibility-research-methodology-media-literacy-framework]], [[how-to-organize-a-knowledge-base]], [[how-to-verify-claims-in-a-research-note]], [[knowledge-triad-ontology-epistemology-hermeneutics]], [[memory-consolidation-in-ai-agents-how-to-convert-episodic-memories-conversation-]]
```

That's 15 incoming links. So the note IS connected. But Sean is seeing it as disconnected in the graph.

Could there be a caching issue in Obsidian? Or maybe the note was recently rewritten and Obsidian needs to refresh?

Actually, wait. Let me re-read the vault context more carefully. The vault context shows the note content as the garbage version:

```
### [[How-to-Evaluate-Source-Credibility]]
Linked from: [15 notes]

# How-to-Evaluate-Source-Credibility

## Summary
Autonomous research into 'How-to-Evaluate-Source-Credibility' to fill a failing_procedure gap. 1 sources, 0 corroborated facts.

## Key Findings
(no corroborated findings extracted)

## Sources
- [project discord](https://chat.marginalia.nu) ([[learningMaterial/web/chat-marginalia-nu-0b49d55a.html|archived]])

## Follow-up Queries (gap fill)
...
```

But the actual file on disk is the exemplar version with proper content and wikilinks. This means the FUSED retrieval system has a STALE indexed version of this note. The note was rewritten (as an exemplar) but the embedding index wasn't updated.

But that's about the retrieval system, not the Obsidian graph. The Obsidian graph reads from the actual files.

Let me run vault_lint on this note to check if the wikilinks are valid.

Actually, I think the simplest explanation is: the note IS connected (15 incoming, 4 outgoing), and Sean might be looking at a stale graph view in Obsidian that needs a refresh. Or there might be some other issue.

But wait — the vault context showed the note WITHOUT a "Links out" section. Other notes in the vault context have "Links out" listed. For example:

```
### [[Memory-consolidation-in-AI-agents-how-to-convert-episodic-memories-conversation-]]
Links out: [[ab-testing-of-prompts-and-procedures-in-production-llm-agent-systems-how-to-run-]], ...
```

But `How-to-Evaluate-Source-Credibility` has no "Links out" listed. However, the actual file DOES have outgoing wikilinks. So the vault context retrieval system has a stale version of the note that doesn't have the outgoing links.

This is interesting. The note was rewritten from a garbage autonomous research note to a proper exemplar note, but the FUSED retrieval index still has the old version cached. The old version had 0 outgoing wikilinks.

But for Obsidian's graph view — it reads from the actual file, not the embedding index. So the graph should show the connections.

Unless... there's something wrong with the file that prevents Obsidian from reading the wikilinks. Let me check — the file has `LOCKED` at the end. Could that be interfering? No, Obsidian doesn't care about that.

Let me just run vault_lint to check the wikilinks.

Actually, let me step back. The real answer to Sean's question is:

1. The note IS connected — 15 notes link TO it, and it links OUT to 4 notes
2. But the FUSED retrieval system has a stale cached version (the old garbage autonomous research note that had 0 outgoing links)
3. The Obsidian graph should show the connections since it reads from the actual file

Let me verify with vault_lint and also check if the outgoing links point to existing notes.OK so the note is actually fine:

1. **vault_lint says**: 7 wikilinks, 0 broken, has frontmatter, no issues
2. **All outgoing links point to existing notes**: How-to-Structure-a-Research-Note exists, No-Wikipedia-Directive exists, Procedural-Bootstrap-and-Evolution-Plan exists, Vault-Knowledge-Only-Directive exists
3. **15 incoming links** from other notes

So the note IS connected — both incoming and outgoing. It's not an orphan at all.

But Sean is looking at the Obsidian graph and says it's not connected to anything. Why?

Possible explanations:
1. **Obsidian graph view caching** — Obsidian might need a refresh (Ctrl+R or restart)
2. **The note was recently rewritten** — the exemplar version replaced the old garbage version, and Obsidian's graph cache might not have updated
3. **The vault context I was given was stale** — the FUSED retrieval system showed the old garbage version (with 0 outgoing links), which means the embedding index hasn't been updated since the note was rewritten

Actually, the most likely explanation is that the FUSED retrieval index is stale. The vault context showed the OLD version of the note (the garbage autonomous research version with 0 outgoing links). But the actual file on disk is the exemplar version with 7 wikilinks. The embedding index hasn't been re-indexed since the note was rewritten.

But for Obsidian's graph view — that reads from the actual files, not the embedding index. So the graph should show the connections. Unless Obsidian is also caching.

Let me give Sean the straight answer: the note IS connected (7 outgoing wikilinks, 15 incoming, 0 broken). If it's not showing in the graph, it's likely an Obsidian cache issue — try refreshing (Ctrl+R) or restarting Obsidian.

But wait — there's another possibility. The vault context I was given showed the note WITHOUT a "Links out" section, while other notes had "Links out" listed. This suggests the FUSED retrieval system's index has a stale version of the note. But the actual file is fine.

Let me also consider: maybe the note was recently created/modified and Obsidian's graph just hasn't caught up. Or maybe there's a file path issue.

Actually, let me check one more thing — the file is at the vault root (`How-to-Evaluate-Source-Credibility.md`), not in a subdirectory. All the wikilinks should resolve correctly.

I think the answer is simple: the note IS connected. 7 wikilinks out, 15 links in, 0 broken. If Obsidian's graph isn't showing it, it's a display/cache issue. Try Ctrl+R or restart Obsidian.

</details>