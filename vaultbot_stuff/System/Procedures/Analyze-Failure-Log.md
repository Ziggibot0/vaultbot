---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-02
description: Read the procedure failure log and identify patterns — which procedures fail most, what types of failures are most common, and which procedures need fixing. Returns a summary of failure patterns with specific procedure names and failure reasons. Use when diagnosing why procedures fail or when improving procedure reliability.
when_to_use: when procedures are failing, when diagnosing failure patterns, when improving procedure reliability, or when asked 'why do procedures keep failing'
falsifiable_if: the failure analysis is incorrect, or the patterns don't match the actual failure log
applies_to:
  - procedure-audit
  - failure-analysis
  - procedure-quality
  - self-improvement
allowed_tools:
  - code_read
  - llm_generate
summary: Analyze-Failure-Log
tags:
  - procedure
  - procedures
---

# Analyze-Failure-Log

## When to Run This

When procedures keep failing and you want to know why. Reads the failure
log, identifies patterns, and points to specific procedures that need
fixing.

## Steps

### Step 1: Read the failure log

1. ```python
import json

log_path = Path(vault_path) / "vaultbot_stuff" / "vaultbot_backend" / "procedure_failure_log.json"
if not log_path.exists():
    result = json.dumps({"error": "no failure log found"})
else:
    try:
        log_data = json.loads(log_path.read_text(encoding="utf-8"))
    except Exception as e:
        result = json.dumps({"error": f"could not parse failure log: {e}"})
    else:
        # Normalize: could be a list or a dict
        if isinstance(log_data, dict):
            entries = []
            for proc_name, failures in log_data.items():
                if isinstance(failures, list):
                    for f in failures:
                        if isinstance(f, dict):
                            entries.append({"procedure": proc_name, **f})
                        else:
                            entries.append({"procedure": proc_name, "error": str(f)})
                else:
                    entries.append({"procedure": proc_name, "data": str(failures)[:200]})
        elif isinstance(log_data, list):
            entries = log_data
        else:
            entries = []
        result = json.dumps({"entries": entries[:50], "total_entries": len(entries)})
```

### Step 2: Small model analyzes failure patterns (split into 3 focused calls)

2. ```python
import json as _json
from collections import Counter

data = _json.loads(output)
if "error" in data:
    result = data
else:
    entries = data.get("entries", [])
    if not entries:
        result = _json.dumps({"patterns": [], "note": "no failures logged"})
    else:
        # Count failures by procedure
        proc_counts = Counter(e.get("procedure", "unknown") for e in entries)
        # Count failure types
        error_types = Counter()
        for e in entries:
            error = e.get("error", e.get("reason", e.get("failed_step", "unknown")))
            error_str = str(error).lower()
            if "import" in error_str or "module" in error_str:
                error_types["import_error"] += 1
            elif "name" in error_str or "args" in error_str:
                error_types["name_error"] += 1
            elif "timeout" in error_str:
                error_types["timeout"] += 1
            elif "syntax" in error_str:
                error_types["syntax_error"] += 1
            elif "tool" in error_str:
                error_types["tool_error"] += 1
            else:
                error_types["other"] += 1

        # --- Call A: Identify worst procedures (tiny input) ---
        top5 = dict(proc_counts.most_common(5))
        prompt_a = f"Given these procedure failure counts, return the top 3 worst procedure names as a JSON array of strings. Counts: {top5}. Return ONLY the JSON array."
        worst_raw = llm_generate(prompt_a)
        try:
            start = worst_raw.find("[")
            end = worst_raw.rfind("]")
            worst_procedures = _json.loads(worst_raw[start:end+1]) if start != -1 else list(top5.keys())[:3]
        except Exception:
            worst_procedures = list(top5.keys())[:3]

        # --- Call B: Identify common causes (tiny input) ---
        prompt_b = f"Given these error type counts, return the top 3 most common causes as a JSON array of strings. Counts: {dict(error_types)}. Return ONLY the JSON array."
        causes_raw = llm_generate(prompt_b)
        try:
            start = causes_raw.find("[")
            end = causes_raw.rfind("]")
            common_causes = _json.loads(causes_raw[start:end+1]) if start != -1 else list(error_types.keys())[:3]
        except Exception:
            common_causes = list(error_types.keys())[:3]

        # --- Call C: Fix priorities for worst procedure only (focused input) ---
        worst_proc = worst_procedures[0] if worst_procedures else "unknown"
        worst_entries = [e for e in entries if e.get("procedure") == worst_proc][:5]
        prompt_c = f"Given these failure entries for procedure '{worst_proc}', what is the main failure pattern and what should be fixed? Return JSON: {{\"procedure\": \"{worst_proc}\", \"fix\": \"what to fix\"}}. Entries: {_json.dumps(worst_entries)}. Return ONLY the JSON."
        fix_raw = llm_generate(prompt_c)
        try:
            start = fix_raw.find("{")
            end = fix_raw.rfind("}")
            fix_priority = _json.loads(fix_raw[start:end+1]) if start != -1 else {"procedure": worst_proc, "fix": "unknown"}
        except Exception:
            fix_priority = {"procedure": worst_proc, "fix": "unknown"}

        # --- Merge results in Python (no LLM needed) ---
        result = _json.dumps({
            "worst_procedures": worst_procedures,
            "common_causes": common_causes,
            "fix_priorities": [fix_priority]
        })
```

### Step 3: Return the failure analysis

3. ```python
import json as _json
try:
    start = output.find("{")
    end = output.rfind("}")
    parsed = _json.loads(output[start:end+1]) if start != -1 else {}
except Exception:
    parsed = {"worst_procedures": [], "common_causes": []}
result = _json.dumps(parsed)
```