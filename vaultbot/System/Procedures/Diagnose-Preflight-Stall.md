---
type: procedure
status: verified
baseline: true
model_cartridge: small
created: 2026-08-14
description: "Diagnose why the preflight (Think + Route-Task) is slow or appears hung. Reads the latest session log, computes per-step durations for Think and Route-Task, and checks whether asyncio.wait_for timeouts are actually firing or being silently defeated by event-loop-blocking subprocess calls. Use when the GUI shows a procedure step (e.g. 'Route-Task step 2/2') stuck for minutes, when a user message takes 3+ minutes before the first response token, or when the watchdog reports a stale heartbeat during preflight."
when_to_use: when the GUI is stuck on a procedure step for a long time, when a user message takes minutes before any response, when the watchdog reports the backend is hung, when Think or Route-Task seems to take forever, when preflight timeouts don't seem to work
falsifiable_if: it reports a step duration that doesn't match the actual timestamps in the JSONL log (verifiable by reading the raw log), or it reports timeouts are working when the log shows they fired long after the configured timeout
applies_to:
  - preflight-stall
  - event-loop-blocking
  - timeout-failure
  - procedure-slowness
  - troubleshooting
  - self-diagnosis
  - latency
allowed_tools:
  - code_run
summary: Diagnose preflight stalls by computing per-step durations from session logs and checking if asyncio.wait_for timeouts are defeated by event-loop-blocking subprocess calls.
tags:
  - procedure
  - procedures
  - troubleshooting
  - preflight
  - timeout
  - event-loop
  - latency
success_count: 7
failure_count: 1
success_rate: 0.88
---

# Diagnose-Preflight-Stall

When the GUI shows a procedure step stuck for minutes (e.g.
"Route-Task step 2/2"), the cause is almost never the visible procedure
— it's the **parallel** preflight procedure (Think) blocking the event
loop with synchronous subprocess calls, which defeats the
`asyncio.wait_for` timeout.

## Root Cause Pattern

The preflight runs Think and Route-Task in parallel via
`asyncio.gather`. Think has a timeout (`VAULTBOT_THINK_TIMEOUT_S`,
default 15s). But `execute_procedure` calls `_run_code_step`, which is a
**synchronous** `subprocess.run()`. When the subprocess calls
`run_procedure()` (shelling out to other lens procedures), each is
another full subprocess chain. The event loop is blocked inside
`subprocess.run()`, so `asyncio.wait_for`'s timeout callback **never
fires** — it can't interrupt a blocking call that's hogging the event
loop.

**Status (2026-08-14): FIXED.** All three step types in
`step_gate_runtime.py` now run via `asyncio.to_thread()`:
- Code steps: `await asyncio.to_thread(_run_code_step, ...)`
- LLM steps: `await asyncio.to_thread(_run_llm_step, ...)`
- Text steps: `await asyncio.to_thread(llm_client.chat, ...)`

The event loop is no longer blocked by subprocess or HTTP calls, so
`asyncio.wait_for` timeouts fire correctly. This procedure remains
useful for diagnosing *new* stalls that may arise from other causes.

## When to Run This

- GUI stuck on "Route-Task step 2/2" (or any procedure step) for minutes
- User message takes 3+ minutes before the first response token
- Watchdog reports "heartbeat stale — backend appears hung"
- Think or Route-Task step durations in the session log exceed the
  configured timeout
- After any change to step_gate_runtime.py or chat_handler.py preflight

## Inputs

- `session_id` (optional): UUID of the session to analyze. Defaults to
  the most recently modified session log.

## Steps

### Step 1: Find and read the latest session log

```python
import json, os, pathlib, glob

sessions_dir = pathlib.Path(vault_path) / "vaultbot" / "vaultbot_backend" / "sessions"
target_id = (args.get("session_id") or "").strip()

if target_id:
    log_file = sessions_dir / f"{target_id}.jsonl"
    if not log_file.exists():
        result = f"ERROR: session log not found: {target_id}"
    else:
        lines = log_file.read_text(encoding="utf-8", errors="replace").strip().split("\n")
        events = []
        for line in lines:
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
else:
    # Find the most recently modified session log
    log_files = sorted(sessions_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not log_files:
        result = "ERROR: no session logs found"
        events = []
    else:
        log_file = log_files[0]
        lines = log_file.read_text(encoding="utf-8", errors="replace").strip().split("\n")
        events = []
        for line in lines:
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

if not events:
    if not result:
        result = "ERROR: no parseable events in session log"
else:
    result = f"Loaded {len(events)} events from {log_file.name}"

# Persist events into prior_results so step 2 can access them.
# Each step runs in a separate subprocess with a fresh namespace;
# only prior_results (a dict) survives between steps.
prior_results["events"] = events
prior_results["log_file_name"] = log_file.name if log_file else "unknown"
```

### Step 2: Extract procedure step events and compute durations

```python
# Step 2 runs in a separate subprocess — recover events from prior_results.
events = prior_results.get("events", [])
log_file_name = prior_results.get("log_file_name", "unknown")

if not events:
    result = "ERROR: no events to analyze (step 1 failed)"
else:
    # Extract procedure_step websocket messages
    step_events = []
    for ev in events:
        data = ev.get("data", {})
        payload = data.get("payload", {}) if isinstance(data, dict) else {}
        if payload.get("type") == "procedure_step":
            step_events.append({
                "timestamp": ev.get("timestamp", 0),
                "procedure": payload.get("procedure", ""),
                "step": payload.get("step", 0),
                "total": payload.get("total", 0),
                "instruction": payload.get("instruction", ""),
                "step_type": payload.get("step_type", ""),
                "status": payload.get("status", ""),
            })

    # Extract timeout/error events
    timeout_events = []
    for ev in events:
        evt_type = ev.get("event", "")
        if "timeout" in evt_type or "stale" in evt_type or "error" in evt_type:
            timeout_events.append({
                "timestamp": ev.get("timestamp", 0),
                "event": evt_type,
                "data": ev.get("data", {}),
            })

    # Compute per-step durations
    # Pair "running" with "passed"/"failed" events
    step_durations = []
    pending = {}
    for se in step_events:
        key = (se["procedure"], se["step"])
        if se["status"] == "running":
            pending[key] = se
        elif se["status"] in ("passed", "failed"):
            if key in pending:
                start = pending.pop(key)["timestamp"]
                end = se["timestamp"]
                duration = end - start
                step_durations.append({
                    "procedure": se["procedure"],
                    "step": se["step"],
                    "total": se["total"],
                    "instruction": se["instruction"],
                    "step_type": se["step_type"],
                    "status": se["status"],
                    "duration_s": round(duration, 1),
                })

    # Build report
    report_lines = []
    report_lines.append("## Preflight Stall Diagnosis")
    report_lines.append("")
    report_lines.append(f"Session: {log_file_name}")
    report_lines.append(f"Total events: {len(events)}")
    report_lines.append(f"Procedure step events: {len(step_events)}")
    report_lines.append(f"Timeout/error events: {len(timeout_events)}")
    report_lines.append("")

    if step_durations:
        report_lines.append("### Per-Step Durations")
        report_lines.append("")
        report_lines.append("| Procedure | Step | Type | Duration (s) | Status | Instruction |")
        report_lines.append("|-----------|------|------|-------------|--------|--------------|")
        for sd in step_durations:
            report_lines.append(
                f"| {sd['procedure']} | {sd['step']}/{sd['total']} | {sd['step_type']} | "
                f"**{sd['duration_s']}** | {sd['status']} | {sd['instruction'][:60]} |"
            )
        report_lines.append("")

        # Flag slow steps
        slow_steps = [sd for sd in step_durations if sd["duration_s"] > 30]
        if slow_steps:
            report_lines.append("### ⚠️ Slow Steps (>30s)")
            report_lines.append("")
            for sd in slow_steps:
                report_lines.append(
                    f"- **{sd['procedure']} step {sd['step']}** ({sd['step_type']}): "
                    f"{sd['duration_s']}s — {sd['instruction']}"
                )
            report_lines.append("")

    # Check for timeout events
    if timeout_events:
        report_lines.append("### Timeout/Error Events")
        report_lines.append("")
        for te in timeout_events[:20]:
            report_lines.append(
                f"- {te['event']} at t={te['timestamp']:.1f}: {json.dumps(te['data'])[:200]}"
            )
        report_lines.append("")

    # Diagnose: did Think timeout actually fire?
    think_timeout_events = [te for te in timeout_events if "premise_gate_timeout" in te.get("event", "")]
    think_step_events = [se for se in step_events if se["procedure"] == "Think"]
    think_total = 0
    if think_step_events:
        first_ts = think_step_events[0]["timestamp"]
        last_ts = think_step_events[-1]["timestamp"]
        think_total = last_ts - first_ts

    think_timeout_s = 15.0  # default
    # Check env for configured timeout
    env_timeout = os.environ.get("VAULTBOT_THINK_TIMEOUT_S", "")
    if env_timeout:
        try:
            think_timeout_s = float(env_timeout)
        except ValueError:
            pass

    report_lines.append("### Diagnosis")
    report_lines.append("")
    if think_total > think_timeout_s and think_timeout_events:
        report_lines.append(
            f"**CONFIRMED: Event-loop blocking.** Think ran for {think_total:.0f}s "
            f"but the timeout is {think_timeout_s:.0f}s. The timeout fired at "
            f"t={think_timeout_events[0]['timestamp']:.1f} — {think_total - think_timeout_s:.0f}s "
            f"TOO LATE. The event loop was blocked by synchronous subprocess calls "
            f"in `_run_code_step`, preventing `asyncio.wait_for` from firing on time."
        )
        report_lines.append("")
        report_lines.append("**Note:** The `asyncio.to_thread()` fix is already applied in step_gate_runtime.py. If this still fires, the blocking is elsewhere.")
    elif think_total > think_timeout_s and not think_timeout_events:
        report_lines.append(
            f"**LIKELY: Event-loop blocking.** Think ran for {think_total:.0f}s "
            f"(timeout is {think_timeout_s:.0f}s) but no timeout event was logged. "
            f"The event loop was blocked and the timeout callback never fired."
        )
        report_lines.append("")
        report_lines.append("**Fix:** Ensure `_run_code_step` and `_run_llm_step` are wrapped in `asyncio.to_thread()` in `step_gate_runtime.py`.")
    elif think_total > 0:
        report_lines.append(
            f"Think ran for {think_total:.0f}s (timeout {think_timeout_s:.0f}s). "
            f"Timeout appears to be working correctly."
        )
    else:
        report_lines.append("No Think procedure events found in this session.")

    report_lines.append("")
    result = "\n".join(report_lines)
    print(result)
```

## Falsifiability

This procedure is falsifiable if:
- It reports a step duration that doesn't match the actual timestamps in
  the JSONL log (verifiable by reading the raw file)
- It reports timeouts are working when the log shows they fired long
  after the configured timeout
- It reports the wrong session when `session_id` is provided

## Related Procedures

- [[Analyze-Session-Log]] — general session log reader (this procedure
  specializes on preflight stall diagnosis)
- [[Diagnose-System-Health]] — overall health check (use first for
  triage, then this for preflight-specific stalls)