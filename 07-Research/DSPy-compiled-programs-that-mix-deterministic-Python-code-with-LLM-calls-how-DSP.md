# DSPy compiled programs that mix deterministic Python code with LLM calls — how DSPy modules compile declarative signatures into executable pipelines where some steps are pure Python and others are LLM calls. Also: LangGraph nodes that execute deterministic code without LLM, mixed code-and-LLM agent graphs, Quarto or Jupyter notebooks as executable agent workflows, executable specifications vs LLM-as-controller.

## Summary
Research into 'DSPy compiled programs that mix deterministic Python code with LLM calls — how DSPy modules compile declarative signatures into executable pipelines where some steps are pure Python and others are LLM calls. Also: LangGraph nodes that execute deterministic code without LLM, mixed code-and-LLM agent graphs, Quarto or Jupyter notebooks as executable agent workflows, executable specifications vs LLM-as-controller.' (13 sources, 19 facts).

## Key Findings
- Direct `Agent.call_tool` calls, another LLM instance, or a client connected directly to the MCP server bypass the example.  [sources: examples: demonstrate pre-tool approval for MCP calls]
- Jenkins Agent is a base image for Docker that includes Java and the Jenkins agent executable (agent.jar).  [sources: Jenkins Agent]
- Major releases are very infrequent (less than one per year). ⁠ Agent 6 The Datadog agent, including a Python 2 interpreter for Python checks.  [sources: datadog/agent]
- Upon receiving user requests, the LLM-driven agent generates plans using an LLM, executes these plans through various tools, and then returns the response to the user.  [sources: A Plan Reuse Mechanism for LLM-Driven Agent]
- Deterministic `rule-only`, `dry-run`, and fake-model paths work without a model API key; `--llm=openai` optionally enables a real OpenAI-compatible model for Skills orchestration assist.  [sources: examples/code_review_agent: add deterministic review agent example]
- A wake-gate script (`bootstrap_scan_gate.py`) skips the LLM run once the report exists, so the job stops spending tokens after it succeeds. - **`bootstrap-inventory-delivery`** — `no_agent` cron job (1m interval).  [sources: feat(platform): implement agent bootstrap mechanism]
- This study introduces the ``Focus Agent,'' a Large Language Model (LLM) powered framework that simulates both the focus group (for data collection) and acts as a moderator in a focus group setting with human participants.  [sources: Focus Agent: LLM-Powered Virtual Focus Group]
- It does not attempt to parse shell commands or provide a general security policy engine, and it does not add a new mcp-agent public API. ## Enforcement boundary The hook covers calls dispatched through this augmented LLM instance.  [sources: examples: demonstrate pre-tool approval for MCP calls]
- Note that Python 2 EOL is set for January 1, 2020.  [sources: datadog/agent]
- Focus Agent: LLM-Powered Virtual Focus Group.  [sources: Focus Agent: LLM-Powered Virtual Focus Group]
- On a fresh pod/PVC, the agent surveys the GKE environment in the background and delivers a complete inventory + prioritized SRE recommendations to the operator's chat, then transitions into normal daily operation — exactly once. ## Architecture Three pieces, coordinated by three flag files under `/opt/data/`: - **`bootstrap-inventory-scan`** — LLM cron job (1m interval).  [sources: feat(platform): implement agent bootstrap mechanism]
- ## Summary Implements first-time **bootstrap onboarding + GKE environment discovery** for the Platform Agent.  [sources: feat(platform): implement agent bootstrap mechanism]
- Quantitative analysis indicates that Focus Agent can generate opinions similar to those of human participants.  [sources: Focus Agent: LLM-Powered Virtual Focus Group]
- ## What changed Add a runnable Go code review agent example under `examples/code_review_agent`.  [sources: examples/code_review_agent: add deterministic review agent example]
- This executable is an instance of the Jenkins Remoting library and enables distributed build capabilities for Jenkins controllers.  [sources: Jenkins Agent]
- If you are uncertain about what your needs are, this is probably the one you should use. ⁠ agent:<version> This variant doesn't embed a Java runtime.  [sources: datadog/agent]
- Python is an interpreted, interactive, object-oriented, open-source programming language.  [sources: Python, python]

## Sources
- [examples/code_review_agent: add deterministic review agent example](https://github.com/trpc-group/trpc-agent-go/pull/2305) ([[learningMaterial/web/github-com-trpc-group-trpc-agent-go-pull-2305-c6aae040.html|archived]])
- [datadog/agent](https://hub.docker.com/r/datadog/agent) ([[learningMaterial/web/hub-docker-com-r-datadog-agent-157530e0.html|archived]])
- [feat(platform): implement agent bootstrap mechanism](https://github.com/gke-labs/kube-agents/pull/325) ([[learningMaterial/web/github-com-gke-labs-kube-agents-pull-325-9a13c56f.html|archived]])
- [Jenkins Agent](https://hub.docker.com/r/dhi/jenkins-agent)
- [examples: demonstrate pre-tool approval for MCP calls](https://github.com/lastmile-ai/mcp-agent/pull/723) ([[learningMaterial/web/github-com-lastmile-ai-mcp-agent-pull-723-b0c6175a.html|archived]])
- [Focus Agent: LLM-Powered Virtual Focus Group](http://arxiv.org/abs/2409.01907v1) ([[learningMaterial/web/arxiv-org-abs-2409-01907v1-af6acb18.html|archived]])
- [project discord](https://chat.marginalia.nu) ([[learningMaterial/web/chat-marginalia-nu-0b49d55a.html|archived]])
- [A Plan Reuse Mechanism for LLM-Driven Agent](http://arxiv.org/abs/2512.21309v2) ([[learningMaterial/web/arxiv-org-abs-2512-21309v2-f053bbab.html|archived]])
- [Python](https://hub.docker.com/r/dhi/python)
- [python](https://hub.docker.com/_/python) ([[learningMaterial/web/hub-docker-com-python-a6595cfd.html|archived]])
- [API Declarative CLI (ADC)](https://hub.docker.com/r/dhi/adc)
- [mongodb/signatures](https://hub.docker.com/r/mongodb/signatures) ([[learningMaterial/web/hub-docker-com-r-mongodb-signatures-3a7d98f9.html|archived]])
- [Kubeflow Pipelines - Frontend](https://hub.docker.com/r/dhi/kubeflow-pipelines-frontend)

## Follow-up Queries (gap fill)
- DSPy compiled programs that mix deterministic Python code with LLM calls — how DSPy modules compile declarative signatures into executable pipelines where some steps are pure Python and others are LLM calls. Also: LangGraph nodes that execute deterministic code without LLM, mixed code-and-LLM agent graphs, Quarto or Jupyter notebooks as executable agent workflows, executable specifications vs LLM-as-controller. versus compared to

<!-- research: 13 sources, 19 facts, 3 rounds -->

## Related

[[Procedure-Subprocess-Architecture]]
[[Deterministic-Scaffolding-for-Small-Models]]
