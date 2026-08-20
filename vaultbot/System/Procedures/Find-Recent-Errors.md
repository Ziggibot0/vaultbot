---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-08-05
description: "Scan the N most recent VaultBot session logs and return every failure across them: exceptions, console errors, failed tool results, and procedure-step failures. Broad-net self-diagnosis — finds problems the operator never reported because they happened silently in a background task or a session the operator isn't looking at. Use when the user asks 'what went wrong', 'any errors recently', 'why did you stop', 'did something break', or when the system feels off. Read-only — never modifies session files."
when_to_use: when asked 'what went wrong', 'any errors recently', 'why did you stop', 'did something break', 'what went wrong recently', when the system feels broken but you don't know which session, as the first diagnostic step before calling a repair procedure, when doing a proactive health sweep
falsifiable_if: it reports an error that isn't actually in any session JSONL (verifiable by grepping the cited files), or it misses an error present in the scanned files
applies_to:
  - troubleshooting
  - self-diagnosis
  - error-detection
  - health-monitoring
allowed_tools:
  - code_run
summary: "SUMMARY|# Find-Recent-Errors: Scans recent JSONL files for console_error and exception log_errors to identify system failures grouped by session.
##tags||,session_log_analysis,error_detection,health_c"
tags:
  - procedure
  - procedures
  - troubleshooting
  - errors
  - sessions
---

# Find-Recent-Errors

Scans the most-recent session JSONL files and pulls out every failure
signal: `console_error`, `exception`/`log_exception`, tool results
carrying an `error` key, and `procedure_step` failures. Returns them
grouped by session so you can see exactly what went wrong and where.

Complements [[Analyze-Session-Log]]: that one deep-dives a single
session; this one sweeps across many to find which session had the
problem.

Read-only — never modifies session files.

## When to Run This

- "What went wrong recently?" / "Any errors lately?"
- The system feels off but you don't know which session to look at
- First diagnostic step before calling a repair procedure
- Proactive health sweep

## Why This Exists

Failures often happen silently in a background task or a session the operator isn't looking at, so they never get reported. This procedure exists to sweep the N most recent session logs and return every failure signal across them. The key tradeoff is that it is read-only — it never modifies session files — and it complements [[Analyze-Session-Log]], which deep-dives a single session.

## Inputs

- `count` (optional): how many recent sessions to scan. Default `10`.
- `errors_only` (optional): if `true`, only return sessions that had at
  least one error. Default `true`.

## Steps

### Step 1: Scan recent session logs for failure signals

1. ```python
   import json
   from pathlib import Path

   sessions_dir = Path(vault_path) / "vaultbot" / "vaultbot_backend" / "sessions"
   count = int(args.get("count", 10))
   errors_only = str(args.get("errors_only", "true")).lower() == "true"

   if not sessions_dir.exists():
       result = f"ERROR: sessions dir not found: {sessions_dir}"
   else:
       files = sorted(sessions_dir.glob("*.jsonl"),
                      key=lambda f: f.stat().st_mtime, reverse=True)[:count]
       sessions_out = []
       for f in files:
           errs = []
           title = "New Session"
           started = ""
           try:
               lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
           except OSError:
               continue
           for line in lines:
               try:
                   evt = json.loads(line)
               except json.JSONDecodeError:
                   continue
               ev = evt.get("event", "")
               data = evt.get("data") or {}
               ts = evt.get("timestamp")
               if ev == "session_title":
                   title = evt.get("title", title)
               elif ev == "session_start":
                   started = evt.get("started_at", started)
               elif ev in ("console_error", "notify_console_failure"):
                   errs.append({"kind": "console_error",
                                "message": (data.get("message")
                                            or json.dumps(data, default=str))[:200],
                                "timestamp": ts})
               elif ev == "log_exception" or ev == "exception" or "exception" in ev.lower():
                   errs.append({"kind": "exception",
                                "event": ev,
                                "message": (data.get("message")
                                             or data.get("error")
                                             or json.dumps(data, default=str))[:200],
                                "timestamp": ts})
               elif ev == "tool_result":
                   if data.get("error"):
                       name = data.get("name") or "?"
                       errs.append({"kind": "tool_error",
                                    "tool": name,
                                    "message": str(data.get("error"))[:200],
                                    "timestamp": ts})
               elif ev == "procedure_step":
                   if data.get("status") == "failed" or data.get("failed"):
                       pname = data.get("procedure") or "?"
                       step = data.get("step")
                       errs.append({"kind": "procedure_step_failed",
                                    "procedure": pname,
                                    "step": step,
                                    "message": str(data.get("error")
                                                   or data.get("detail")
                                                   or "step failed")[:200],
                                    "timestamp": ts})
           if errs or not errors_only:
               sessions_out.append({
                   "session_id": f.stem,
                   "title": title,
                   "started_at": started,
                   "error_count": len(errs),
                   "errors": errs,
               })

       out = []
       out.append(f"Scanned {len(files)} sessions. "
                  f"{sum(1 for s in sessions_out if s['error_count'])} had errors. "
                  f"Total errors: {sum(s['error_count'] for s in sessions_out)}.")
       out.append("")
       for s in sessions_out:
           out.append(f"### {s['title']}  ({s['session_id'][:8]}...)  "
                      f"started {s['started_at']}  "
                      f"  errors: {s['error_count']}")
           for e in s["errors"]:
               out.append(f"  - [{e['kind']}] {e.get('message','')}")
           out.append("")
       result = "\n".join(out)
   ```

## Related

- [[Analyze-Session-Log]] — deep-dives a single session this sweep identifies
- [[Diagnose-System-Health]] — endpoint-level health check, complementary
- [[Analyze-Failure-Log]] — analyzes a specific failure log