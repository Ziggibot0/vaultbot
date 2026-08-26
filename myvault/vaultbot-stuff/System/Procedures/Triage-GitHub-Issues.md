---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-22
description: "Triage the VaultBot repo's open GitHub issues: score each on a deterministic urgency/importance rubric (7 Habits quadrant Q1-Q4), identify the highest-priority Q1 issue, and report the full quadrant table. Read-only on GitHub."
when_to_use: When asked to 'triage the issues', 'what should we fix first', 'prioritize our GitHub issues', or as the first step of an autonomous issue sweep.
falsifiable_if: The procedure ranks a documentation issue above a correctness bug, reports a Q1 issue that is not actually open, or describes priority metadata as proof of implementation ease.
allowed_tools:
  - code_read
summary: Triage-GitHub-Issues
tags:
  - procedure
  - procedures
  - github
  - prioritization
  - triage
---

# Triage-GitHub-Issues

## Purpose

List the VaultBot repo's open GitHub issues, score each on a deterministic
urgency/importance rubric (the 7 Habits quadrant), identify the single
highest-priority Q1 issue, and report the full quadrant table back to the user.
This ranking does not establish which issue is easiest to implement.

## Why This Exists

"Fix the most important issue" is only actionable if "most important" is
defined deterministically. This procedure closes that gap by turning the
judgment into a label→score table plus a recency term, so the small-model
cartridge applies it consistently without freeform gut feel. The tradeoff
is that it ranks on metadata (labels, dates) rather than reading every body
— implementation ease remains unknown until issue bodies and relevant code
surfaces are inspected.

## Steps

### Step 1: List open issues and score them on the quadrant rubric

```python
import json
from datetime import datetime, timezone

from custom_tools.github_issues import run as _issues

# Label -> priority score (issue #97 rubric). Higher = fix first.
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

# Labels that are inherently time-sensitive (raise urgency regardless of age).
_URGENT_LABELS = {"security", "breaks ci", "regression"}


def _importance(labels):
    if not labels:
        return 2  # unlabeled: assume moderate importance
    return max((_LABEL_PRIORITY.get(l.lower(), 2) for l in labels), default=2)


def _urgency(labels, created_at):
    # Time-sensitive labels raise urgency to max.
    if any(l.lower() in _URGENT_LABELS for l in labels):
        return 5
    # Otherwise urgency decays with age: newer issues are more urgent.
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - dt).days
        return max(0, 5 - age_days // 7)  # 5 for <1wk, 0 for >5wk
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
if "error" in res:
    result = json.dumps({"error": res["error"]})
else:
    scored = []
    for i in res.get("issues", []):
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
    # Sort: Q1 first, then by importance+urgency, then by recency.
    _q_order = {"Q1": 0, "Q2": 1, "Q3": 2, "Q4": 3}
    scored.sort(
        key=lambda x: (
            _q_order.get(x["quadrant"], 9),
            -(x["importance"] + x["urgency"]),
            x["created_at"] or "",
        )
    )
    top_q1 = next((s for s in scored if s["quadrant"] == "Q1"), None)
    result = json.dumps(
        {
            "top_number": top_q1["number"] if top_q1 else None,
            "top_issue": top_q1,
            "quadrant_table": scored,
        }
    )
print(result)
```

## Related

- [[Solve-GitHub-Issue]] — may be run separately after the user chooses an issue
- [[Review-Contributions]] — the review-and-merge step inside Solve-GitHub-Issue
- [[Submit-Contribution]] — the PR submission step inside Solve-GitHub-Issue
