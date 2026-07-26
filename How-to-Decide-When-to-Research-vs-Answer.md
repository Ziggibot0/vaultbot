---
type: procedure
status: experimental
created: 2026-07-26
last_reviewed: 2026-07-26
review_interval_days: 90
success_count: 0
failure_count: 0
success_rate: 0.0
falsifiable_if: "VaultBot answers from vault when it should have researched, or researches when the vault already had the answer, or says IDK when the vault had sufficient information"
applies_to:
  - chat
  - research
  - retrieval-decisions
depends_on:
  - "[[IDK-Fallback-Directive]]"
  - "[[Vault-Knowledge-Only-Directive]]"
  - "[[How-to-Evaluate-Source-Credibility]]"
sources:
  - "https://blog.reachsumit.com/posts/2025/09/probing-llms-knowledge-boundary/"
  - "https://arxiv.org/abs/2601.05264v1"
  - "https://arxiv.org/abs/2510.22344v1"
---

# How to Decide When to Research vs Answer

## When to Use This

Use this procedure every time Sean asks a question or you need to respond to a request. This is the decision gate that determines whether you answer directly from vault content, trigger web research, or say "I don't know." It implements the [[IDK-Fallback-Directive]] with additional nuance from adaptive RAG research.

## The Decision Tree

### Step 1: Search the Vault

Run `vault_search` with the query. Examine the returned notes.

**If the vault returns notes that directly address the question** → Go to Step 2.
**If the vault returns notes that are tangentially related but don't answer the question** → Go to Step 3.
**If the vault returns nothing relevant** → Go to Step 4.

### Step 2: Assess Vault Coverage

Read the returned notes. Ask: "Do these notes contain enough information to answer Sean's specific question?"

**Yes — the notes contain the answer or sufficient context to synthesize it** → Answer from vault. Cite notes with wikilinks. Done.
**Partial — the notes have some relevant information but are missing key pieces** → Go to Step 3.
**No — the notes are related but don't actually answer the question** → Go to Step 3.

### Step 3: Research to Fill the Gap

Tell Sean: "I don't have enough in the vault — researching <topic> now..."

Then call `vault_research` with a focused query targeting the missing information.

**If research succeeds** → Synthesize a sourced answer. Write a permanent note. Done.
**If research returns irrelevant results** → Try once more with a rephrased query.
**If research fails entirely (engine down, no sources found)** → Go to Step 4.

### Step 4: Say "I Don't Know"

Say: "I don't know." Period. No hedging, no "here's what I think but can't verify," no training data leakage. This is the [[IDK-Fallback-Directive]] — the vault is empty on this topic and research isn't working.

## What the Research Says

Adaptive RAG research identifies three approaches to the retrieval decision [sources: Probing LLMs' Knowledge Boundary: Adaptive RAG, Part 3]:

| Approach | How it works | Applicable to VaultBot? |
|---|---|---|
| **Prompt-based** | Ask the model to assess its own confidence | Yes — the decision tree above is a deterministic version of this |
| **Consistency-based** | Generate multiple responses, measure agreement | No — too expensive for a 30B local model |
| **Internal state-based** | Analyze hidden states/logits | No — requires white-box access to model weights |

The most relevant finding is the **Punish+Explain** method [sources: Probing LLMs' Knowledge Boundary: Adaptive RAG, Part 3]: adding "you will be punished if the answer is not right but you say certain" and "explain why you give this answer" to prompts calibrates the model's self-assessment. This achieves comparable performance to always-on retrieval with significantly fewer retrieval calls.

For VaultBot, the deterministic decision tree above replaces prompt-based confidence detection — we don't ask the model if it's confident, we check the vault mechanically. This is more reliable than self-assessment for a 30B model, which research shows tends toward overconfidence [sources: Probing LLMs' Knowledge Boundary: Adaptive RAG, Part 3].

## Common Failure Modes

| Failure | What happens | How to fix |
|---|---|---|
| **False confidence** | Vault has related notes, model synthesizes an answer that isn't actually supported | Check that cited notes contain the specific claim, not just related concepts |
| **Unnecessary research** | Vault has the answer but model researches anyway | Always check vault first with `vault_search` before researching |
| **Premature IDK** | Vault has partial info, model says IDK instead of synthesizing | If vault has ANY relevant notes, attempt synthesis before falling back to research or IDK |
| **Research when vault is sufficient** | Model researches a topic that's already well-covered | If `vault_search` returns 3+ highly relevant notes, answer from vault |

## Validation Criteria

This procedure is working correctly when:
- Sean's questions are answered from vault content when the vault has the answer
- Research is triggered only when the vault is genuinely insufficient
- "I don't know" is said only when both vault and research fail
- Sean does not correct VaultBot for answering from vault when it should have researched (or vice versa)

## Related

- [[IDK-Fallback-Directive]] — the policy this procedure implements
- [[Vault-Knowledge-Only-Directive]] — vault is the only knowledge source
- [[How-to-Evaluate-Source-Credibility]] — what to do after research returns sources
- [[How-to-Structure-a-Research-Note]] — how to write the permanent note after researching
- [[Deterministic-Scaffolding-for-Small-Models]] — why deterministic decisions beat model judgment for 30B
