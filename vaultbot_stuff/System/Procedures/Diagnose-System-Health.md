---
type: procedure
status: active
model_cartridge: small
created: 2026-08-05
description: "Run the VaultBot's proactive health battery by calling the backend's /diagnose, /health, /system/stats, and /ollama/stats endpoints. Returns a single consolidated health report: is the backend up, is the LLM backend reachable, is the configured model available, is the FAISS index healthy, are there config conflicts, plus live CPU/RAM/GPU stats. Fastest way to answer 'is everything okay?' Read-only — calling these endpoints has no side effects."
when_to_use: "when asked 'are you healthy' / 'check your health' / 'is everything okay', at the start of any troubleshooting session, when something feels broken and you need a quick triage, before restarting to capture the pre-restart state"
falsifiable_if: "it reports healthy when /diagnose returns problems (verifiable by calling /diagnose directly), or it reports the backend down when /health responds 200"
applies_to:
  - health-check
  - self-diagnosis
  - troubleshooting
  - triage
allowed_tools:
  - code_run
summary: Diagnose-System-Health
tags:
  - procedure
  - procedures
  - troubleshooting
  - health
  - diagnosis
---

# Diagnose-System-Health

Calls the backend's existing health endpoints and consolidates the
answers into one report:

- `GET /health` — backend alive + index loaded + watcher running
- `GET /diagnose` — proactive check battery (Ollama reachable, model
  available, vault not in a sync folder, FAISS index, config conflicts)
- `GET /system/stats` — live CPU/RAM/GPU/NPU/disk/net
- `GET /ollama/stats` — Ollama-loaded models + counts

Read-only — calling these endpoints has no side effects.

## When to Run This

- "Are you healthy?" / "Check your health" / "Is everything okay?"
- Start of any troubleshooting session (triage before deep-diving)
- Before a restart, to capture the pre-restart state
- When something feels broken and you need a quick read

## Inputs

None.

## Steps

1. ```python
   import json
   import urllib.request

   BASE = "http://127.0.0.1:8000"

   def _get(path, timeout=5):
       try:
           req = urllib.request.Request(f"{BASE}{path}", method="GET")
           with urllib.request.urlopen(req, timeout=timeout) as r:
               return json.loads(r.read().decode("utf-8"))
       except Exception as e:
           return {"_endpoint_error": str(e)[:200]}

   h = _get("/health")
   d = _get("/diagnose")
   st = _get("/system/stats")
   ol = _get("/ollama/stats")

   lines = []
   if "_endpoint_error" in h:
       lines.append(f"BACKEND: DOWN ({h['_endpoint_error']})")
   else:
       lines.append("BACKEND: UP")
       for k in ("index_loaded", "watcher_running"):
           if k in h:
               lines.append(f"  {k}: {h[k]}")
       if "model" in h:
           lines.append(f"  model: {h['model']}")

   problems = d.get("problems", []) if isinstance(d, dict) else []
   if "_endpoint_error" in d:
       lines.append(f"DIAGNOSE: UNAVAILABLE ({d['_endpoint_error']})")
   elif problems:
       lines.append(f"DIAGNOSE: {len(problems)} PROBLEM(S)")
       for p in problems:
           cat = p.get("category", "?")
           msg = p.get("user_message", p.get("message", ""))
           lines.append(f"  - [{cat}] {msg}")
   else:
       lines.append("DIAGNOSE: ALL CHECKS PASSED")

   if "_endpoint_error" in ol:
       lines.append(f"OLLAMA: UNREACHABLE ({ol['_endpoint_error']})")
   else:
       host = ol.get("host") or ol.get("base_url") or ""
       models = ol.get("models") or ol.get("loaded_models") or []
       lines.append(f"OLLAMA: {host or '(default)'}")
       if models:
           for m in models[:10]:
               if isinstance(m, dict):
                   nm = m.get("name") or m.get("model") or "?"
                   sz = m.get("size") or m.get("size_gb") or ""
                   lines.append(f"  - {nm} {sz}")
               else:
                   lines.append(f"  - {m}")
           if len(models) > 10:
               lines.append(f"  ... and {len(models)-10} more")

   if "_endpoint_error" in st:
       lines.append(f"SYSTEM STATS: UNAVAILABLE ({st['_endpoint_error']})")
   else:
       cpu = st.get("cpu", {})
       ram = st.get("ram", {})
       gpu = st.get("gpu")
       if cpu:
           lines.append(f"CPU: {cpu.get('percent', '?')}% "
                        f"({cpu.get('cores', '?')} cores)")
       if ram:
           lines.append(f"RAM: {ram.get('percent', '?')}% "
                        f"({ram.get('used_gb', '?')}/{ram.get('total_gb', '?')} GB)")
       if gpu:
           lines.append(f"GPU: {gpu.get('name','?')} {gpu.get('memory_percent','?')}%")

   result = "\n".join(lines)
   ```