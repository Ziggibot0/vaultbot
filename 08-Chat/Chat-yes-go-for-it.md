# Chat: yes go for it

## 2026-07-26 02:43 UTC

**User:** yes go for it

**Assistant:** All 5 done. Here's the summary:

| # | Subfield | Sources | Facts | Note Location |
|---|---|---|---|---|
| 1 | **Claim verification / fact-checking** | 15 | 15 | `research/Automated-claim-verification-and-fact-checking-of-LLM-outputs-against-source-doc.md` |
| 2 | **RAG evaluation metrics** | 10 | 16 | `research/RAG-evaluation-metrics-how-to-measure-retrieval-quality-in-retrieval-augmented-g.md` |
| 3 | **Context window management** | 13 | 19 | `research/Context-window-management-for-graph-based-RAG-retrieval-strategies-for-truncatin.md` |
| 4 | **Calibration without ground truth** | 13 | 19 | `research/Calibrating-automated-quality-assessment-gates-without-ground-truth-labels-metho.md` |
| 5 | **Exemplar document design** | 6 | 15 | `research/Designing-permanent-exemplar-documents-for-pattern-matching-in-knowledge-based-A.md` |

## Key Findings Per Topic

**1. Claim verification** — The field has mature frameworks: **OpenFactCheck** (unified factuality evaluation), **MiniCheck** (efficient fact-checking against grounding documents), **Chain-of-Verification** (self-checking pipeline), and **Claimify** (Microsoft's claim extraction method). The pattern is: extract claims → retrieve evidence → verify each claim independently. Directly applicable to VaultBot — I can verify synthesized notes against their cited sources.

**2. RAG evaluation metrics** — Key frameworks: **RAGAS** (faithfulness, answer relevance, context precision/recall), **ARES** (automated evaluation), and ranking metrics like **recall@k** and **NDCG**. The critical insight: retrieval quality and generation quality are *separate* dimensions — you measure them independently. I can build a test suite that scores FUSED retrieval on both.

**3. Context window management** — Three strategies from Anthropic's context engineering work: **memory** (persist state externally), **compaction** (summarize old context), and **tool clearing** (drop stale tool results). Also: **FILCO** filters irrelevant spans from retrieved passages before generation. For VaultBot: as the vault grows, I need context budgeting — rank retrieved notes by relevance, truncate low-priority ones, and compact older context.

**4. Calibration without ground truth** — Strong research. Key insight: **LLM-as-judge is a measurement instrument, not an oracle** — it needs calibration against human ratings. Three biases to test for: position bias, verbosity bias, self-preference bias. The recommendation: build a calibration set of 500+ human-labeled cases before trusting aggregate metrics. For VaultBot: Sean's corrections ARE the ground truth — I need to systematically log them and use them to calibrate `vault_lint`.

**5. Exemplar design** — ⚠️ Weakest result. The research found KG-RAG papers (knowledge graph + RAG for schema matching) but didn't really hit the target of "how to design permanent exemplar notes for pattern-matching." This subfield may be too novel — it might not have established research yet. I may need to synthesize this one myself from the few-shot prompting research we already have + the procedural bootstrap plan.

## What's Next

The vault now has research coverage for 4 of the 5 subfields. The exemplar design gap is real — I'll need to reason from first principles using the existing few-shot research + the [[Small-Model-Path-to-AGI]] vision rather than citing external research.

Want me to start synthesizing these into architecture notes and procedural notes, or is there something else you want to dig into first?

<details>
<summary>Thinking process</summary>

Sean wants me to research all 5 subfields. Let me kick them off in parallel since they're independent.All 5 research topics completed. Let me summarize the results for Sean.

The research results vary in quality:
1. **Claim verification** — 15 sources, 15 facts. Good coverage of fact-checking frameworks (OpenFactCheck, MiniCheck, Chain-of-Verification, Claimify).
2. **RAG evaluation metrics** — 10 sources, 16 facts. Good coverage of RAGAS, recall@k, faithfulness metrics.
3. **Context window management** — 13 sources, 19 facts. Good coverage of context engineering, compaction, tool clearing.
4. **Calibration without ground truth** — 13 sources, 19 facts. Strong coverage of LLM-as-judge calibration, bias testing, human calibration.
5. **Exemplar design** — 6 sources, 15 facts. Weaker — mostly about KG-RAG for schema matching, not really about exemplar document design. This one didn't hit the target well.

Let me give Sean the summary. The exemplar design research was the weakest — it found KG-RAG papers but not really what I was looking for (how to structure permanent exemplar notes). I should flag that.

</details>