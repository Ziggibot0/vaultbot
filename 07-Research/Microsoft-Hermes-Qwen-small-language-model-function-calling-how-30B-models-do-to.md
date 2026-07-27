# Microsoft Hermes Qwen small language model function calling — how 30B models do tool use with structured prompts, system prompt templates for function calling, and what frameworks exist for making small models reliable at tool selection

## Summary
Research into 'Microsoft Hermes Qwen small language model function calling — how 30B models do tool use with structured prompts, system prompt templates for function calling, and what frameworks exist for making small models reliable at tool selection' (8 sources, 14 facts).

## Key Findings
- In contrast, Small Language Models (SLMs) can operate efficiently, offering faster response times, and lower computational demands, making them potential candidates for function calling on edge devices.  [sources: Small Models, Big Tasks: An Exploratory Empirical Study on Small Language Models for Function Calling]
- Function calling is a complex task with widespread applications in domains such as information retrieval, software engineering and automation.  [sources: Small Models, Big Tasks: An Exploratory Empirical Study on Small Language Models for Function Calling]
- The system now also provides tag-based permission policies, BM25F-powered progressive tool disclosure for large registries, think-augmented function calling, multi-provider schema support (OpenAI, Anthropic, Gemini), declarative JSONC/YAML configuration, and a near-zero-dependency core built on stdlib-only vendored modules.  [sources: ToolRegistry: A Protocol-Agnostic Tool Management Library for Function-Calling LLMs]
- Every LLM tool call is structurally an RPC -- a function name, JSON arguments, and a serialized result -- yet each protocol (native Python, MCP, OpenAPI, LangChain) is integrated from scratch.  [sources: ToolRegistry: A Protocol-Agnostic Tool Management Library for Function-Calling LLMs]
- In this exploratory empirical study, we evaluate the efficacy of SLMs in generating function calls across diverse domains using zero-shot, few-shot, and fine-tuning approaches, both with and without prompt injection, while also providing the finetuned models to facilitate future applications.  [sources: Small Models, Big Tasks: An Exploratory Empirical Study on Small Language Models for Function Calling]
- Complex physical models are the most advanced tools available for producing realistic simulations of the climate system.  [sources: AIRCC-Clim: a user-friendly tool for generating regional probabilistic climate change scenarios and risk measures]
- Large Language Models (LLMs) can automate this process but are computationally expensive and impractical in resource-constrained settings.  [sources: Small Models, Big Tasks: An Exploratory Empirical Study on Small Language Models for Function Calling]
- Prompt injection experiments further indicate that the models are generally robust and exhibit only a slight decline in performance.  [sources: Small Models, Big Tasks: An Exploratory Empirical Study on Small Language Models for Function Calling]
- AIRCC-Clim emulates 37 atmosphere-ocean coupled general circulation models with low computational and technical requirements for the user.  [sources: AIRCC-Clim: a user-friendly tool for generating regional probabilistic climate change scenarios and risk measures]
- Furthermore, we analyze the model responses across a range of metrics, capturing various aspects of function call generation.  [sources: Small Models, Big Tasks: An Exploratory Empirical Study on Small Language Models for Function Calling]
- While SLMs demonstrate potential for the function call generation task, our results also highlight areas that need further refinement for real-time functioning.  [sources: Small Models, Big Tasks: An Exploratory Empirical Study on Small Language Models for Function Calling]
- We recommend using Hermes-style tool use for Qwen3 to maximize function calling performance.  [sources: Function Calling - Qwen]

## Sources
- [AIRCC-Clim: a user-friendly tool for generating regional probabilistic climate change scenarios and risk measures](https://arxiv.org/abs/2111.01762v1) ([[learningMaterial/web/arxiv-org-abs-2111-01762v1-64540b5b.html|archived]])
- [Function Calling: Structured Tool Use for Large Language Models](https://mbrenndoerfer.com/writing/function-calling-llm-structured-tools) ([[learningMaterial/web/mbrenndoerfer-com-writing-function-calling-llm-structured-tools-cc0d959a.html|archived]])
- [ToolRegistry: A Protocol-Agnostic Tool Management Library for Function-Calling LLMs](https://arxiv.org/abs/2507.10593v3) ([[learningMaterial/web/arxiv-org-abs-2507-10593v3-a89c3479.html|archived]])
- [LLM Agent & Tool-Use Benchmarks — Function Calling, MCP, Structured ...](https://benchlm.ai/llm-agent-benchmarks) ([[learningMaterial/web/benchlm-ai-llm-agent-benchmarks-dca962fd.html|archived]])
- [Small Models, Big Tasks: An Exploratory Empirical Study on Small Language Models for Function Calling](https://arxiv.org/abs/2504.19277v1) ([[learningMaterial/web/arxiv-org-abs-2504-19277v1-7dbc1ff3.html|archived]])
- [project discord](https://chat.marginalia.nu) ([[learningMaterial/web/chat-marginalia-nu-0b49d55a.html|archived]])
- [Function Calling and Tool Use | QwenLM/Qwen3 | DeepWiki](https://deepwiki.com/QwenLM/Qwen3/4.3-function-calling-and-tool-use) ([[learningMaterial/web/deepwiki-com-qwenlm-qwen3-4-3-function-calling-and-tool-use-682403bb.html|archived]])
- [Function Calling - Qwen](https://qwen.readthedocs.io/en/latest/framework/function_call.html) ([[learningMaterial/web/qwen-readthedocs-io-en-latest-framework-function-call-html-c7a14bc5.html|archived]])

## Follow-up Queries (gap fill)
- Microsoft Hermes Qwen small language model function calling — how 30B models do tool use with structured prompts, system prompt templates for function calling, and what frameworks exist for making small models reliable at tool selection structured

<!-- research: 8 sources, 14 facts, 2 rounds -->

## Related Architecture
- [[Can-30B-parameter-LLM-models-reliably-follow-step-by-step-procedural-instruction]]
- [[Deterministic-Scaffolding-for-Small-Models]]
- [[Small-Model-Path-to-AGI]]
