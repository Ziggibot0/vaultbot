---
type: procedure
status: active
model_cartridge: small
created: 2026-08-01
description: Score and evaluate every procedure's health over time. Reads each procedure's frontmatter stats (success_count, failure_count, success_rate, last_reviewed, status) plus the raw step-gate failure log, classifies each as healthy/degraded/broken/untested, flags recurring failing steps, and recommends which procedures need review, demotion to small cartridge, or retirement. Called by Dream-Pass each cycle per the operator's directive that the framework self-scores procedures.
when_to_use: when asked how the procedures are performing, which procedures are failing or need improvement, or during a dream/consolidation pass to self-score the procedure library
falsifiable_if: it reports a procedure as healthy that the failure log shows failing, or misses a procedure with a low success_rate
applies_to:
  - procedures
  - self-improvement
  - evaluation
  - meta-procedure
allowed_tools:
  - vault_list
summary: Procedure-Eval
tags:
  - procedure
  - procedures
---

# Procedure-Eval

## When to Run This

Run to evaluate the procedure library's health. The operator's directive:
*"the vaultbot framework should handle procedure scoring and evaluation
over time to see if changes are needed — this should be its own
procedure, and the dream pass should call it."* This is that procedure.
[[Dream-Pass]] calls it each cycle; it can also run standalone. It reads
the deterministic counters the framework already maintains (frontmatter
stats + `procedure_failure_log.json`) and classifies — the small model
only formats/labels, it does not judge from scratch.

## Data sources

- **Per-procedure frontmatter:** `success_count`, `failure_count`,
  `success_rate`, `last_reviewed`, `review_interval_days`, `status`,
  `model_cartridge`, `description`.
- **Raw step-gate log:** `vaultbot_backend/procedure_failure_log.json`
  (`entries[]` with `procedure`, `step_number`, `validation_result`).

## Steps

### Step 1: Collect frontmatter stats for every procedure and the raw failure log

1. ```python
import os, json, datetime

vault = str(Path(vault_path).resolve())
proc_dir = Path(vault) / "vaultbot_stuff" / "System" / "Procedures"
log_file = Path(vault) / "vaultbot_stuff" / "vaultbot_backend" / "procedure_failure_log.json"

def parse_fm(text):
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm = {}
    for line in text[3:end].split("\n"):
        line = line.strip()
        if not line or line.startswith("- "):
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm

procs = []
if proc_dir.is_dir():
    for md in sorted(proc_dir.glob("*.md")):
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        fm = parse_fm(text)
        if fm.get("type", "").lower() != "procedure":
            continue
        def _f(key):
            try:
                return float(fm.get(key, 0))
            except (TypeError, ValueError):
                return 0.0
        procs.append({
            "name": md.stem,
            "status": fm.get("status", ""),
            "cartridge": fm.get("model_cartridge", "big"),
            "success_count": int(_f("success_count")),
            "failure_count": int(_f("failure_count")),
            "success_rate": _f("success_rate"),
            "last_reviewed": fm.get("last_reviewed", ""),
            "review_interval_days": int(_f("review_interval_days")),
            "has_when_to_use": bool(fm.get("when_to_use") or fm.get("when")),
        })

# Raw step-gate log: find the worst failing (procedure, step) pairs
failing_steps = {}
if log_file.exists():
    try:
        log = json.loads(log_file.read_text(encoding="utf-8"))
        for e in log.get("entries", []):
            if e.get("validation_result") == "fail":
                key = (e.get("procedure", "?"), e.get("step_number"))
                failing_steps[key] = failing_steps.get(key, 0) + 1
    except Exception:
        pass
worst_steps = sorted(
    ({"procedure": k[0], "step": k[1], "failures": v} for k, v in failing_steps.items()),
    key=lambda x: -x["failures"])[:15]

# Classify each procedure
now = datetime.date.today()
def classify(p):
    total = p["success_count"] + p["failure_count"]
    if total == 0:
        return "untested"
    rate = p["success_rate"] if p["success_rate"] else (p["success_count"] / total)
    if rate >= 0.85:
        return "healthy"
    if rate >= 0.6:
        return "degraded"
    return "broken"

for p in procs:
    p["health"] = classify(p)
    # due for review?
    due = ""
    if p["last_reviewed"] and p["review_interval_days"]:
        try:
            lr = datetime.date.fromisoformat(p["last_reviewed"])
            if (now - lr).days >= p["review_interval_days"]:
                due = "overdue"
        except ValueError:
            pass
    elif not p["last_reviewed"]:
        due = "never-reviewed"
    p["review_status"] = due

summary = {
    "total": len(procs),
    "healthy": sum(1 for p in procs if p["health"] == "healthy"),
    "degraded": sum(1 for p in procs if p["health"] == "degraded"),
    "broken": sum(1 for p in procs if p["health"] == "broken"),
    "untested": sum(1 for p in procs if p["health"] == "untested"),
    "on_small_cartridge": sum(1 for p in procs if p["cartridge"] == "small"),
    "on_big_cartridge": sum(1 for p in procs if p["cartridge"] == "big"),
    "missing_when_to_use": [p["name"] for p in procs if not p["has_when_to_use"]],
    "review_overdue": [p["name"] for p in procs if p["review_status"] in ("overdue", "never-reviewed")],
}
problem_procs = [
    {"name": p["name"], "health": p["health"], "success_rate": p["success_rate"],
     "failures": p["failure_count"], "cartridge": p["cartridge"]}
    for p in procs if p["health"] in ("degraded", "broken")
]

result = json.dumps({
    "summary": summary,
    "problem_procedures": problem_procs,
    "worst_failing_steps": worst_steps,
    "all": [{"name": p["name"], "health": p["health"],
             "rate": p["success_rate"], "cartridge": p["cartridge"]} for p in procs],
})
```

### Step 2: Classify procedure health and recommend actions

2. [llm: Report the procedure library health from the prior step output. Lead with the summary: total procedures, healthy/degraded/broken/untested counts, and how many run on the cheap small cartridge vs the big one. For each problem procedure (degraded/broken), name it, its success rate and failure count, whether its failing step is in worst_failing_steps, and recommend ONE action: REVIEW the failing step, DEMOTE to small cartridge (if it's a bounded task on big), or RETIRE. Flag procedures missing when_to_use (they can't be discovered) and procedures overdue for review. If everything is healthy, say so and note the cartridge split.]

### Step 3: Validate

3. [validate: contains "procedure"]
