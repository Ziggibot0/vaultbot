---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-08-03
description: "Report the current system status: backend running, tools available, autonomous researcher state, and any known issues. Uses the small model to format the report."
when_to_use: when the user asks 'how are you doing' or 'what's your status' or you need a quick health check
falsifiable_if: the reported status contradicts the actual backend/tool state
applies_to:
  - health-check
  - status-report
  - diagnostics
allowed_tools:
  - llm_generate
summary: System-Status
tags:
  - procedure
  - procedures
---

# System-Status

## When to Run This

Run this when the user asks for a status check or when you need to verify the system is healthy. This procedure gathers key signals and formats them into a concise report.

## What This Procedure Checks

1. **Backend**: Is the backend process running? (Check via tool availability — if tools respond, backend is up.)
2. **Tools**: Are all 7 core tools live? (vault_research, vault_search, code_read, vault_safe_write, vault_append, plan_task/update_task, execute_procedure)
3. **Autonomous researcher**: Is the background researcher running? (Check system state in identity.)
4. **Knowledge gaps**: How many dangling links / thin notes remain?
5. **Recent activity**: What was the last chat session or note written?

## Steps

### Step 1: Gather signals

1. [code: Print a summary of current system state — backend status, tool count, gap count, recent notes count]

### Step 2: Format report

2. [llm: Format the gathered signals into a concise status report. Include: backend status, tools live, researcher state, gap count, last activity. Keep it under 5 lines.]

## Related

- [[Vault-Gaps]] — procedure for checking knowledge gaps
- [[Backend-Restart]] — procedure for restarting the backend