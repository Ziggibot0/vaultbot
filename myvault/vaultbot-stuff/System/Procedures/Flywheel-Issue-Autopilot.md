---
type: procedure
status: experimental
baseline: true
model_cartridge: medium
created: 2026-08-24
description: "Autonomously process open GitHub issues in flywheel order. Ranks issues with the existing urgency/importance rubric, then runs Solve-GitHub-Issue for each issue in sequence to open PRs without manual handoffs."
when_to_use: "When asked to 'get to work on GitHub issues', 'run the flywheel', 'work through issues in order', or run issue PR work overnight while the user sleeps."
falsifiable_if: "The procedure skips a higher-ranked issue, or claims completion without attempting Solve-GitHub-Issue on the ordered queue."
allowed_tools:
  - code_read
  - run_procedure
summary: Flywheel-Issue-Autopilot
tags:
  - procedure
  - procedures
  - github
  - flywheel
  - orchestration
---

# Flywheel-Issue-Autopilot

## Purpose

Run the GitHub issue flywheel end-to-end: rank all open issues with a
deterministic rubric, then process them one-by-one through
`Solve-GitHub-Issue`.

## Why This Exists

`Triage-GitHub-Issues` picks one top issue. This procedure extends that into
an autonomous queue runner so VaultBot can keep moving through the backlog and
open PRs continuously.

## Input

- `args.max_issues` (optional): max number of issues to process this run.
  Defaults to all open issues.
- `args.only_q1` (optional): if true, process only Q1 issues.
  Defaults to false.
- `args.skip_if_open_pr` (optional): if true (default), skip issues that
    already appear in an open PR body/title as `fixes #N`/`closes #N`.

## Steps

### Step 1: Build the flywheel-ordered issue queue

```python
import json
from datetime import datetime, timezone
import re

from custom_tools.github_issues import run as _issues

_LABEL_PRIORITY = {
    "security": 5,
    "breaks ci": 5,
    "regression": 4,
    "bug": 3,
    "feature": 2,
    "enhancement": 2,
    "tech-debt": 2,
    "help wanted": 1,
    "good first issue": 1,
    "documentation": 1,
}

_URGENT_LABELS = {"security", "breaks ci", "regression"}


def _importance(labels):
    if not labels:
        return 2
    return max((_LABEL_PRIORITY.get(str(l).lower(), 2) for l in labels), default=2)


def _urgency(labels, created_at):
    if any(str(l).lower() in _URGENT_LABELS for l in labels):
        return 5
    try:
        dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - dt).days
        return max(0, 5 - age_days // 7)
    except Exception:
        return 0


def _quadrant(importance, urgency):
    important = importance >= 3
    urgent = urgency >= 3
    if important and urgent:
        return "Q1"
    if important and not urgent:
        return "Q2"
    if not important and urgent:
        return "Q3"
    return "Q4"


res = _issues({"action": "list", "state": "open"})
if isinstance(res, dict) and "error" in res:
    result = json.dumps({"error": res["error"]})
else:
    scored = []
    for i in res.get("issues", []) if isinstance(res, dict) else []:
        labels = i.get("labels", [])
        importance = _importance(labels)
        urgency = _urgency(labels, i.get("created_at", ""))
        scored.append(
            {
                "number": i.get("number"),
                "title": i.get("title"),
                "labels": labels,
                "comments": i.get("comments", 0),
                "created_at": i.get("created_at"),
                "html_url": i.get("html_url"),
                "importance": importance,
                "urgency": urgency,
                "quadrant": _quadrant(importance, urgency),
            }
        )

    _q_order = {"Q1": 0, "Q2": 1, "Q3": 2, "Q4": 3}
    scored.sort(
        key=lambda x: (
            _q_order.get(x["quadrant"], 9),
            -(x["importance"] + x["urgency"]),
            x["created_at"] or "",
        )
    )

    only_q1 = bool(args.get("only_q1", False))
    if only_q1:
        scored = [s for s in scored if s.get("quadrant") == "Q1"]

    max_issues = args.get("max_issues")
    if max_issues is not None:
        try:
            n = max(0, int(max_issues))
            scored = scored[:n]
        except Exception:
            pass

    skipped_due_to_open_pr = []
    if bool(args.get("skip_if_open_pr", True)):
        try:
            from custom_tools.gh_client import gh_api
            from custom_tools.upstream_identity import resolve_upstream

            upstream_owner, upstream_repo = resolve_upstream()
            pulls = gh_api(
                "GET",
                f"repos/{upstream_owner}/{upstream_repo}/pulls?state=open&per_page=100",
                timeout=30,
            )

            issue_nums_with_open_pr = set()
            close_pat = re.compile(
                r"\\b(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)\\s*#(\\d+)\\b",
                re.IGNORECASE,
            )
            for pr in pulls if isinstance(pulls, list) else []:
                text = f"{pr.get('title', '')}\n{pr.get('body', '')}"
                for m in close_pat.findall(text):
                    try:
                        issue_nums_with_open_pr.add(int(m))
                    except Exception:
                        continue

            kept = []
            for item in scored:
                num = item.get("number")
                if isinstance(num, int) and num in issue_nums_with_open_pr:
                    skipped_due_to_open_pr.append(
                        {
                            "issue_number": num,
                            "title": item.get("title", ""),
                            "reason": "open_pr_exists",
                        }
                    )
                    continue
                kept.append(item)
            scored = kept
        except Exception:
            # Best effort: if PR discovery fails, continue with scored queue.
            pass

    result = json.dumps({
        "queue": scored,
        "count": len(scored),
        "only_q1": only_q1,
        "skipped_due_to_open_pr": skipped_due_to_open_pr,
    })

print(result)
```

### Step 2: Process the queue with Solve-GitHub-Issue

```python
import json

try:
    data = json.loads(output)
except Exception:
    data = {}

if isinstance(data, dict) and data.get("error"):
    result = json.dumps({"error": data.get("error")})
else:
    queue = data.get("queue", []) if isinstance(data, dict) else []
    skipped = (
        data.get("skipped_due_to_open_pr", [])
        if isinstance(data, dict)
        else []
    )
    processed = []
    for item in queue:
        num = item.get("number") if isinstance(item, dict) else None
        if not num:
            continue
        solve = run_procedure("Solve-GitHub-Issue", {"issue_number": int(num)})
        processed.append(
            {
                "issue_number": int(num),
                "title": item.get("title", ""),
                "quadrant": item.get("quadrant", ""),
                "solve_result": solve,
            }
        )

    result = json.dumps(
        {
            "requested": len(queue),
            "processed": len(processed),
            "skipped_due_to_open_pr": skipped,
            "results": processed,
        },
        default=str,
    )

print(result)
```

## Related

- [[Triage-GitHub-Issues]] — source of the flywheel ranking rubric
- [[Solve-GitHub-Issue]] — issue-to-PR execution loop
- [[Review-Contributions]] — merge gate used by Solve-GitHub-Issue
