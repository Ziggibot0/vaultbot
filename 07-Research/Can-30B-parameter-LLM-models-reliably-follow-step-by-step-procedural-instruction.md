# Can 30B parameter LLM models reliably follow step-by-step procedural instructions for tool use and task execution without hallucinating or skipping steps

## Summary
Research into 'Can 30B parameter LLM models reliably follow step-by-step procedural instructions for tool use and task execution without hallucinating or skipping steps' (13 sources, 16 facts).

## Key Findings
- Definition 1 (Procedural hallucination) .  [sources: Attention Deficits in Language Models: Causal Explanations for Procedural Hallucinations]
- We call this a procedural hallucination : a failure to follow a verifiable prompt-grounded specification .  [sources: Attention Deficits in Language Models: Causal Explanations for Procedural Hallucinations]
- We show that speculative execution attacks consist of 6 critical attack steps.  [sources: SoK: Hardware Defenses Against Speculative Execution Attacks]
- In this paper, we argue that TTRPG design can usefully be viewed as procedural content generator design.  [sources: Tabletop Roleplaying Games as Procedural Content Generators]
- This paper presents a systematization of the hardware defenses against speculative execution attacks that have been proposed.  [sources: SoK: Hardware Defenses Against Speculative Execution Attacks]
- For instance, the step-by-step PPO with Math-Shepherd significantly improves the accuracy of Mistral-7B (77.9\%$\to$84.1\% on GSM8K and 28.6\%$\to$33.0\% on MATH).  [sources: Math-Shepherd: Verify and Reinforce LLMs Step-by-step without Human Annotations]
- Tabletop roleplaying games (TTRPGs) and procedural content generators can both be understood as systems of rules for producing content.  [sources: Tabletop Roleplaying Games as Procedural Content Generators]
- We study this as procedural hallucination : failure to execute a verifiable, prompt-grounded specification even when the correct value is present in-context.  [sources: Attention Deficits in Language Models: Causal Explanations for Procedural Hallucinations]
- Unlike factual hallucination, which concerns world knowledge, procedural hallucination concerns the model’s ability to faithfully execute a specification given in its own context.  [sources: Attention Deficits in Language Models: Causal Explanations for Procedural Hallucinations]
- 1.1 Overview of Contributions We develop a theory of procedural hallucinations grounded in information theory and causal reasoning, then validate it empirically across model families.  [sources: Attention Deficits in Language Models: Causal Explanations for Procedural Hallucinations]
- We say the model exhibits a procedural hallucination on W W if Y ^ ​ ( W ) ≠ g ​ ( W ) \hat{Y}(W)\neq g(W) in settings where g ​ ( W ) g(W) is unambiguous and verifiable from the prompt (i.e., the answer is deterministically recoverable by an explicit procedure).  [sources: Attention Deficits in Language Models: Causal Explanations for Procedural Hallucinations]
- Speculative execution attacks leverage the speculative and out-of-order execution features in modern computer processors to access secret data or execute code that should not be executed.  [sources: SoK: Hardware Defenses Against Speculative Execution Attacks]
- Procedural hallucinations correspond to η k ≪ 1 \eta_{k}\ll 1 : the hidden state encodes the answer ( I avail I_{\mathrm{avail}} is large), but the output ignores it ( I used I_{\mathrm{used}} is small).  [sources: Attention Deficits in Language Models: Causal Explanations for Procedural Hallucinations]
- To facilitate future research, we release the step-by-step instructions and their human quality evaluation results.  [sources: Improving Cross-Task Generalization with Step-by-Step Instructions]

## Sources
- [Tabletop Roleplaying Games as Procedural Content Generators](https://arxiv.org/abs/2007.06108v2) ([[learningMaterial/web/arxiv-org-abs-2007-06108v2-78cf99a8.html|archived]])
- [project discord](https://chat.marginalia.nu) ([[learningMaterial/web/chat-marginalia-nu-0b49d55a.html|archived]])
- [Attention Deficits in Language Models: Causal Explanations for Procedural Hallucinations](https://arxiv.org/html/2602.19239) ([[learningMaterial/web/arxiv-org-html-2602-19239-22a721af.html|archived]])
- [SoK: Hardware Defenses Against Speculative Execution Attacks](https://arxiv.org/abs/2301.03724v1) ([[learningMaterial/web/arxiv-org-abs-2301-03724v1-6d6c9ec6.html|archived]])
- [[­AINews]­ not much happened today •­ Buttondown](https://buttondown.com/ainews/archive/ainews-not-much-happened-today-7847/) ([[learningMaterial/web/buttondown-com-ainews-archive-ainews-not-much-happened-today-7847-9c39967b.html|archived]])
- [Beyond Final Answers: Auditing Trajectory-Level Hallucinations in Multi ...](https://arxiv.org/pdf/2605.24219v2) ([[learningMaterial/web/arxiv-org-pdf-2605-24219v2-05288529.html|archived]])
- [When LLMs Stop Following Steps: A Diagnostic Study of Procedural ...](https://alanhou.org/blog/arxiv-when-llms-stop-following-steps-a/) ([[learningMaterial/web/alanhou-org-blog-arxiv-when-llms-stop-following-steps-a-79aed653.html|archived]])
- [Math-Shepherd: Verify and Reinforce LLMs Step-by-step without Human Annotations](https://arxiv.org/abs/2312.08935v3) ([[learningMaterial/web/arxiv-org-abs-2312-08935v3-34fd56b7.html|archived]])
- [TRICE-30B Model: Tool Learning with Feedback](https://www.emergentmind.com/topics/trice-30b-model) ([[learningMaterial/web/www-emergentmind-com-topics-trice-30b-model-6179dd87.html|archived]])
- [RL-Finetuned Qwen3-30B Models - emergentmind.com](https://www.emergentmind.com/topics/rl-finetuned-qwen3-30b-models) ([[learningMaterial/web/www-emergentmind-com-topics-rl-finetuned-qwen3-30b-models-242d5c48.html|archived]])
- [Improving Cross-Task Generalization with Step-by-Step Instructions](https://arxiv.org/abs/2305.04429v1) ([[learningMaterial/web/arxiv-org-abs-2305-04429v1-7e0d2831.html|archived]])
- [Best Local Tool-Calling Models 2026: Real MCP Benchmarks](https://www.promptquorum.com/power-local-llm/best-local-models-tool-calling-2026) ([[learningMaterial/web/www-promptquorum-com-power-local-llm-best-local-models-tool-calling-2026-d26d46e7.html|archived]])
- [Towards Better Instruction Following Retrieval Models](https://arxiv.org/abs/2505.21439v1) ([[learningMaterial/web/arxiv-org-abs-2505-21439v1-b9ace03f.html|archived]])

## Follow-up Queries (gap fill)
- Can 30B parameter LLM models reliably follow step-by-step procedural instructions for tool use and task execution without hallucinating or skipping steps hallucinating
- Can 30B parameter LLM models reliably follow step-by-step procedural instructions for tool use and task execution without hallucinating or skipping steps instructions
- Can 30B parameter LLM models reliably follow step-by-step procedural instructions for tool use and task execution without hallucinating or skipping steps parameter

<!-- research: 13 sources, 16 facts, 3 rounds -->