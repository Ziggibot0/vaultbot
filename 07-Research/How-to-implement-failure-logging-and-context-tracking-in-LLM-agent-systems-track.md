# How to implement failure logging and context tracking in LLM agent systems — tracking which context/prompt was used per turn, logging validation failures, patterns for agent self-improvement feedback loops

## Summary
Research into 'How to implement failure logging and context tracking in LLM agent systems — tracking which context/prompt was used per turn, logging validation failures, patterns for agent self-improvement feedback loops' (8 sources, 15 facts).

## Key Findings
- agent-reflexion-mcp MCP server for agent self-improvement and reflection.  [sources: GitHub - mdfifty50-boop/agent-reflexion-mcp: Agent self-improvement and ...]
- The solution is structured, leveled, and contextual logging designed specifically for multi-step agent execution.  [sources: AI Agent Monitoring, Logging, and Debugging]
- The most valuable agent-specific logging pattern is the decision log , a record of why the agent chose each action.  [sources: AI Agent Monitoring, Logging, and Debugging]
- Our technique, which allows for the validation of agent based simulations uses VOMAS: a Virtual Overlay Multi-agent System.  [sources: Verification & Validation of Agent Based Simulations using the VOMAS (Virtual Overlay Multi-agent System) approach]
- First, agent behavior is non-deterministic.  [sources: AI Agent Monitoring, Logging, and Debugging]
- In this paper, we address this important area of validation of agent based models by presenting a novel technique which has broad applicability and can be applied to all kinds of agent-based models.  [sources: Verification & Validation of Agent Based Simulations using the VOMAS (Virtual Overlay Multi-agent System) approach]
- They instrument from the first prototype, because retrofitting telemetry into a running agent is painful and because every day of operation without logging is a day of lost data that could have driven improvement.  [sources: AI Agent Monitoring, Logging, and Debugging]
- Input: agent_id (string) — Agent tracking this metric metric_name (string) — Metric name (e.g. "accuracy", "response_time_ms") value (number) — Current value context (string, optional) — Context for this data point Output: { metric_id, trend ("up"/"down"/"stable"), rolling_average, data_points } get_performance_report Generate a comprehensive performance summary.  [sources: GitHub - mdfifty50-boop/agent-reflexion-mcp: Agent self-improvement and ...]
- In addition, since agent-based models have been typically growing, in parallel, in multiple domains, to cater for all of these, we present a new single validation technique applicable to all agent based models.  [sources: Verification & Validation of Agent Based Simulations using the VOMAS (Virtual Overlay Multi-agent System) approach]
- Structured logging, where each event is a JSON object with consistent fields, is essential because agent logs generate enormous volume and the only way to make sense of them is programmatic querying.  [sources: AI Agent Monitoring, Logging, and Debugging]
- Cost and Latency Tracking AI agents consume paid API resources on every invocation, and the relationship between agent behavior and cost is far less predictable than in traditional software.  [sources: AI Agent Monitoring, Logging, and Debugging]
- When an agent produces a wrong answer, you need to know whether the problem was a bad prompt, a failed tool call, a hallucinated intermediate result, a context window overflow, or a model limitation.  [sources: AI Agent Monitoring, Logging, and Debugging]
- Third, the cost of each agent invocation is not fixed.  [sources: AI Agent Monitoring, Logging, and Debugging]
- Every token of input and output costs money, and the total cost of a single task depends on how many LLM calls the agent makes, how large the context is for each call, and whether retries occur.  [sources: AI Agent Monitoring, Logging, and Debugging]

## Sources
- [project discord](https://chat.marginalia.nu) ([[learningMaterial/web/chat-marginalia-nu-0b49d55a.html|archived]])
- [Self-Improvements in Modern Agentic Systems: A Survey](https://arxiv.org/abs/2607.13104v1) ([[learningMaterial/web/arxiv-org-abs-2607-13104v1-dd2804d9.html|archived]])
- [GitHub - mdfifty50-boop/agent-reflexion-mcp: Agent self-improvement and ...](https://github.com/mdfifty50-boop/agent-reflexion-mcp) ([[learningMaterial/web/github-com-mdfifty50-boop-agent-reflexion-mcp-460445b3.html|archived]])
- [cogitatio Volume 1­3­ 2­0­2­5­](https://www.cogitatiopress.com/socialinclusion/issue/download/514/433) ([[learningMaterial/web/www-cogitatiopress-com-socialinclusion-issue-download-514-433-64fbe624.html|archived]])
- [Exploiting Context to Identify Lexical Atoms -- A Statistical View of Linguistic Context](https://arxiv.org/abs/cmp-lg/9701001v1) ([[learningMaterial/web/arxiv-org-abs-cmp-lg-9701001v1-54c37c45.html|archived]])
- [PDF Self-Improvement Agent Harness: A Deterministic SIA Exemplar](https://zenodo.org/records/20453880/files/Friedman_2026_Selfimprovement_7087b6d1.pdf?download=1) ([[learningMaterial/web/zenodo-org-records-20453880-files-friedman-2026-selfimprovement-7087b6d1-pdf-d69a9b90.html|archived]])
- [Verification & Validation of Agent Based Simulations using the VOMAS (Virtual Overlay Multi-agent System) approach](https://arxiv.org/abs/1708.02361v1) ([[learningMaterial/web/arxiv-org-abs-1708-02361v1-2c8134b3.html|archived]])
- [AI Agent Monitoring, Logging, and Debugging](https://www.autolearningagents.com/ai-agent-observability/) ([[learningMaterial/web/www-autolearningagents-com-ai-agent-observability-ac8581df.html|archived]])

<!-- research: 8 sources, 15 facts, 4 rounds -->

## Related

[[Procedural-Bootstrap-and-Evolution-Plan]]
