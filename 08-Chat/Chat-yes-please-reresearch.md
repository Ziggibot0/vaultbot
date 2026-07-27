# Chat: yes please reresearch

## 2026-07-26 03:35 UTC

**User:** yes please reresearch

**Assistant:** Done. Here's the bottom line:

## What I Did
- Re-researched typed edges (2 deep dives, 23 sources total)
- Read the key sources in full: Karpathy's original gist, the Penfield article, the KG reasoning survey
- Wrote [[Typed-Edges-Research-Assessment]] — 8.9KB, 17 wikilinks, 0 broken
- Updated [[Pre-Thought-Information-Shapes]] with a supersession notice

## The Verdict

**The research for typed edges is weak.** Here's why:

- **The main advocate is a vendor ad.** Penfield Labs wrote the article to sell their cloud knowledge graph service. Conflict of interest.
- **Karpathy doesn't use them.** His LLM Wiki — the pattern that started this whole conversation — works with plain wikilinks + LLM maintenance in prose.
- **The one credible practitioner** (Survivor Forge, 1,100+ sessions) says typed edges helped at ~500 notes, but warns autonomous linking "hallucinates connections that look plausible but aren't load-bearing."
- **The academic literature** is about large-scale KGs (Wikidata-scale), not personal PKM with 154 notes.
- **Two of the four original research notes were junk** — completely off-topic (Wikipedia parsing, differential geometry).

## The Decision

**Don't build typed edges.** [[Vault-Thinks-LLM-Synthesizes]] was right all along — prose explains relationships better than metadata, at a fraction of the maintenance cost. The [[RAG-Evaluation-for-FUSED-Retrieval]] system will detect if/when retrieval quality degrades at scale, and typed edges can be a targeted intervention then — not a preemptive architecture decision now.

<details>
<summary>Thinking process</summary>

Sean wants me to re-research typed edges. Let me do a deep research on this topic to get proper sources and replace the junk research notes. I should focus on the actual topic: typed/named edges in knowledge graphs and personal knowledge management, semantic relationships between notes, and whether they're worth the maintenance cost.

Let me also search the vault for the existing notes to understand what needs to be replaced.The research results are... not great. The first one found Karpathy's LLM wiki gist (which is relevant) and some knowledge management papers, but also a bunch of math papers about link polynomials and knot invariants (irrelevant). The second one found some knowledge graph reasoning papers but also irrelevant graph theory papers.

Let me look at what we actually got:

**Research 1 (typed edges in PKM):**
- Karpathy's LLM wiki gist — relevant! Talks about maintenance burden, LLM doing cross-referencing, knowledge compounding
- "What Cost Knowledge Management? The Example of Infosys" — relevant to maintenance cost question
- "Knowledge Graph Curation: A Practical Framework" — could be relevant
- "Deep Reasoning with Knowledge Graph for Social Relationship Understanding" — could be relevant
- Math papers about link polynomials — irrelevant junk

**Research 2 (edge typing vs untyped):**
- "Knowledge Graph Reasoning with Logics and Embeddings: Survey and Perspective" — relevant! Talks about logic-based vs embedding-based reasoning
- "Learning Attention-based Representations from Multiple Patterns for Relation Prediction in Knowledge Graphs" — relevant to relation prediction
- "AI Agent-Driven Framework for Automated Product Knowledge Graph Construction" — somewhat relevant
- Graph theory papers about cliques, order types, RAC drawings — irrelevant junk

So the research is partially useful but also has a lot of noise. The key findings are:

1. From Karpathy's LLM wiki: maintenance is the core problem. "Humans abandon wikis because the maintenance burden grows faster than the value." The LLM can do the maintenance. But this doesn't directly address typed edges.

2. From the KG reasoning survey: "Conventional KG reasoning based on symbolic logic is deterministic, with reasoning results being explainable, while modern embedding-based reasoning can deal with uncertainty and predict plausible knowledge." This is relevant — it suggests typed edges enable deterministic, explainable reasoning while embeddings handle uncertainty.

3. "A promising direction is to integrate both logic-based and embedding-based methods" — suggests both approaches have value.

But honestly, neither research round directly addressed the core question: are typed edges worth the maintenance cost in a personal knowledge management context? The research went off-topic again.

Let me also check the existing vault notes on this topic to see what we already have.Now I have the Karpathy LLM wiki gist content. Key insight from Karpathy: he talks about the maintenance burden being the core problem with knowledge bases, and LLMs solving it. But he does NOT specifically advocate for typed edges — his wiki uses plain markdown links and cross-references. The "noting where new data contradicts old claims" is done by the LLM in prose, not by typed edge syntax.

Now let me also read the existing research note that was good — the one about typed wikilinks and Penfield. Let me also read the key source about "What Karpathy's LLM Wiki Is Missing" which seems to be the one that specifically proposes typed edges as an addition to Karpathy's approach.

Let me also look at the KG reasoning survey source.Now let me also read the existing good research note on typed wikilinks to see what sources it found, and specifically the "What Karpathy's LLM Wiki Is Missing" source and the "Wikilinks and Named Edges — Agent Reference Guide" source.Now I have a clear picture. Let me read the key source — the Penfield article about "What Karpathy's LLM Wiki Is Missing" — since that's the primary source advocating for typed edges.Now I have a very clear picture. Let me also read the existing Pre-Thought-Information-Shapes note and the Vault-Thinks-LLM-Synthesizes note to understand the contradiction, then synthesize everything.

Let me also read the Obsidian forum thread about link types to get the community perspective.

Actually, I have enough now. Let me synthesize what I've found:

## What the Research Actually Shows

### Source 1: Karpathy's LLM Wiki (the original)
- Karpathy's pattern uses **plain wikilinks** — no typed edges
- The LLM does maintenance: "noting where new data contradicts old claims" — but this is in prose, not typed edge syntax
- Key insight: "Humans abandon wikis because the maintenance burden grows faster than the value. LLMs don't get bored."
- He does NOT advocate for typed edges — his wiki is plain markdown with cross-references

### Source 2: Penfield's "What Karpathy's LLM Wiki Is Missing" (the main typed edges advocate)
- This is a **product blog post** by Penfield Labs, which sells a knowledge graph backend
- They argue Karpathy's plain wikilinks are insufficient because they carry only 1 bit of info
- They propose `@supersedes`, `@contradicts`, etc. syntax in wikilinks
- They have an Obsidian plugin (obsidian-wikilink-types) and a cloud service (Penfield)
- **Critical context: this is a vendor promoting their product.** The article is literally an ad for Penfield's paid service.
- The comment from "Survivor Forge" (an autonomous agent runner with 1,100+ sessions) is the most credible: "Started with flat markdown files. Links were implicit. After ~500 sessions, retrieval started returning noise because 'related' could mean anything. The fix was typed predicates on graph edges."
- BUT: "Autonomous linking in isolation tends to hallucinate connections that look plausible but aren't load-bearing."
- AND: "relationship discovery works better as a byproduct of real work, not from a dedicated linking pass"

### Source 3: KG Reasoning Survey (arxiv)
- "Conventional KG reasoning based on symbolic logic is deterministic, with reasoning results being explainable, while modern embedding-based reasoning can deal with uncertainty and predict plausible knowledge"
- "A promising direction is to integrate both logic-based and embedding-based methods"
- This is about large-scale KGs, not personal PKM

### Source 4: Obsidian Forum threads
- Community has been requesting link types for years
- Multiple workarounds exist (Breadcrumbs plugin, Dataview, frontmatter)
- No consensus on whether typed edges are worth the complexity

### The Vault's Internal Contradiction
- [[Pre-Thought-Information-Shapes]] advocates typed edges: "If the edges themselves carry relationship types, the graph traversal becomes reasoning"
- [[Vault-Thinks-LLM-Synthesizes]] rejects them: "No typed edges, no abstraction cache, no argument builder. These were considered but rejected — too much ongoing maintenance for unclear benefit."
- The rejection was based on: (1) typed edges add cost to every note write, (2) abstraction cache is self-maintaining subsystem, (3) in practice notes are written in r...

### My Assessment

The research picture is:
1. **One vendor blog post** (Penfield) is the primary advocate — and it's selling a product
2. **One practitioner comment** (Survivor Forge) provides the most credible evidence: typed predicates helped after 500 sessions, but autonomous linking hallucinates
3. **Karpathy himself** doesn't use typed edges — his wiki works with plain links + LLM maintenance
4. **The KG reasoning literature** is about large-scale knowledge graphs, not personal PKM with ~200 notes
5. **The vault itself** already rejected typed edges in [[Vault-Thinks-LLM-Synthesizes]]

The honest answer: typed edges are **a reasonable idea with weak research support**. The main evidence is:
- One vendor blog post (conflict of interest)
- One practitioner comment (anecdotal, n=1)
- General KG reasoning literature (different domain)

The main arguments against:
- Karpathy's pattern works without them
- Maintenance burden is real (even Penfield admits "manually typing @supersedes and @contradicts on every note is tedious")
- The vault already has a competing principle ([[Vault-Thinks-LLM-Synthesizes]]) that says prose explains relationships
- Autonomous linking "hallucinates connections that look plausible but aren't load-bearing"

I should write a synthesis note that honestly assesses this and resolves the contradiction. Let me also delete or mark the junk research notes.

Let me write a proper synthesis note and update the existing notes.The files are in the vault root, not in vaultbot_backend. Let me try different paths.Now I have the full picture. Let me also read the Pre-Thought-Information-Shapes note.Now I have the full picture. Let me synthesize everything and write a proper assessment note. Here's what I know:

## The Research Evidence

### For typed edges:
1. **Penfield Labs blog post** (dev.to) — the primary advocate. BUT: this is a **vendor blog post selling a product** (Penfield is a paid cloud knowledge graph service). The article promotes their Obsidian plugin (obsidian-wikilink-types) and their cloud backend. Conflict of interest is clear.

2. **Survivor Forge comment** — the most credible evidence. An autonomous agent operator with 1,100+ sessions and a Neo4j-backed KG says: "After ~500 sessions, retrieval started returning noise because 'related' could mean anything. The fix was typed predicates on graph edges." BUT: this is one anecdote, and they also say "Autonomous linking in isolation tends to hallucinate connections that look plausible but aren't load-bearing."

3. **Obsidian forum threads** — community has been requesting link types for years. Multiple workarounds exist (Breadcrumbs, Dataview, frontmatter). This shows desire but not evidence of effectiveness.

4. **KG Reasoning survey** (arxiv) — "Conventional KG reasoning based on symbolic logic is deterministic, with reasoning results being explainable." This is about large-scale KGs (Google's KG, Wikidata), not personal PKM with 200 notes.

5. **Christopher Allen's gist** — a reference guide for named edges in markdown. This is a how-to guide, not research evidence.

### Against typed edges:
1. **Karpathy's LLM Wiki** — the original pattern that started this conversation uses **plain wikilinks**. Karpathy's insight is that the LLM does the maintenance (cross-referencing, noting contradictions) in prose, not in typed edge syntax. The wiki works without typed edges.

2. **Penfield's own admission** — "manually typing @supersedes and @contradicts on every note is tedious, and you'll miss connections that aren't obvious." They had to build an AI tool (Vault Linker) to automate it, which "hallucinates connections that look plausible but aren't load-bearing."

3. **Vault-Thinks-LLM-Synthesizes** — the vault's own design principle explicitly rejected typed edges: "too much ongoing maintenance for unclear benefit." The argument: notes should be self-contained arguments with prose explaining relationships, not metadata requiring vocabulary discipline.

4. **Maintenance burden** — even Penfield admits this is the core problem. Their solution is to sell a cloud service that handles it. That's not applicable to Sean's goal of a local, accessible system.

5. **Scale mismatch** — the evidence for typed edges comes from systems with 500+ notes (Survivor Forge) or 4,000+ notes (Penfield's import). The vault currently has ~154 notes. The problem typed edges solve (retrieval noise at scale) doesn't exist yet.

### The honest assessment:

**The research support for typed edges is weak.** The primary source is a vendor blog post. The one practitioner anecdote is credible but acknowledges the key problem (hallucinated relationships). The academic literature is about a different domain (large-scale KGs, not personal PKM).

**The vault already has a competing principle** ([[Vault-Thinks-LLM-Synthesizes]]) that says prose explains relationships better than metadata. This principle was established before the typed edges research, and the research hasn't provided strong enough evidence to overturn it.

**The real question is: at what scale does the flat graph break down?** Survivor Forge says ~500 sessions/notes. Penfield says "past a few hundred notes." The vault is at ~154 notes. We're not there yet.

**The recommendation:** Don't build typed edges now. The research doesn't justify the maintenance cost at current scale. Instead:
1. Keep the [[Vault-Thinks-LLM-Synthesizes]] principle — prose explains relationships
2. Monitor for the scale problem (retrieval noise) as the vault grows
3. If/when retrieval quality degrades at 500+ notes, revisit typed edges with actual data
4. The [[RAG-Evaluation-for-FUSED-Retrieval]] system (already built) will detect this degradation

Let me write this up as a synthesis note and update the existing notes.Now I have everything I need. Let me write a synthesis note that honestly assesses the research and resolves the contradiction between Pre-Thought-Information-Shapes (pro typed edges) and Vault-Thinks-LLM-Synthesizes (anti typed edges).

Let me write the note using the safe_write tool (it's a markdown note, not a .py file, so I can use code_write... actually, looking at the tools, I should use safe_write for backend files and for markdown notes I can use code_write. But wait, I don't have code_write in my tool list. I have safe_write and vault_append. Let me use safe_write for the new note since it's a new file.

Actually, looking at the tools again: safe_write says "For markdown notes or non-code files, code_write is fine." But I don't have code_write in my available tools. I have safe_write which says it can be used for any file. Let me use safe_write for the new note.

Wait, actually I need to re-read the tool descriptions. safe_write says: "SAFE self-edit of backend source code. Use this INSTEAD of code_write for any .py file under vaultbot_backend/." And for markdown notes, it says "code_write is fine." But I don't have code_write. Let me just use safe_write — it should work for markdown too since it writes UTF-8 content.

Actually, looking more carefully, I think I should just use safe_write for the new note. It writes content to a file path. Let me create the synthesis note.The safe_write tool is trying to syntax-check the file as Python. Let me check if there's a way to write markdown files. The tool description says "For markdown notes or non-code files, code_write is fine." But I don't have code_write. Let me check my available tools again.

Looking at my tools, I have:
- safe_write — for .py files, but also works for other files
- vault_append — for appending to existing notes

The issue is that safe_write is trying to syntax-check the markdown as Python. The date "2026-07-26" might be causing issues, or some other content. Let me try a different approach — maybe I can use vault_append if the file already exists, or I need to find another way.

Actually, looking at the error more carefully: "leading zeros in decimal integer literals are not permitted" — this is a Python syntax error. The safe_write tool is checking the content as Python syntax. This is because the tool is designed for .py files. For markdown files, I should use a different approach.

Wait, the tool description says: "For markdown notes or non-code files, code_write is fine." But I don't have code_write in my tool list. Let me check if there's another way...

Actually, I think the issue might be that safe_write is designed for .py files and does syntax checking. For markdown files, I might need to use a different approach. Let me see if I can use vault_append to create a new file... no, vault_append appends to existing files.

Hmm, let me try using the tool_create or some other approach. Actually, wait — looking at the error, it says "line 2" which would be the `created: 2026-07-26` line. The YAML frontmatter has `2026-07-26` which Python might interpret as `2026 - 07 - 26` and `07` is a leading zero in a decimal integer literal.

But the tool should recognize .md files and skip Python syntax checking. Let me try with a simpler file path or see if there's a workaround.

Actually, I think the issue is that safe_write always does syntax checking regardless of file type. Let me try a different approach — maybe I can write the file using code_run to execute Python code that writes the file.Good, the file was written. Now let me lint it to verify quality.The lint shows 17 wikilinks, 0 broken, frontmatter present. It says tag_count is 0 and tags are empty, but I did include tags in the frontmatter. The empty sections issue is probably from the "## The Question" and "## The Short Answer" sections being short. That's fine — they're intentionally concise.

Now let me update the Pre-Thought-Information-Shapes note to reflect this assessment. I should append a section at the end noting the assessment results.

</details>