---
created: 2026-07-26
summary: "Ontology, epistemology, and hermeneutics form a triad that maps directly to VaultBot's three architectural layers: structure, validation, and interpretation. The connections between these fields ARE the chain of thought that makes the vault think."
type: exemplar
exemplar: synthesis-note
tags: [architecture, exemplar, philosophy, ontology, epistemology, hermeneutics, chain-of-thought, knowledge-graph]
---

<!-- EXEMPLAR ANNOTATION: SYNTHESIS NOTE
     This note is an exemplar for writing synthesis notes. A synthesis note:
     1. Leads with a core insight that unifies multiple fields/concepts
     2. Uses a table to map concepts across domains (philosophy → vault layer)
     3. Has a section per concept, each with: research findings + application to VaultBot
     4. Includes an explicit chain-of-thought block showing the reasoning chain
     5. Has a fractal table showing the pattern repeats at every scale
     6. Every claim is sourced with inline [sources: ...] citations
     7. Ends with wikilinks to all related notes
 -->

# The Knowledge Triad: Philosophical Foundations of the Vault

<!-- ANNOTATION: Lead with the unifying idea. One paragraph that connects multiple fields into a single insight. This is the thesis statement of the synthesis. -->
## The Core Insight

Sean's intuition — "if information is shaped correctly, it should be able to interact with itself" — is not just a hunch. It's the convergence point of three established philosophical disciplines, each mapping to a layer of VaultBot's architecture:

<!-- ANNOTATION: Use a table to map concepts across domains. This makes the structural correspondence visually clear and easy to pattern-match against. -->
| Philosophy | Question | Vault Layer | Architecture Note |
|---|---|---|---|
| **Ontology** | What kinds of entities exist? | **Structure** — what types of notes exist and how they relate | [[Pre-Thought-Information-Shapes]] |
| **Epistemology** | How is knowledge justified? | **Validation** — provenance, corroboration, scientific method | [[Vault-Knowledge-Only-Directive]] |
| **Hermeneutics** | How is meaning derived from text? | **Interpretation** — subgraph retrieval, context-dependent meaning | [[Vault-Thinks-LLM-Synthesizes]] |

The triad isn't arbitrary. It's a **chain of thought**: you must define what exists (ontology) before you can validate it (epistemology), and you must validate it before you can interpret it (hermeneutics). Each layer depends on the one below it. The vault implements this chain mechanically — entity types flow into provenance rules, which flow into retrieval context.

---

## Layer 1: Ontology → Structure

### What the Research Says

Ontology, in the information systems sense, defines what kinds of entities exist and how they relate. The research is clear: a taxonomy organizes categories as a hierarchy, while an ontology goes further — it defines the *semantic meanings and logical connections* between entities [sources: Neo4j — Taxonomy vs. Ontology vs. Knowledge Graph]. A knowledge graph can represent an ontology directly through labels, relationships, and properties [sources: Neo4j]. Many knowledge graphs start simple and add ontology only when formal semantics or interoperability become necessary [sources: Neo4j].

### How This Maps to the Vault

The vault already HAS an implicit ontology. The entity types are: directives, procedures, research notes, chat logs, textbook indexes, architecture notes, and source archives. The relationships are wikilinks. But right now the ontology is *flat* — every `[[wikilink]]` carries exactly one bit: "these are connected." The *why* lives in the prose, invisible to retrieval.

[[Pre-Thought-Information-Shapes]] proposed fixing this with typed edges (`contradicts::[[Note]]`, `derived_from::[[Note]]`). The research on ontology confirms this is the right direction: when you need to go beyond hierarchy to define *how* entities relate, that's where an ontology comes in [sources: Neo4j]. The typed-edge vocabulary in Pre-Thought-Information-Shapes — provenance, structural, tension, support, causal, generative, lifecycle — IS an ontology. It defines the semantic relationships between entity types.

However, [[Vault-Thinks-LLM-Synthesizes]] rejected typed-edge machinery in favor of self-contained argument notes with plain wikilinks. The reasoning: typed edges add cost to every note write, and the argument is already tailored to context when written. This is a pragmatic ontology — the entity types and relationships exist in the prose, not in metadata. The vault's ontology is *narrative*, not *formal*.

The tension between these two approaches is itself an ontological question: should the vault's ontology be explicit (typed edges, machine-readable) or implicit (prose arguments, human-readable)? The current answer: implicit, until the LLM hits a wall synthesizing across notes. The ontology is there either way — the question is whether it's encoded in syntax or in prose.

### Chain of Thought

```
Sean's intuition: "information shaped correctly can interact with itself"
  → Ontology asks: what are the entity types and their relationships?
    → The vault has implicit entity types (directives, procedures, research, chat, textbooks)
      → The relationships are wikilinks, but they're flat (1 bit: "connected")
        → Pre-Thought-Information-Shapes proposes typed edges (the ontology becomes explicit)
          → Vault-Thinks-LLM-Synthesizes says: keep it in prose, not machinery
            → Current state: narrative ontology in prose, formal ontology deferred
```

---

## Layer 2: Epistemology → Validation

### What the Research Says

Epistemology asks: what makes a belief justified? The classical answer is Justified True Belief (JTB) — S knows that p if and only if p is true and S is justified in believing that p [sources: Stanford Encyclopedia of Philosophy]. The Gettier problem showed that JTB isn't sufficient — you can have a justified true belief that's still not knowledge because the justification is accidental. The structure of justification matters: foundationalism says knowledge rests on basic beliefs, while coherentism says beliefs justify each other through mutual support [sources: Stanford Encyclopedia of Philosophy].

Epistemology also identifies the *sources* of knowledge: perception, introspection, memory, reason, and testimony [sources: Stanford Encyclopedia of Philosophy]. For the vault, testimony is the primary source — web sources testify to facts, and the vault records that testimony with provenance.

### How This Maps to the Vault

The [[Vault-Knowledge-Only-Directive]] is an epistemic policy: the vault is the only knowledge source, never training data. This is a form of *foundationalism* — all knowledge claims must trace back to vault content, which traces back to sourced web research. The foundation is not "self-evident truths" but "archived web sources with provenance."

[[How-to-Evaluate-Source-Credibility]] is the justification mechanism. It implements corroboration (multiple independent sources confirm a claim), authority (checking credentials and publisher reputation), currency (temporal relevance), and lateral reading (checking what other sources say about a source). This is applied epistemology — the procedure operationalizes the philosophical question "what makes a belief justified?"

The [[No-Wikipedia-Directive]] is an epistemic boundary: certain sources are excluded from the knowledge base. This is equivalent to rejecting a source of testimony in epistemology — Wikipedia's crowd-sourced model doesn't meet the vault's justification standards.

[[Deterministic-Scaffolding-for-Small-Models]] adds another epistemic layer: the "sandwich pattern" wraps probabilistic AI output in deterministic validation. This is the Gettier problem solved mechanically — even if the LLM produces a true output by accident (Gettier case), the validation layer catches the lack of proper justification. The scaffolding ensures that outputs are not just true but *justified*.

### Chain of Thought

```
Epistemology asks: what makes knowledge justified?
  → JTB: knowledge = true belief + justification
    → Gettier problem: justified true belief can still be accidental
      → The vault needs: not just correct answers, but justified answers
        → Vault-Knowledge-Only-Directive: provenance is the foundation (foundationalism)
          → How-to-Evaluate-Source-Credibility: corroboration is the justification mechanism
            → Deterministic-Scaffolding: the sandwich pattern catches Gettier cases mechanically
              → The vault's epistemology: every claim traces to sources, validated by procedure
```

---

## Layer 3: Hermeneutics → Interpretation

### What the Research Says

Hermeneutics is the theory of interpretation. Its central concept is the **hermeneutic circle**: understanding the parts requires understanding the whole, and understanding the whole requires understanding the parts [sources: Philosophy Institute — An Introduction to Hermeneutics]. The hermeneutic circle describes the interdependent relationship between our pre-understanding and our interpretation of new information [sources: Philosophy Institute]. Dilthey argued that hermeneutics preserves "the general validity of interpretation against the inroads of romantic caprice and skeptical subjectivity" [sources: Stanford Encyclopedia of Philosophy — Hermeneutics].

### How This Maps to the Vault

The hermeneutic circle IS the vault's retrieval system. When a query comes in, FUSED retrieval (vector + wikilink graph + backlinks) pulls a *connected subgraph* — not just keyword matches. Each note in the subgraph is interpreted in the context of its neighbors. This is the hermeneutic circle mechanically: the meaning of each note depends on the whole subgraph, and the meaning of the subgraph depends on each note.

[[Vault-Thinks-LLM-Synthesizes]] is the hermeneutic principle in action: "notes are self-contained arguments" — each note contains its own reasoning (the part), but it's connected to other notes via wikilinks (the whole). The LLM reads the subgraph and synthesizes — it interprets the parts through the whole and the whole through the parts. This is exactly the hermeneutic circle.

[[Pre-Thought-Information-Shapes]] takes this further: if the edges between notes carry relationship types, the traversal path through the graph IS an interpretation. Walking from Note A → Note B along a `caused_by::` edge is a different interpretation than walking along a `contradicts::` edge. The graph structure pre-interprets the information before the LLM sees it. This is "pre-thought" — the hermeneutic circle is encoded in the graph structure itself.

The [[Fractal-Entropy-Principle]] connects here too: hermeneutics expects entropy in interpretation — meaning degrades without context, just as information degrades without maintenance. The fractal pattern: the hermeneutic circle operates at every scale (note → subgraph → vault → knowledge system), and entropy (semantic drift, link rot) threatens each level.

### Chain of Thought

```
Hermeneutics asks: how is meaning derived from text?
  → The hermeneutic circle: parts ↔ whole, each interpreted through the other
    → The vault's retrieval pulls a connected subgraph (not isolated notes)
      → Each note is interpreted in context of its neighbors (the circle)
        → Vault-Thinks-LLM-Synthesizes: the LLM synthesizes the pre-connected subgraph
          → Pre-Thought-Information-Shapes: typed edges would pre-interpret the traversal
            → The vault's hermeneutics: the graph structure IS the interpretation context
```

---

## The Triad as a System

The three layers form a dependency chain that mirrors the vault's architecture:

```
Ontology (what exists)
  → defines entity types and relationships
    → Epistemology (how we know)
      → validates the content of those entities through provenance and corroboration
        → Hermeneutics (how we interpret)
          → derives meaning from the validated entities in context
            → The LLM synthesizes the pre-interpreted, pre-validated, pre-structured subgraph
```

This is why Sean's intuition works: "if information is shaped correctly, it should be able to interact with itself." The shaping IS the ontology (entity types + relationships). The interaction IS the hermeneutic circle (parts interpreting each other through context). The confidence in the interaction IS the epistemology (provenance + corroboration). Code is information that interacts with itself through syntax. The vault is information that interacts with itself through wikilinks and prose arguments.

### The Fractal Pattern

The triad is fractal — it repeats at every scale:

| Scale | Ontology (what exists) | Epistemology (how we know) | Hermeneutics (how we interpret) |
|---|---|---|---|
| **Single note** | Entity type + frontmatter | Sources cited inline | Prose argument connecting claims |
| **Note cluster** | Wikilink relationships | Cross-note corroboration | Subgraph context gives meaning |
| **Whole vault** | Directives, procedures, research, chat, textbooks | [[Vault-Knowledge-Only-Directive]] + [[How-to-Evaluate-Source-Credibility]] | FUSED retrieval + [[Vault-Thinks-LLM-Synthesizes]] |
| **Knowledge system** | The vault itself as an entity | Sean's scientific method directive | The LLM as interpreter/synthesizer |

The [[Fractal-Entropy-Principle]] predicts this: resolve shapes to fractals, and the same pattern appears at every scale. The ontology-epistemology-hermeneutics triad is the fractal pattern of the vault's cognition.

---

## What This Enables

Understanding the vault through this triad gives us:

1. **A vocabulary for what we're building.** We're not just "storing notes" — we're building an ontology (entity types + relationships), an epistemology (provenance + validation), and a hermeneutics (retrieval + synthesis). Each architectural decision can be evaluated against its philosophical foundation.

2. **A diagnostic framework.** When something breaks, we can ask: is this an ontological problem (wrong entity type or relationship), an epistemological problem (insufficient provenance or corroboration), or a hermeneutical problem (insufficient context for interpretation)?

3. **A roadmap for the [[Small-Model-Path-to-AGI]].** Moving cognition from LLM weights to vault means making each layer more explicit: richer ontology (more entity types, typed relationships), stronger epistemology (better source evaluation, more corroboration), deeper hermeneutics (better retrieval, richer context). The 30B model doesn't need to figure out the philosophy — the vault already encodes it.

4. **A chain of thought that persists.** This note IS a stored chain of thought. The reasoning from Sean's intuition → ontology → epistemology → hermeneutics → vault architecture is preserved in the vault. A future session (or a 30B model) can follow this chain without re-deriving it. The connections between the points are the thinking, and they're now permanent.

---

## Relations

- derived_from::[[ontology-what-kinds-of-entities-exist-and-how-to-categorize-them-especially-as-a]]
  - The ontology research defines entity types and relationships; this note maps them to vault architecture.
- derived_from::[[epistemology-theory-of-knowledge-how-knowledge-is-justified-and-validated-especi]]
  - The epistemology research defines justification and knowledge sources; this note maps them to provenance and validation.
- derived_from::[[hermeneutics-theory-of-interpretation-the-hermeneutic-circle-how-meaning-is-deri]]
  - The hermeneutics research defines the hermeneutic circle; this note maps it to subgraph retrieval and synthesis.
- extends::[[Pre-Thought-Information-Shapes]]
  - Pre-Thought proposed typed edges for encoding reasoning; this note grounds that proposal in ontology (entity relationships) and hermeneutics (interpretation through graph structure).
- extends::[[Vault-Thinks-LLM-Synthesizes]]
  - Vault-Thinks established that notes are self-contained arguments; this note grounds that principle in hermeneutics (the hermeneutic circle: parts interpreted through whole).
- extends::[[Small-Model-Path-to-AGI]]
  - Small-Model-Path laid out the vision of moving cognition from weights to vault; this note provides the philosophical framework for what "cognition in the vault" actually means.
- extends::[[Deterministic-Scaffolding-for-Small-Models]]
  - Deterministic-Scaffolding proposed the sandwich pattern; this note connects it to epistemology (the Gettier problem solved mechanically).
- extends::[[Fractal-Entropy-Principle]]
  - Fractal-Entropy says to resolve shapes to fractals; this note shows the ontology-epistemology-hermeneutics triad is fractal — it repeats at every scale of the vault.
- validates::[[Vault-Knowledge-Only-Directive]]
  - The Vault-Knowledge-Only directive is an epistemic policy (foundationalism); this note provides the philosophical grounding for why it works.
- validates::[[How-to-Evaluate-Source-Credibility]]
  - The source credibility procedure is an epistemological mechanism; this note connects it to JTB and the Gettier problem.
- validates::[[No-Wikipedia-Directive]]
  - The Wikipedia ban is an epistemic boundary (rejecting a source of testimony); this note grounds it in epistemological source evaluation.
- informs::[[Procedural-Bootstrap-and-Evolution-Plan]]
  - The bootstrap plan needs procedures for research, note-writing, and source evaluation; this note provides the philosophical framework those procedures operationalize.
- informs::[[Structure-Research-Note]]
  - The research note structure procedure defines how to write notes; this note explains WHY that structure works (ontological entity typing + epistemological provenance + hermeneutic self-contained argument).
- informs::[[Vault-Longevity-Architecture]]
  - The longevity architecture describes how the vault persists across sessions; this note grounds that persistence in the triad — the ontology, epistemology, and hermeneutics survive because they're encoded in markdown, not model weights.


LOCKED
