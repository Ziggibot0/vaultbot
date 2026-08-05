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

### Step 2: Small model analyzes failure patterns

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
            # Categorize error
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

        prompt = f"""Analyze these procedure failure patterns:

Failures by procedure: {dict(proc_counts.most_common(10))}
Failure types: {dict(error_types)}

Sample entries:
{json.dumps(entries[:10], indent=2)}

Return JSON: {{"worst_procedures": ["procedures that fail most"], "common_causes": ["most frequent failure causes"], "fix_priorities": [{{"procedure": "...", "fix": "what to fix"}}]}}
Return ONLY the JSON."""
    analysis = llm_generate(prompt)
    result = analysis
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