# Embedding executable code blocks in SOP/procedure documents for AI agents — frameworks that let procedures contain direct tool calls (code) alongside LLM steps, so deterministic steps run without LLM involvement and only semantic steps invoke the model. Markdown-as-code for AI agents, mixed deterministic-LLM procedure execution, reducing LLM calls via procedural code.

## Summary
Research into 'Embedding executable code blocks in SOP/procedure documents for AI agents — frameworks that let procedures contain direct tool calls (code) alongside LLM steps, so deterministic steps run without LLM involvement and only semantic steps invoke the model. Markdown-as-code for AI agents, mixed deterministic-LLM procedure execution, reducing LLM calls via procedural code.' (7 sources, 12 facts).

## Key Findings
- In this paper we show that how the guidance is produced is the decisive variable, and introduce \emph{probe-and-refine tuning}: a procedure that uses synthetic bug-fix probes to iteratively diagnose and patch a repository's guidance file through single-shot LLM calls, with no agent loop or tool use during tuning.  [sources: [2606.20512v1] Probe-and-Refine Tuning of Repository Guidance for Coding Agents]
- We introduce Recommendation Atlas (Agentic Tool-Level Assessment for Shopping), or RecoAtlas, a benchmark and toolkit for evaluating shopping agents with behavior-grounded metrics.  [sources: RecoAtlas: From Semantic Plausibility to Set-Level Utility in LLM Recommendation Agents, RecoAtlas: From Semantic Plausibility to Set-Level Utility in LLM Recommendation Agents]
- LLM recommendation agents increasingly produce structured recommendation reports: sets of items accompanied by natural-language justifications.  [sources: RecoAtlas: From Semantic Plausibility to Set-Level Utility in LLM Recommendation Agents]
- Its controlled tool environment exposes agents to either semantic, behavior-aligned, or faulty tools, enabling diagnosis of whether performance gains arise from stronger reasoning, better signals, or more effective tool-use policies.  [sources: RecoAtlas: From Semantic Plausibility to Set-Level Utility in LLM Recommendation Agents, RecoAtlas: From Semantic Plausibility to Set-Level Utility in LLM Recommendation Agents]
- Additional option --steps will show us the running process.  [sources: codeception/codeceptjs]
- It abstracts browser interaction to simple steps that are written from a user perspective.  [sources: codeception/codeceptjs]
- Pattern follows the harness mechanics reference (#589): one self-contained doc, no code changes. ## Linear Closes MOT-4223.  [sources: (MOT-4223) llm-router: reference skill]
- If you want to send additions or fixes to the code or the documentation please check the Contributing guide ⁠ . ⁠ At a Glance Describe what you test and how you test it.  [sources: codeception/codeception]
- Engineers typically maintain \texttt{AGENTS.md} files to supply this context as instructions for coding agents, but whether they help is contested: recent studies disagree on whether LLM-generated guidance improves or harms agent performance.  [sources: [2606.20512v1] Probe-and-Refine Tuning of Repository Guidance for Coding Agents]
- ## What A real skill for llm-router (skills/SKILL.md), replacing the auto-generated registry stub ("Worker on this mesh exposing 4 function(s)... no published skill exists for this worker yet") that agents currently get from `directory::skills::get { id: "llm-router" }`.  [sources: (MOT-4223) llm-router: reference skill]
- On SWE-bench Verified across four independent trials with Qwen3.5-35B-A3B at 200 steps, probe-and-refine achieves 33.0\,\% mean resolve rate vs.\ 28.3\,\% for the static knowledge base used to initialize it and 25.5\,\% for an unguided baseline ($p < 0.001$ for both probe-and-refine contrasts).  [sources: [2606.20512v1] Probe-and-Refine Tuning of Repository Guidance for Coding Agents]

## Sources
- [(MOT-4223) llm-router: reference skill](https://github.com/iii-hq/workers/pull/590) ([[learningMaterial/web/github-com-iii-hq-workers-pull-590-3f88c343.html|archived]])
- [codeception/codeceptjs](https://hub.docker.com/r/codeception/codeceptjs) ([[learningMaterial/web/hub-docker-com-r-codeception-codeceptjs-8685b0ee.html|archived]])
- [RecoAtlas: From Semantic Plausibility to Set-Level Utility in LLM Recommendation Agents](https://arxiv.org/abs/2605.18805v1) ([[learningMaterial/web/arxiv-org-abs-2605-18805v1-0f6a7df6.html|archived]])
- [[2606.20512v1] Probe-and-Refine Tuning of Repository Guidance for Coding Agents](https://github.com/Mont9165/arxiv-issue-bot/issues/5933) ([[learningMaterial/web/github-com-mont9165-arxiv-issue-bot-issues-5933-b18c4040.html|archived]])
- [codeception/codeception](https://hub.docker.com/r/codeception/codeception) ([[learningMaterial/web/hub-docker-com-r-codeception-codeception-123e5ef2.html|archived]])
- [RecoAtlas: From Semantic Plausibility to Set-Level Utility in LLM Recommendation Agents](http://arxiv.org/abs/2605.18805v1) ([[learningMaterial/web/arxiv-org-abs-2605-18805v1-48ef2ef2.html|archived]])
- [Uncertainty Decomposition for Clarification Seeking in LLM Agents](http://arxiv.org/abs/2606.19559v1) ([[learningMaterial/web/arxiv-org-abs-2606-19559v1-45d59d89.html|archived]])

<!-- research: 7 sources, 12 facts, 3 rounds -->

## Related

[[Procedure-Subprocess-Architecture]]
