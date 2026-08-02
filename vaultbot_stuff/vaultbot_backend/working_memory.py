"""Working memory — the externalized task list that keeps the agent on track.

THE PROBLEM THIS SOLVES
-----------------------
The agentic chat loop had no structured working memory. The model's only
memory of "what am I doing" was buried in the raw conversation transcript,
which the sliding window drops when it grows too long. When old messages
fell out of the window, the model literally forgot the plan — which is why
it re-read Research-Roadmap.md 14 times and re-ran the same vault_search
queries in a loop until it hit MAX_ROUNDS.

This module is the VaultBot equivalent of Claude Code's TodoWrite / GitHub
Copilot's plan checklist. It maintains a structured, per-session task list
that lives OUTSIDE the conversation and is re-injected into the system
prompt every turn so the model always sees "here's what's done, here's
what's next" without having to remember.

DESIGN (grounded in how production agents stay on track)
--------------------------------------------------------
- Per-session, in-memory (resets on /new and on backend restart).
- The model writes the plan at the start of a multi-step task, updates
  entries after each tool round, and the harness prepends the current
  list to the system prompt every round.
- Mutators return MINIMAL confirmations (not full snapshots) to avoid
  conversation bloat. The model sees the full list in the system prompt
  every round via ``render_for_prompt()``, so echoing it back in the tool
  result is redundant data that inflates the conversation — every
  update_task call was adding ~1KB of task content that the model already
  had, and with compaction disabled this accumulated across 138 rounds.

RETURN VALUE DESIGN
-------------------
All mutators (``set_plan``, ``update_task``, ``add_task``) return a small
dict with just the action result:

    {"status": "ok", "action": "update", "task_id": "3",
     "task_status": "completed", "total": 9, "completed": 4}

The full task list is available via ``snapshot()`` (for the harness/UI)
and ``render_for_prompt()`` (injected into the system prompt every round).
The model never needs the full snapshot in the tool result — it's already
in the system prompt.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

MAX_TASKS = 20


@dataclass
class Task:
    id: str
    content: str
    status: str = "pending"  # pending | in_progress | completed
    notes: str = ""


@dataclass
class TaskList:
    """Per-session working-memory task list.

    Owned by the websocket session (one per chat connection). Thread-safe
    because the chat loop and any background heartbeat thread may read it.
    """
    tasks: list[Task] = field(default_factory=list)
    goal: str = ""            # the high-level goal this task list serves
    # Per-step consolidated summaries (the memory-consolidation layer).
    # Keyed by task id. When a step is marked completed, the chat loop asks
    # the small model to write a gist of what happened during that step,
    # stores it here, and replaces the raw tool noise in the conversation
    # with this summary. render_for_prompt() shows it next to the step so
    # the model sees the shapes, not the details, as it works later steps.
    step_summaries: dict[str, str] = field(default_factory=dict)
    # RLock (reentrant), NOT Lock: set_plan / update_task / add_task hold the
    # lock and then call self.snapshot(), which acquires it AGAIN. A plain
    # threading.Lock is non-reentrant, so that nested acquire deadlocks the
    # chat loop on the very first plan_task call. RLock lets the same thread
    # re-acquire, which is exactly the set_plan -> snapshot call pattern.
    lock: threading.RLock = field(default_factory=threading.RLock)

    # ------------------------------------------------------------------ #
    # Mutators (called by the model via the plan_task / update_task tools)
    # ------------------------------------------------------------------ #

    def set_plan(self, goal: str, items: list[str]) -> dict[str, Any]:
        """Replace the entire task list with a fresh plan.

        Called at the start of a multi-step task. Clears any prior list
        so the model can re-plan when the user redirects.

        Returns a minimal confirmation — the full list is in the system
        prompt via ``render_for_prompt()``.
        """
        with self.lock:
            self.goal = goal[:500]
            self.tasks = []
            for i, item in enumerate(items[:MAX_TASKS]):
                self.tasks.append(Task(
                    id=str(i + 1),
                    content=item[:300],
                    status="pending",
                ))
            return {
                "status": "ok",
                "action": "plan_set",
                "goal": self.goal[:100],
                "total": len(self.tasks),
            }

    def update_task(self, task_id: str, status: str = "",
                    notes: str = "") -> dict[str, Any]:
        """Update a single task's status and/or notes.

        The model calls this after each tool round to mark progress.
        Returns a minimal confirmation — the full list is in the system
        prompt via ``render_for_prompt()``.
        """
        with self.lock:
            for t in self.tasks:
                if t.id == task_id:
                    if status in ("pending", "in_progress", "completed"):
                        t.status = status
                    if notes:
                        t.notes = notes[:500]
                    return {
                        "status": "ok",
                        "action": "update",
                        "task_id": task_id,
                        "task_status": t.status,
                        "total": len(self.tasks),
                        "completed": sum(1 for x in self.tasks if x.status == "completed"),
                        "pending": sum(1 for x in self.tasks if x.status == "pending"),
                        "in_progress": sum(1 for x in self.tasks if x.status == "in_progress"),
                    }
            return {"error": f"task {task_id} not found"}

    def add_task(self, content: str, status: str = "pending",
                 notes: str = "") -> dict[str, Any]:
        """Append a single task to the list (mid-plan discovery).

        Lets the model add a step it discovered mid-task without re-planning
        the whole list. Bounded by MAX_TASKS.

        Returns a minimal confirmation — the full list is in the system
        prompt via ``render_for_prompt()``.
        """
        with self.lock:
            if len(self.tasks) >= MAX_TASKS:
                return {"error": f"max tasks ({MAX_TASKS}) reached"}
            new_id = str(len(self.tasks) + 1)
            self.tasks.append(Task(
                id=new_id,
                content=content[:300],
                status=status if status in ("pending", "in_progress", "completed") else "pending",
                notes=notes[:500],
            ))
            return {
                "status": "ok",
                "action": "add",
                "task_id": new_id,
                "total": len(self.tasks),
                "completed": sum(1 for x in self.tasks if x.status == "completed"),
                "pending": sum(1 for x in self.tasks if x.status == "pending"),
                "in_progress": sum(1 for x in self.tasks if x.status == "in_progress"),
            }

    def clear(self) -> None:
        """Wipe the list (called on /new session reset)."""
        with self.lock:
            self.tasks = []
            self.goal = ""
            self.step_summaries = {}

    # ------------------------------------------------------------------ #
    # Readers (called by the harness, not the model)
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable view of the current list.

        Used by the harness/UI (NOT returned to the model as a tool result).
        The model sees the list via ``render_for_prompt()`` in the system
        prompt instead.
        """
        with self.lock:
            return {
                "goal": self.goal,
                "tasks": [
                    {"id": t.id, "content": t.content,
                     "status": t.status, "notes": t.notes}
                    for t in self.tasks
                ],
                "step_summaries": dict(self.step_summaries),
                "total": len(self.tasks),
                "completed": sum(1 for t in self.tasks if t.status == "completed"),
                "pending": sum(1 for t in self.tasks if t.status == "pending"),
                "in_progress": sum(1 for t in self.tasks if t.status == "in_progress"),
            }

    def render_for_prompt(self) -> str:
        """Render the list as a compact block for injection into the system
        prompt. This is what the model sees every round so it knows where
        it is in the plan without relying on the compacted transcript.

        Returns an empty string when there's no active plan (so simple
        Q&A turns aren't polluted with empty scaffolding).
        """
        with self.lock:
            if not self.tasks:
                return ""
            lines = ["# WORKING MEMORY (your active plan)"]
            if self.goal:
                lines.append(f"Goal: {self.goal}")
            for t in self.tasks:
                mark = {"completed": "[x]", "in_progress": "[~]",
                        "pending": "[ ]"}.get(t.status, "[ ]")
                line = f"{mark} {t.id}. {t.content}"
                if t.notes:
                    line += f" — {t.notes}"
                lines.append(line)
            done = sum(1 for t in self.tasks if t.status == "completed")
            total = len(self.tasks)
            lines.append(f"Progress: {done}/{total} done")
            # Append the consolidated summaries for completed steps so the
            # model carries the gist forward without re-reading raw tool
            # output. This is the "zoom out to bigger shapes" surface: each
            # summary is what was accomplished + lessons + key facts the
            # next step needs (see step_summarizer.py).
            for t in self.tasks:
                if t.status != "completed":
                    continue
                sm = self.step_summaries.get(t.id, "")
                if sm:
                    lines.append(f"  ↳ Step {t.id} summary: {sm}")
            return "\n".join(lines)

    def record_step_summary(self, task_id: str, summary: str) -> None:
        """Store the consolidated summary for a completed step.

        Called by the chat loop after the small model produces a gist of the
        step's tool/thinking noise. ``render_for_prompt`` surfaces it next to
        the step so later rounds see the shape, not the raw trace.
        """
        with self.lock:
            self.step_summaries[task_id] = summary[:800]

    def summary_for_step(self, task_id: str) -> str:
        """Return the stored summary for a step, or '' if none."""
        with self.lock:
            return self.step_summaries.get(task_id, "")

    def has_plan(self) -> bool:
        """True if there's at least one task in the list."""
        with self.lock:
            return len(self.tasks) > 0

    def all_done(self) -> bool:
        """True if every task is completed."""
        with self.lock:
            return bool(self.tasks) and all(
                t.status == "completed" for t in self.tasks)

    def restore_snapshot(self, snap: dict[str, Any]) -> None:
        """Restore a prior state from a snapshot (used by checkpoint/resume
        after a crash or backend restart).

        Accepts the format produced by ``snapshot()``.
        """
        with self.lock:
            self.goal = (snap.get("goal") or "")[:500]
            self.tasks = []
            self.step_summaries = {}
            _sums = snap.get("step_summaries") or {}
            if isinstance(_sums, dict):
                for _k, _v in _sums.items():
                    if isinstance(_k, str) and isinstance(_v, str):
                        self.step_summaries[_k] = _v[:800]
            for t in snap.get("tasks", []):
                # Skip malformed entries (non-dict, missing fields) so a
                # corrupt snapshot can't crash the restore.
                if not isinstance(t, dict):
                    continue
                # Validate status the same way add_task / update_task do —
                # a corrupt or unknown status is coerced to "pending"
                # rather than propagating garbage into the task list.
                raw_status = t.get("status", "pending")
                valid = raw_status if raw_status in ("pending", "in_progress", "completed") else "pending"
                self.tasks.append(Task(
                    id=str(t.get("id", "")),
                    content=(t.get("content") or "")[:300],
                    status=valid,
                    notes=(t.get("notes") or "")[:500],
                ))
