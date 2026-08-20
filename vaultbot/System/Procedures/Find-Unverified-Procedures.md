---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-02
description: Check if a procedure's frontmatter status matches its actual reliability. Reads the procedure's success/fail counts from frontmatter and the failure log, and flags procedures where the status says verified but the success rate is below 70%, or where the status says experimental but the procedure has a high success rate and should be promoted. Use when auditing procedure trustworthiness.
when_to_use: when auditing procedure trustworthiness, when checking if verified procedures still work, when promoting good experimental procedures, or when asked 'which procedures can I trust'
falsifiable_if: the procedure misreports a procedure's reliability, or misses status-rate mismatches
applies_to:
  - procedure-audit
  - procedure-quality
  - trustworthiness
  - self-improvement
allowed_tools:
  - vault_list
  - llm_generate
summary: "# Find-Unverified-Procedures"
tags:
  - procedure
  - procedures
---

# Find-Unverified-Procedures

## When to Run This

Run this to audit procedure trustworthiness. Catches procedures that say
"verified" but actually fail, and procedures that say "experimental" but
have a great track record and should be promoted.

## Why This Exists

A procedure's `status` field can drift from its actual reliability as
success/failure counts accumulate. This procedure flags status-rate
mismatches — "verified" procedures that fail, "experimental" ones that
should be promoted. The tradeoff: it requires at least 5 runs before
judging, so new procedures aren't audited.

## Steps

### Step 1: Read all procedure frontmatter and the failure log

1. ```python
import json, re

proc_dir = Path(vault_path) / "System" / "Procedures"
procedures = []
for p in proc_dir.glob("*.md"):
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    if not text.startswith("---"):
        continue
    end = text.find("---", 3)
    if end == -1:
        continue
    fm = text[3:end]
    if "type: procedure" not in fm:
        continue
    info = {"name": p.stem}
    for line in fm.split("\n"):
        for key in ("status", "success_count", "failure_count", "success_rate"):
            if line.strip().startswith(f"{key}:"):
                val = line.split(":", 1)[1].strip()
                if key in ("success_count", "failure_count"):
                    try:
                        info[key] = int(val)
                    except ValueError:
                        info[key] = 0
                elif key == "success_rate":
                    try:
                        info[key] = float(val)
                    except ValueError:
                        info[key] = 0.0
                else:
                    info[key] = val
    procedures.append(info)

# Read failure log if it exists
failure_log_path = Path(vault_path) / "vaultbot_backend" / "procedure_failure_log.json"
failure_log = {}
if failure_log_path.exists():
    try:
        failure_log = json.loads(failure_log_path.read_text(encoding="utf-8"))
    except Exception:
        failure_log = {}

result = json.dumps({"procedures": procedures, "failure_log_entries": len(failure_log)})
```

### Step 2: Flag status-rate mismatches

2. ```python
import json as _json

data = _json.loads(output)
procedures = data.get("procedures", [])
mismatches = []

for proc in procedures:
    status = proc.get("status", "experimental")
    success_count = proc.get("success_count", 0)
    failure_count = proc.get("failure_count", 0)
    success_rate = proc.get("success_rate", 0.0)
    total = success_count + failure_count

    if total < 5:
        continue  # not enough data

    if status == "verified" and success_rate < 0.70:
        mismatches.append({
            "procedure": proc["name"],
            "issue": "verified but success rate below 70%",
            "success_rate": success_rate,
            "total_runs": total,
            "recommendation": "demote to experimental or flagged",
        })
    elif status == "experimental" and success_rate >= 0.85 and total >= 10:
        mismatches.append({
            "procedure": proc["name"],
            "issue": "experimental but high success rate — should be promoted",
            "success_rate": success_rate,
            "total_runs": total,
            "recommendation": "promote to verified",
        })
    elif status == "flagged" and success_rate >= 0.70:
        mismatches.append({
            "procedure": proc["name"],
            "issue": "flagged but success rate recovered",
            "success_rate": success_rate,
            "total_runs": total,
            "recommendation": "review and un-flag if fixed",
        })

result = _json.dumps({"mismatches": mismatches, "total_procedures": len(procedures)})
```

### Step 3: Return the audit

3. ```python
import json as _json

data = _json.loads(output)
mismatches = data.get("mismatches", [])
result = _json.dumps({
    "status_mismatches": mismatches,
    "total_mismatches": len(mismatches),
    "procedures_audited": data.get("total_procedures", 0),
})
```

## Related

- [[Procedure-Eval]] — evaluates procedure quality/trustworthiness
- [[Procedure-Library-Health]] — broader library health assessment
- [[Find-Redundant-Procedures]] — sibling procedure-library audit probe