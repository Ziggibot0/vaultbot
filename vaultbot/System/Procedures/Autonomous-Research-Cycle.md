---
type: procedure
status: active
baseline: true
created: 2026-08-14
description: "Decision logic for the autonomous researcher's cycle. Takes the current vault state as args and returns a JSON plan: skip, run consolidation, or research a prioritized list of gaps. The Python infrastructure in autonomous_researcher.py calls this procedure and executes the returned plan. Edit this note to change what gets researched, in what order, and how gaps are filtered — no backend restart needed."
when_to_use: "Called automatically by autonomous_researcher.py every cycle. Not for manual invocation."
allowed_tools: []
model_cartridge: big
summary: Decision logic for the autonomous research cycle — returns a JSON plan.
tags:
  - procedure
  - autonomous-research
  - decision-logic
---

# Autonomous-Research-Cycle

## What This Does

This is the **decision layer** of the autonomous researcher. The Python class
`AutonomousResearcher` in `autonomous_researcher.py` is a thin executor: it
gathers raw state (gaps, queue sizes, chat status), calls this procedure, and
mechanically executes the returned plan. **All logic lives here** — gap
filtering, prioritization, budget calculation, consolidation scheduling,
QA/chat yield decisions, and post-action directives.

```
AutonomousResearcher._cycle_impl()
  ├── Gather raw state (gaps, qa_queue_size, chat_active, cycle_count)
  ├── Call this procedure → get JSON plan
  └── Mechanically execute the plan (research / consolidate / skip)
```

## Inputs

Passed as `procedure_args` (the `args` dict in code steps):

| Key | Type | Description |
|---|---|---|
| `cycle_count` | int | Current cycle number (increments each cycle) |
| `consolidation_interval` | int | Run consolidation every Nth cycle (default 5) |
| `recovered_gaps` | list[dict] | Gaps from a crashed previous cycle (empty if none) |
| `procedure_gaps` | list[dict] | Gaps from the procedure tracker (empty if none) |
| `curriculum_gaps` | list[dict] | Gaps from the knowledge curriculum |
| `max_researches_per_cycle` | int | Budget cap for research per cycle |
| `qa_queue_size` | int | Number of notes pending QA healing |
| `chat_active` | bool | True if a chat turn is currently in flight |

Each gap dict has: `{"topic": str, "kind": str, "priority": int, ...}`

## Output

A JSON string assigned to `result`:

```json
{
  "action": "skip",
  "reason": "chat turn in flight"
}
```

or

```json
{
  "action": "consolidation",
  "reason": "cycle 10 is a consolidation cycle"
}
```

or

```json
{
  "action": "research",
  "gaps": [
    {"topic": "quantum-computing", "kind": "dangling_link", ...},
    ...
  ],
  "budget": 2,
  "source": "recovered|procedure|curriculum",
  "rejected_count": 3,
  "reset_procedures": ["Find-Redundant-Procedures"],
  "run_promotion": true
}
```

or

```json
{
  "action": "no_gaps",
  "reason": "no researchable gaps found"
}
```

## Steps

### Step 1: Decide what to do this cycle and return a JSON plan

```python
import json, re

# --- Read inputs ---
cycle_count = args.get("cycle_count", 0)
consolidation_interval = args.get("consolidation_interval", 5)
recovered_gaps = args.get("recovered_gaps", [])
procedure_gaps = args.get("procedure_gaps", [])
curriculum_gaps = args.get("curriculum_gaps", [])
max_researches = args.get("max_researches_per_cycle", 2)
qa_queue_size = args.get("qa_queue_size", 0)
chat_active = args.get("chat_active", False)

# --- Pre-flight gates ---

# 1. Chat-priority: if a chat turn is in flight, skip this cycle entirely.
#    The user's embedding/LLM calls must not queue behind background research
#    on a single-GPU laptop. The next cycle runs normally once chat ends.
if chat_active:
    result = json.dumps({
        "action": "skip",
        "reason": "chat turn in flight",
    })
    # STOP — return immediately.

# 2. QA-priority: if the QA worker still has notes to heal, skip this cycle.
#    Existing vault notes get healed BEFORE the researcher creates new ones.
#    QA heals what's there, then the researcher expands, then QA heals what
#    the researcher made.
if qa_queue_size > 0:
    result = json.dumps({
        "action": "skip",
        "reason": f"QA queue has {qa_queue_size} pending notes",
    })
    # STOP — return immediately.

# --- Gap quality gate ---
# Prevents the researcher from wasting cycles on topics that are clearly
# not researchable knowledge concepts. This is the SECOND layer of filtering
# on top of the knowledge curriculum's own filters.

# Topics that start with these prefixes are conversation logs or synthetic
# hub proposals, NOT knowledge concepts worth web-researching.
_BAD_TOPIC_PREFIXES = (
    "chat-",
    "moc for:",
    "moc-for:",
    "partial_",
)

_BAD_TOPIC_PATTERNS = re.compile(
    r"^(?:partial|untitled|draft|todo|tbd|readme|license)$",
    re.IGNORECASE,
)

# VaultBot's own tool / API names. Topics that are "how to <tool_name>"
# are procedural gaps about OUR tools, not web-researchable concepts.
_INTERNAL_TOOL_NAMES = frozenset({
    "vault_research", "vault_search", "vault_gaps", "vaultbot_status",
    "plan_task", "update_task", "code_read", "code_run", "code_write",
    "tool_create", "self_reflect", "git_rollback", "safe_write",
    "js_safe_write", "capability_audit", "execute_procedure",
    "textbook_ingest", "textbook_read_page", "web_read_source",
    "vault_append", "vault_delete", "vault_graph_analyzer",
    "vault_lint", "vault_list", "preflight_safety_check",
    "backend_restart", "plugin_reload",
})

_MAX_TOPIC_WORDS = 8
_MIN_TOPIC_ALNUM_CHARS = 3

# Single-word stopics (mirrors knowledge_curriculum._SINGLE_WORD_STOPICS)
_SINGLE_WORD_STOPICS = frozenset({
    "note", "target", "wikilink", "todo", "task", "step", "procedure",
    "summary", "tag", "link", "file", "path", "name", "title", "date",
    "status", "type", "content", "body", "head", "frontmatter", "backlink",
    "reference", "source", "topic", "concept", "idea", "memory", "chat",
    "session", "log", "entry", "draft", "template", "example", "sample",
    "test", "check", "verify", "validate", "review", "audit", "fix",
    "error", "warning", "issue", "problem", "bug", "fail", "pass", "ok",
    "true", "false", "none", "null", "empty", "missing", "stale", "old",
    "new", "add", "remove", "delete", "update", "create", "build", "make",
    "run", "start", "stop", "pause", "resume", "load", "save", "open",
    "close", "read", "write", "import", "export", "sync", "merge", "split",
})

# Placeholder patterns (mirrors knowledge_curriculum._PLACEHOLDER_RE)
_PLACEHOLDER_RE = re.compile(
    r"^(?:\[\[|\]\]|\{|template|placeholder|example|sample|todo|tbd|xxx|yyy|foo|bar|baz|abc|def|untitled|draft|note-?title|related-?note|some-?note|your-?note|this-?note|the-?note)",
    re.IGNORECASE,
)

_TEMPLATE_VAR_RE = re.compile(r"^\{[a-z_]\}$|^\[\[\{[a-z_]\}\]\]$|^\{\d+\}$", re.IGNORECASE)


def _is_internal_tool_topic(topic):
    t = topic.strip().lower()
    for prefix in ("how to ", "how to", "what is ", "what is"):
        if t.startswith(prefix):
            t = t[len(prefix):].strip()
            break
    if t in _INTERNAL_TOOL_NAMES:
        return True
    for token in re.split(r"[\s,]+", t):
        token = token.strip()
        if "_" in token and len(token) <= 20:
            return True
    return False


def _is_researchable_gap(gap):
    try:
        topic = (gap.get("topic") or "").strip()
        if not topic:
            return False

        topic_lower = topic.lower()
        for prefix in _BAD_TOPIC_PREFIXES:
            if topic_lower.startswith(prefix):
                return False

        if _BAD_TOPIC_PATTERNS.match(topic):
            return False

        if _PLACEHOLDER_RE.match(topic):
            return False
        if _TEMPLATE_VAR_RE.match(topic):
            return False

        alpha = re.sub(r"[^a-zA-Z\s]+", " ", topic).strip()
        alpha_words = [w for w in alpha.split() if w]
        if (
            len(alpha_words) == 1
            and alpha_words[0].lower() in _SINGLE_WORD_STOPICS
        ):
            return False

        if "/" in topic or topic.endswith(
            (".html", ".md", ".pdf", ".py", ".js", ".json", ".txt")
        ):
            return False

        if _is_internal_tool_topic(topic):
            return False

        kind = (gap.get("kind") or "").strip()
        if kind == "thin_community":
            return False
        if kind == "link_density":
            return False
        if kind in ("failing_procedure", "failing_step", "stale_procedure"):
            return False

        words = re.split(r"[\s-]+", topic)
        if len(words) > _MAX_TOPIC_WORDS:
            return False

        alnum = re.sub(r"[^a-zA-Z0-9]+", "", topic)
        if len(alnum) < _MIN_TOPIC_ALNUM_CHARS:
            return False

        return True
    except Exception:
        return False


# --- Decision logic ---

# 3. Consolidation check: every Nth cycle, run consolidation instead.
if cycle_count > 0 and cycle_count % consolidation_interval == 0:
    result = json.dumps({
        "action": "consolidation",
        "reason": f"cycle {cycle_count} is a consolidation cycle",
    })
    # STOP — return immediately.

# 4. Gap selection priority:
#    a. Recovered gaps from crash checkpoint (highest priority)
#    b. Procedure tracker gaps (failing procedures, procedural gaps)
#    c. Knowledge curriculum gaps (dangling links, thin notes)

rejected_count = 0
source = None
gaps = []
reset_procedures = []

if recovered_gaps:
    # Filter recovered gaps too — they may have been pre-filter garbage.
    filtered = [g for g in recovered_gaps if _is_researchable_gap(g)]
    rejected_count += len(recovered_gaps) - len(filtered)
    gaps = filtered
    source = "recovered"
elif procedure_gaps:
    # Partition into researchable gaps and rejected procedure gaps.
    # Procedure-name gaps (failing_procedure, failing_step, stale_procedure)
    # are NOT web-researchable — the procedure's name is an internal identifier,
    # not a concept the web knows about. They are filtered out by
    # _is_researchable_gap above. Collect their names so the executor can
    # reset their failure counts (otherwise they come back every cycle).
    filtered = [g for g in procedure_gaps if _is_researchable_gap(g)]
    rejected_count += len(procedure_gaps) - len(filtered)

    # Collect rejected procedure names for failure reset.
    for g in procedure_gaps:
        if not _is_researchable_gap(g) and g.get("procedure"):
            reset_procedures.append(g["procedure"])

    gaps = filtered
    source = "procedure"
else:
    filtered = [g for g in curriculum_gaps if _is_researchable_gap(g)]
    rejected_count += len(curriculum_gaps) - len(filtered)
    gaps = filtered
    source = "curriculum"

# 5. Budget calculation.
budget = min(max_researches, len(gaps))

# 6. Final plan.
if not gaps:
    result = json.dumps({
        "action": "no_gaps",
        "reason": "no researchable gaps found after filtering",
        "rejected_count": rejected_count,
        "total_gaps_found": len(recovered_gaps) + len(procedure_gaps) + len(curriculum_gaps),
    })
else:
    result = json.dumps({
        "action": "research",
        "gaps": gaps[:budget],
        "budget": budget,
        "source": source,
        "rejected_count": rejected_count,
        "reset_procedures": reset_procedures,
        "run_promotion": True,
        "total_gaps_found": len(recovered_gaps) + len(procedure_gaps) + len(curriculum_gaps),
    })
```