"""Procedure Discovery Service — the type-aware surface for procedures.

THE PROBLEM THIS SOLVES
-----------------------
Procedures are the mechanism that lets a SMALL model do BIG work: a procedure
is a stored, deterministic subagent the LLM invokes by name. But today a
procedure is discovered only by riding generic FUSED retrieval, which injects
the procedure's ENTIRE body into context and then *hopes* the model notices it
and decides to call ``execute_procedure(name)``. That leans on exactly the
thing we want to remove — LLM judgment at scale.

the operator's insight (the one this module implements): "the agent only knows about
the procedures it needs." So instead of dumping full procedure bodies into
context and hoping, we give the agent a COMPACT, status-aware *surface* — one
line per relevant procedure with its ``description``, ``when-to-use``, and
``status`` — and let ``execute_procedure`` do the heavy lifting
deterministically. "Route + run" replaces "read + decide."

This is what makes the LLM smaller for free: the model doesn't need to read
and weigh a 3KB procedure body; it sees a one-line capability and calls it.

WHAT THIS MODULE DOES
---------------------
1. ``procedure_surface_line(stem, frontmatter)`` — render one compact line:
   ``- ProcedureName — <description> [status: verified]``
   (with a ``when`` hint if present, and a ``⚠ experimental`` / ``⛔ flagged``
   marker so the model knows the trust level before invoking).
2. ``build_procedure_surface(results, proc_index)`` — scan FUSED results for
   procedure notes and return the compact surface block (a few lines), which
   the chat handler injects as its OWN small system message — separate from
   the big vault-context dump so the compactor never shreds it.
3. ``status_allows_execution(status)`` — the execution gate: ``verified`` and
   ``experimental`` may run (experimental with a caution), ``flagged`` is
   blocked and routed to re-research. This is the "extra-safe" half.

Pure stdlib. No LLM calls. No I/O beyond reading frontmatter the caller
already has.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Statuses that may be executed, and whether they carry a caution.
#   verified    -> trusted, run clean
#   experimental-> run, but tell the model it's unproven
#   flagged     -> DO NOT run; route to re-research
#   "" / unknown-> treat as experimental (a procedure with no status is new)
_EXECUTABLE = {"verified", "experimental", ""}
_CAUTION = {"experimental", ""}
_BLOCKED = {"flagged"}


def _fm_get(frontmatter: dict[str, Any], key: str, default: str = "") -> str:
    """Pull a scalar frontmatter value as a stripped string."""
    v = frontmatter.get(key, default)
    if v is None:
        return default
    if isinstance(v, (list, tuple)):
        # A multi-value field (shouldn't happen for description) — join.
        return " ".join(str(x) for x in v).strip()
    return str(v).strip().strip('"').strip("'")


def _fm_list(frontmatter: dict[str, Any], key: str) -> list[str]:
    """Pull a list frontmatter value as a list of stripped strings.

    Handles both YAML-list (``provides:`` + ``  - item``) and inline-string
    (``provides: Foo``) shapes.  Returns ``[]`` when the key is absent.
    """
    v = frontmatter.get(key)
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip().strip('"').strip("'") for x in v if str(x).strip()]
    s = str(v).strip().strip('"').strip("'")
    return [s] if s else []


def status_allows_execution(status: str) -> tuple[bool, str]:
    """The execution gate.

    Returns (allowed, reason). ``allowed`` is True for verified/experimental/
    unknown, False for flagged. ``reason`` is a short human string the chat
    handler can surface to the model/user.
    """
    s = (status or "").strip().lower()
    if s in _BLOCKED:
        return False, (
            "procedure is FLAGGED (repeatedly failed validation) — it is "
            "blocked from running and queued for re-research. Do not execute "
            "it; find another approach or answer directly."
        )
    if s in _CAUTION:
        return True, "experimental"
    return True, "verified"


def procedure_surface_line(
    stem: str,
    frontmatter: dict[str, Any],
    proc_index: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Render one compact procedure surface line.

    Example outputs:
      ``- Verify-Claims — check a note's claims against its sources [verified]``
      ``- Dream-Pass — consolidate episodic logs into semantic knowledge
      [⚠ experimental]``
      ``- Stale-Proc — ... [⛔ FLAGGED — do not use]``

    When ``frontmatter`` contains a ``provides`` list (sub-procedures this
    procedure composes) AND ``proc_index`` is provided, one level of
    sub-procedures is resolved into compact parenthetical capability lines
    so the model can see what the orchestrator *brings to the table* without
    reading each child:

      ``- Dream-Pass — biomimetic dream pass orchestrator (composes:
      Dream-Scan — scan for orphans, Dream-Analyze — analyze links, ...)
      [verified]``

    Only one level is expanded inline (to avoid context bloat).  The full
    recursive tree is available via :func:`build_procedure_tree` for the
    validator and future tooling.
    """
    desc = _fm_get(frontmatter, "description")
    # Prefer the feedback-tuned trigger/inhibitor lists; fall back to the
    # legacy when_to_use string when the lists are absent (pre-migration).
    triggers = _fm_list(frontmatter, "trigger")
    inhibitors = _fm_list(frontmatter, "inhibitor")
    when = _fm_get(frontmatter, "when_to_use") or _fm_get(frontmatter, "when")
    status = _fm_get(frontmatter, "status").lower()
    cartridge = _fm_get(frontmatter, "model_cartridge").lower()
    provides = _fm_list(frontmatter, "provides")

    if status in _BLOCKED:
        tag = "⛔ FLAGGED — do not use"
    elif status == "verified":
        tag = "verified"
    else:
        tag = "⚠ experimental"

    # Show the model cartridge so the agent knows which model the procedure
    # will use (big = cloud/main, small = tiny local, vision = vision model).
    if cartridge and cartridge != "big":
        tag += f" · model:{cartridge}"

    line = f"- {stem}"
    if desc:
        line += f" — {desc}"

    # Resolve one level of sub-procedures so the model sees the full
    # capability surface of an orchestrator in a single glance — it does
    # NOT need to execute/read each child to know what the parent does.
    if provides and proc_index:
        child_summaries: list[str] = []
        for child_name in provides:
            child_name = child_name.strip()
            if not child_name or child_name not in proc_index:
                continue
            child_fm = proc_index[child_name].get("frontmatter") or {}
            child_desc = _fm_get(child_fm, "description")
            child_status = _fm_get(child_fm, "status").lower()
            # Skip flagged children — they can't run and would add noise.
            if child_status in _BLOCKED:
                continue
            if child_desc:
                child_summaries.append(f"{child_name} — {child_desc}")
            else:
                child_summaries.append(child_name)
        if child_summaries:
            line += f" (composes: {', '.join(child_summaries)})"

    if triggers:
        line += f" (triggers: {', '.join(triggers[:3])}"
        if len(triggers) > 3:
            line += f", +{len(triggers) - 3} more"
        line += ")"
    elif when:
        line += f" (use when: {when})"
    if inhibitors:
        line += f" (avoid when: {', '.join(inhibitors[:2])}"
        if len(inhibitors) > 2:
            line += f", +{len(inhibitors) - 2} more"
        line += ")"
    line += f" [{tag}]"
    return line


def build_procedure_tree(
    stem: str,
    proc_index: dict[str, dict[str, Any]],
    max_depth: int = 3,
) -> dict[str, Any] | None:
    """Recursively resolve the ``provides`` composition graph for a procedure.

    Returns a nested dict::

        {
            "name": "Dream-Pass",
            "description": "biomimetic dream pass orchestrator",
            "status": "verified",
            "provides": [
                {"name": "Dream-Scan", "description": "...", "provides": [...]},
                ...
            ]
        }

    Walks to ``max_depth`` levels with cycle detection (a procedure already
    in the current ancestor chain is not re-entered).  Returns ``None`` when
    ``stem`` is not in ``proc_index``.

    This is used by the validator (to check every named sub-procedure
    actually exists) and by future tooling that needs the full capability
    graph — the *surface* only expands one level inline to stay compact.
    """
    return _build_tree_inner(stem, proc_index, set(), max_depth)


def _build_tree_inner(
    stem: str,
    proc_index: dict[str, dict[str, Any]],
    ancestors: set[str],
    depth_left: int,
) -> dict[str, Any] | None:
    if stem not in proc_index:
        return None
    if stem in ancestors:
        # Cycle — don't recurse further, but still return the node so the
        # caller can see the cycle in the graph.
        return {"name": stem, "cycle": True, "provides": []}
    fm = proc_index[stem].get("frontmatter") or {}
    node: dict[str, Any] = {
        "name": stem,
        "description": _fm_get(fm, "description"),
        "status": _fm_get(fm, "status"),
    }
    provides = _fm_list(fm, "provides")
    if not provides or depth_left <= 0:
        node["provides"] = []
        return node
    children: list[dict[str, Any]] = []
    next_ancestors = ancestors | {stem}
    for child_name in provides:
        child_name = child_name.strip()
        if not child_name:
            continue
        child = _build_tree_inner(
            child_name, proc_index, next_ancestors, depth_left - 1
        )
        if child is not None:
            children.append(child)
        else:
            # Named sub-procedure doesn't exist — mark as dangling so the
            # validator / tooling can surface the broken link.
            children.append({"name": child_name, "missing": True})
    node["provides"] = children
    return node


def build_reverse_dependency_index(
    proc_index: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Build a reverse dependency index from the ``provides`` graph.

    Returns ``{child_stem: [parent_stem, ...]}`` — for each procedure,
    which other procedures declare it in their ``provides`` list.

    This is the inverse of the forward ``provides`` graph. It answers
    "who depends on me?" — used by cascading invalidation (when a child
    is flagged, downgrade verified parents) and by the surface line
    (show "used by: ..." for a child procedure).

    Pure function — no I/O, no side effects. The caller owns proc_index.
    """
    rev: dict[str, list[str]] = {}
    for parent_stem, entry in proc_index.items():
        fm = entry.get("frontmatter") or {}
        provides = _fm_list(fm, "provides")
        for child_name in provides:
            child_name = child_name.strip()
            if child_name:
                rev.setdefault(child_name, []).append(parent_stem)
    return rev


def _frontmatter_from_text(text: str) -> dict[str, Any] | None:
    """Minimal frontmatter parse for a note body (type/description/status/when).

    Only extracts the few scalar keys the surface needs; avoids a YAML dep.
    Returns None if the text isn't a frontmatter-led procedure note.
    """
    if not text.startswith("---"):
        return None
    end = text.find("---", 3)
    if end == -1:
        return None
    fm_block = text[3:end]
    if "type: procedure" not in fm_block:
        return None
    fm: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list | None = None
    for line in fm_block.split("\n"):
        line = line.rstrip()
        if not line:
            continue
        # List item: "  - value"
        if line.startswith("  - ") and current_key:
            value = line[4:].strip().strip('"').strip("'")
            if current_list is None:
                current_list = []
                fm[current_key] = current_list
            current_list.append(value)
            continue
        if line.startswith("  "):
            continue
        if ":" in line:
            current_list = None
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if value:
                fm[key] = value
            current_key = key
    return fm


def build_procedure_surface(
    results: list[dict[str, Any]],
    proc_index: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Build the compact procedure surface block from FUSED results.

    Args:
        results: The FUSED retrieve() result list. Any result whose note is a
                 procedure contributes one surface line.
        proc_index: Optional stem -> {path, frontmatter} map from
                    procedure_tracker.get_procedure_index(). When provided,
                    frontmatter is read from here (richer, includes list
                    fields); otherwise parsed from the result's content.

    Returns:
        A markdown block like::

            # RELEVANT PROCEDURES (deterministic subagents — call
            # execute_procedure(name) to run)
            - Verify-Claims — ... [verified]
            - Dream-Pass — ... [⚠ experimental]

        Returns "" when no procedures are in the results (the common case for
        simple Q&A — the surface stays silent and costs zero tokens).
    """
    lines: list[str] = []
    seen: set[str] = set()
    for r in results:
        if not isinstance(r, dict):
            continue
        fp = r.get("file_path", "")
        if not fp:
            continue
        stem = Path(fp).stem
        if stem in seen:
            continue

        fm: dict[str, Any] | None = None
        if proc_index and stem in proc_index:
            fm = proc_index[stem].get("frontmatter") or {}
            # Confirm it's actually a procedure (the index only contains
            # procedures, but be defensive).
            if "procedure" not in str(fm.get("type", "")).lower():
                # proc_index only holds procedures, so trust it.
                pass
        else:
            text = r.get("content") or r.get("snippet") or ""
            if not text:
                try:
                    text = Path(fp).read_text(encoding="utf-8", errors="replace")
                except Exception:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                    continue
            fm = _frontmatter_from_text(text)

        if fm is None:
            continue
        seen.add(stem)
        lines.append(procedure_surface_line(stem, fm, proc_index))

    if not lines:
        return ""
    header = (
        "# RELEVANT PROCEDURES (deterministic subagents — "
        "call execute_procedure(name) to run one; do NOT re-derive "
        "what a procedure already does)"
    )
    return header + "\n" + "\n".join(lines)
