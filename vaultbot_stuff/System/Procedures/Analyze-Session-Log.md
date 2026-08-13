---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-08-05
description: "Read and summarize any VaultBot chat session from its JSONL log. Finds a session by UUID, title (substring match), or 'latest', then extracts: title, start time, token totals, every user/assistant turn, every tool call with its result, every exception/console_error, and thinking-block count. Single entry point for looking at what happened in any past session — for both VaultBot self-diagnosis and operator troubleshooting. Use when Sean asks 'show me the last chat', 'what were you doing earlier', 'why did you crash', 'what happened in the last session', 'the session about X', or when resuming context after a restart. Read-only — never modifies a session file."
when_to_use: when asked 'what happened in the last session', 'show me the last chat', 'what were you doing earlier', 'why did you crash', 'the session about X', 'what did you do earlier', 'what were we doing last time', when resuming context after a restart, when investigating a reported failure, when asked about any specific past session by title or UUID
falsifiable_if: the session it finds does not match the requested title/id, or it reports an event that isn't actually in the JSONL file (verifiable by reading the raw file)
applies_to:
  - session-history
  - troubleshooting
  - self-diagnosis
  - log-analysis
  - context-recovery
allowed_tools:
  - code_run
summary: Analyze-Session-Log|read-only, tool calls + results, exceptions, console errors | jsonL log processing
tags:
  - procedure
  - procedures
  - troubleshooting
  - logs
  - sessions
---

# Analyze-Session-Log

Reads a VaultBot session's JSONL log and returns a structured summary of
everything that happened: title, turns, tool calls + results, exceptions,
console errors, and thinking. Sessions live in
`vaultbot_stuff/vaultbot_backend/sessions/*.jsonl` — one file per chat
session, named by UUID, append-only.

Read-only — never modifies a session file.

## When to Run This

- "What happened in the last session" / "the session about X"
- Recover context after a restart
- Diagnose a failure: see the exact tool calls + errors
- Composing a larger diagnosis (e.g. [[Find-Recent-Errors]] calls this)

## Inputs

- `session` (optional): a UUID, a title substring, or `"latest"`. Default `"latest"`.

## Steps

1. ```python
   import json
   from pathlib import Path

   backend_dir = Path(vault_path) / "vaultbot_stuff" / "vaultbot_backend"
   sessions_dir = backend_dir / "sessions"

   session_arg = (args.get("session") or "latest").strip()

   if not sessions_dir.exists():
       result = f"ERROR: sessions dir not found: {sessions_dir}"
   else:
       target = None
       if session_arg == "latest" or not session_arg:
           files = sorted(sessions_dir.glob("*.jsonl"),
                          key=lambda f: f.stat().st_mtime, reverse=True)
           target = files[0] if files else None
       elif len(session_arg) == 36 and session_arg.count("-") == 4:
           cand = sessions_dir / f"{session_arg}.jsonl"
           target = cand if cand.exists() else None
       else:
           matches = []
           for f in sessions_dir.glob("*.jsonl"):
               try:
                   for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                       try:
                           evt = json.loads(line)
                       except json.JSONDecodeError:
                           continue
                       if evt.get("event") == "session_title":
                           title = evt.get("title", "")
                           if session_arg.lower() in title.lower():
                               matches.append((f.stat().st_mtime, f, title))
                           break
               except OSError:
                   continue
           if matches:
               matches.sort(key=lambda m: m[0], reverse=True)
               target = matches[0][1]

       if target is None:
           result = f"ERROR: no session found for: {session_arg!r}"
       else:
           lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
           title = "New Session"
           started_at = ""
           token_totals = {"prompt_tokens": 0, "completion_tokens": 0}
           turns = []
           tool_calls = []
           exceptions = []
           console_errors = []
           thinking_count = 0
           current_assistant = ""

           for line in lines:
               try:
                   evt = json.loads(line)
               except json.JSONDecodeError:
                   continue
               ev = evt.get("event", "")
               data = evt.get("data") or {}
               if ev == "session_start":
                   started_at = evt.get("started_at", "")
               elif ev == "session_title":
                   title = evt.get("title", title)
               elif ev == "token_usage":
                   token_totals["prompt_tokens"] += data.get("prompt_tokens", 0)
                   token_totals["completion_tokens"] += data.get("completion_tokens", 0)
               elif ev == "websocket_message":
                   direction = data.get("direction")
                   payload = data.get("payload") or {}
                   if direction == "in":
                       if current_assistant:
                           turns.append({"role": "assistant", "p": current_assistant[:300]})
                           current_assistant = ""
                       msg = payload.get("message") or ""
                       if msg:
                           turns.append({"role": "user", "p": msg[:300]})
                   elif direction == "out":
                       ptype = payload.get("type")
                       if ptype == "answer_chunk":
                           current_assistant += payload.get("content") or ""
                       elif ptype == "answer_done":
                           if current_assistant:
                               turns.append({"role": "assistant", "p": current_assistant[:300]})
                               current_assistant = ""
                       elif ptype == "thinking":
                           thinking_count += 1
               elif ev == "tool_call":
                   name = data.get("name") or data.get("tool") or "?"
                   a = json.dumps(data.get("args") or data.get("arguments") or {},
                                  default=str)[:150]
                   tool_calls.append({"name": name, "args": a, "res": None, "err": None})
               elif ev == "tool_result":
                   tname = data.get("name") or ""
                   err = data.get("error")
                   r = json.dumps(data.get("result") or data.get("output") or "",
                                  default=str)[:250]
                   for tc in reversed(tool_calls):
                       if tc["res"] is None and (not tname or tc["name"] == tname):
                           tc["res"] = r
                           tc["err"] = err
                           break
               elif ev in ("console_error", "notify_console_failure"):
                   console_errors.append((data.get("message")
                                          or json.dumps(data, default=str))[:200])
               elif ev == "log_exception" or ev == "exception" or "exception" in ev.lower():
                   exceptions.append({"ev": ev,
                                      "m": (data.get("message") or data.get("error")
                                            or json.dumps(data, default=str))[:200]})

           if current_assistant:
               turns.append({"role": "assistant", "p": current_assistant[:300]})

           out = []
           out.append(f"SESSION: {title}")
           out.append(f"ID: {target.stem}")
           out.append(f"Started: {started_at}")
           out.append(f"Events: {len(lines)}  Turns: {len(turns)}  "
                      f"Tool calls: {len(tool_calls)}  "
                      f"Exceptions: {len(exceptions)}  "
                      f"Console errors: {len(console_errors)}  "
                      f"Thinking events: {thinking_count}")
           out.append(f"Tokens: prompt={token_totals['prompt_tokens']} "
                       f"completion={token_totals['completion_tokens']}")
           out.append("")
           out.append("== TURNS ==")
           for i, t in enumerate(turns):
               role = t["role"].upper()
               preview = (t["p"] or "").replace("\n", " ")[:200]
               out.append(f"[{i+1}] {role}: {preview}")
           out.append("")
           out.append("== TOOL CALLS ==")
           for i, tc in enumerate(tool_calls):
               tag = " [ERROR]" if tc["err"] else ""
               out.append(f"[{i+1}] {tc['name']}{tag} args={tc['args']} -> {tc['res']}")
           if exceptions:
               out.append("")
               out.append("== EXCEPTIONS ==")
               for e in exceptions:
                   out.append(f"- ({e['ev']}) {e['m']}")
           if console_errors:
               out.append("")
               out.append("== CONSOLE ERRORS ==")
               for e in console_errors:
                   out.append(f"- {e}")
           result = "\n".join(out)
   ```