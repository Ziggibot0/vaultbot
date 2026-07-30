---
type: procedure
status: experimental
created: 2026-07-30
last_reviewed: 2026-07-30
review_interval_days: 30
success_count: 0
failure_count: 1
success_rate: 0.0
description: "Audit VaultBot across 5 categories: graph health, retrieval quality, note quality, tool safety, and system health. 5 deterministic steps that run real checks and report findings. Idempotent — safe to re-run."
falsifiable_if: "an audit following this procedure passes a category that Sean identifies as broken, or fails a category that Sean identifies as working"
applies_to:
  - self-evaluation
  - vault-health
  - system-reliability
  - knowledge-quality
depends_on:
  - "[[What-Makes-a-Good-Critique]]"
  - "[[Evaluate-Retrieval]]"
  - "[[Calibration-via-Operator-Feedback]]"
  - "[[Claim-Verification-for-Vault-Notes]]"
  - "[[Deterministic-Scaffolding-for-Small-Models]]"
sources:
  - "https://plot4.ai/library"
  - "https://arxiv.org/abs/2404.13781"
  - "https://arxiv.org/abs/2601.05264v1"
  - "https://arxiv.org/abs/2604.20496v1"
allowed_tools:
  - vault_graph_analyzer
  - vault_cluster_analyzer
  - vault_lint
  - vault_list
  - vault_gaps
  - vault_search
  - code_read
  - code_run
  - vaultbot_status
---

# Audit-VaultBot

## When to Run This

Run this procedure periodically (monthly) or when Sean asks "how are you doing?" or "audit yourself." This is structured self-critique — not a victory lap. The goal is to find real problems and fix them.

## Principles

1. Honest — report failures directly. No passing a category just because nothing crashed today.
2. Actionable — every finding includes what a fix would look like.
3. Major vs. minor triage — findings reported as Critical / Moderate / Minor.
4. Empathy with the creator — evaluate against Sean's goals, not abstract ML benchmarks.
5. Stay in your lane — each category checks what it can actually measure.

## Steps

### Step 1: Knowledge Graph Health

Run vault_graph_analyzer to check connectivity, islands, and orphaned notes.

1. ```python
import json

# The LLM calls vault_graph_analyzer() directly.
# This step documents what to check and how to interpret results.

result = json.dumps({
    "status": "check_required",
    "tool": "vault_graph_analyzer",
    "checks": [
        "Total islands — how many disconnected components?",
        "Connectivity ratio — what fraction of nodes are in the largest island?",
        "Isolated nodes — which notes have zero connections?",
        "Categorize orphans: chat logs (expected), research notes (fixable), system files (expected)",
    ],
    "pass_criteria": "Connectivity > 0.80. Research orphan count < 5. Chat log orphans are expected (not a failure).",
    "if_failing": {
        "critical": "Research notes isolated with no links — fix by adding wikilinks to related notes",
        "moderate": "Connectivity below 0.75 — run Dream-Pass to connect orphans",
        "minor": "Chat log orphans — expected, not a failure",
    },
    "action": "Call vault_graph_analyzer(). Record islands, connectivity, and isolated nodes.",
})
```

[validate: contains "check_required"]

### Step 2: Retrieval Quality

Run test queries and check if the right notes are being retrieved.

2. ```python
import json

result = json.dumps({
    "status": "check_required",
    "tool": "vault_search",
    "test_queries": [
        {"query": "autonomy directive", "expected": "Autonomy-Directive"},
        {"query": "fractal entropy principle", "expected": "Fractal-Entropy-Principle"},
        {"query": "how to write a procedure", "expected": "Procedure-Creator"},
        {"query": "claim verification", "expected": "Claim-Verification-for-Vault-Notes"},
        {"query": "context budgeting", "expected": "Context-Budgeting-for-Vault-Growth"},
    ],
    "checks": [
        "Does the expected note appear in top-5 results?",
        "Are there irrelevant results (noise)?",
        "Are stale index entries being returned? (files that don't exist)",
    ],
    "pass_criteria": "4/5 test queries return the expected note in top-5. No stale entries.",
    "if_failing": {
        "critical": "Stale entries in index — run index cleanup",
        "moderate": "Expected note missing from top-5 — check if note exists, is indexed, has links",
        "minor": "Some noise in results — adjust MIN_SCORE_THRESHOLD if needed",
    },
    "action": "Call vault_search for each test query with k=5. Check if expected notes appear.",
})
```

### Step 3: Note Quality

Sample 5 random notes and run vault_lint on each.

3. ```python
import json

result = json.dumps({
    "status": "check_required",
    "tool": "vault_lint",
    "checks": [
        "Does the note have frontmatter (type, created, tags, summary)?",
        "Does it have wikilinks to other notes?",
        "Does it contain reasoning language (because, therefore, which means)?",
        "Are there broken wikilinks?",
        "Is it self-contained — can a reader understand it without other notes?",
    ],
    "pass_criteria": "4/5 sampled notes pass vault_lint with 0 real issues and contain reasoning language.",
    "if_failing": {
        "critical": "Notes with broken wikilinks — fix the links immediately",
        "moderate": "Notes that are bare fact lists with no reasoning — rewrite with synthesis",
        "minor": "Missing frontmatter — add it. Note: empty sections inside code blocks are false positives.",
    },
    "action": "Use vault_list to get 5 random notes. Call vault_lint on each. Record findings.",
})
```

### Step 4: Tool Safety

Check that destructive tools have safety checks and all custom tools import cleanly.

4. ```python
import json, os, importlib.util

vault_path = os.environ.get("VAULT_PATH", ".")
backend_dir = os.path.join(vault_path, "vaultbot_backend")
custom_dir = os.path.join(backend_dir, "custom_tools")

broken_tools = []
tool_count = 0
if os.path.exists(custom_dir):
    for f in os.listdir(custom_dir):
        if f.endswith('.py') and f != '__init__.py':
            tool_count += 1
            try:
                spec = importlib.util.spec_from_file_location(f"custom_tools.{f[:-3]}", os.path.join(custom_dir, f))
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    if not hasattr(mod, "run"):
                        broken_tools.append({"file": f, "error": "No run() function"})
            except Exception as e:
                broken_tools.append({"file": f, "error": str(e)[:100]})

# Check safety features of destructive tools
safety_checks = {
    "vault_delete": "backs up to trash/ before deleting",
    "safe_write": "syntax-checks before writing, auto-rollback on failure",
    "vault_safe_write": "backs up before overwriting, blocks LOCKED notes",
    "vault_append": "respects LOCKED notes",
}

result = json.dumps({
    "status": "ok" if not broken_tools else "issues_found",
    "tool_count": tool_count,
    "broken_tools": broken_tools,
    "safety_checks": safety_checks,
    "pass_criteria": "All custom tools import cleanly. Destructive tools have safety checks.",
    "if_failing": {
        "critical": "Tools crashing on import — fix immediately, this blocks everything",
        "moderate": "Destructive tool missing safety check — add backup/validation",
        "minor": "Tool works but could be more efficient — note for future",
    },
})
```

### Step 5: System Health and Report

Check backend status, autonomous researcher, and synthesize all findings into a prioritized report.

5. ```python
import json

# The LLM calls vaultbot_status() directly for system health.
# This step synthesizes all findings from Steps 1-4 into a report.

result = json.dumps({
    "status": "report_required",
    "system_checks": [
        "Call vaultbot_status — is backend running? Is autonomous researcher running?",
        "Check autonomous researcher success rate — are recent attempts succeeding?",
        "Check for uncommitted backend changes that could break things on restart",
    ],
    "report_format": {
        "summary": "X critical, Y moderate, Z minor",
        "categories": [
            "Category 1: Knowledge Graph Health — PASS/FAIL + findings",
            "Category 2: Retrieval Quality — PASS/FAIL + findings",
            "Category 3: Note Quality — PASS/FAIL + findings",
            "Category 4: Tool Safety — PASS/FAIL + findings",
            "Category 5: System Health — PASS/FAIL + findings",
        ],
        "prioritized_fix_list": "All Critical first, then Moderate, then Minor",
    },
    "action": "Call vaultbot_status(). Synthesize all findings into a prioritized report. Report to Sean bottom-line-up-front. Fix Critical issues before reporting done.",
})
```

[validate: contains "report_required"]

## Common Failure Modes

| Failure | Fix |
|---|---|
| Rubber-stamping — every category passes | Actually run the checks. If you can't verify, report "unable to verify" |
| Vague findings — "retrieval seems okay" | Name the specific query, result, and problem |
| Fixing during audit — start fixing before finishing all categories | Finish the audit first, then fix in priority order |
| No follow-through — audit finds issues but nobody fixes them | Critical issues must be fixed before audit is complete |

## Related

- [[What-Makes-a-Good-Critique]] — the critique principles this procedure is built on
- [[Evaluate-Retrieval]] — detailed methodology for Category 2
- [[Calibration-via-Operator-Feedback]] — using Sean's corrections
- [[Claim-Verification-for-Vault-Notes]] — claim verification architecture
- [[Deterministic-Scaffolding-for-Small-Models]] — why deterministic checks beat model judgment
- [[Procedural-Bootstrap-and-Evolution-Plan]] — the framework this procedure fits into