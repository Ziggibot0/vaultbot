"""Working memory — the externalized task list that keeps the agent on track.

THE PROBLEM THIS SOLVES
-----------------------
The agentic chat loop had no structured working memory. The model's only
memory of "what am I doing" was buried in the raw conversation transcript,
which the compactor shreds every few rounds. When the transcript got
compacted, the model literally forgot the plan — which is why it re-read
Research-Roadmap.md 14 times and re-ran the same vault_search queries in
a loop until it hit MAX_ROUNDS.

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
- When all tasks are done, the harness tells the model to stop looping.
- Bounded: caps at MAX_TASKS=30 entries so a confused model can't spam
  thousands of micro-tasks.
- Pure stdlib, no LLM calls, no I/O except the in-memory list.

THE STOP SIGNAL
---------------
This is the key piece both Claude Code and Copilot use that VaultBot was
missing: a deterministic stop condition. The loop ends when (a) the model
emits no tool calls (the existing path), OR (b) all tasks are marked
completed. The second path is the one that prevents the "re-search the
same thing forever" loop — the model can't override a fully-checked list.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

MAX_TASKS = 30


@dataclass
class Task:
    """A single item in the working-memory task list."""
    id: str
    content: str
    status: str = "pending"  # pending | in_progress | completed
    notes: str = ""           # free-text annotation by the model


@dataclass
class TaskList:
    """Per-session structured working memory.

    Owned by the websocket session (one per chat connection). Thread-safe
    because the chat loop and any background heartbeat thread may read it.
    """
    tasks: list[Task] = field(default_factory=list)
    goal: str = ""            # the high-level goal this task list serves
    lock: threading.Lock = field(default_factory=threading.Lock)

    # ------------------------------------------------------------------
    # Mutators (called by the model via the plan_task / update_task tools)
    # ------------------------------------------------------------------

    def set_plan(self, goal: str, items: list[str]) -> dict[str, Any]:
        """Replace the entire task list with a fresh plan.

        Called at the start of a multi-step task. Clears any prior list
        so the model can re-plan when the user redirects.
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
            return self.snapshot()

    def update_task(self, task_id: str, status: str = "",
                    notes: str = "") -> dict[str, Any]:
        """Update a single task's status and/or notes.

        The model calls this after each tool round to mark progress.
        Returns the updated snapshot so the model sees the result.
        """
        with self.lock:
            for t in self.tasks:
                if t.id == task_id:
                    if status in ("pending", "in_progress", "completed"):
                        t.status = status
                    if notes:
                        t.notes = notes[:500]
                    return self.snapshot()
            return {"error": f"task {task_id} not found"}

    def add_task(self, content: str, status: str = "pending",
                 notes: str = "") -> dict[str, Any]:
        """Append a single task to the list (mid-plan discovery).

        Lets the model add a step it discovered mid-task without re-planning
        the whole list. Bounded by MAX_TASKS.
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
            return self.snapshot()

    def clear(self) -> None:
        """Wipe the list (called on /new session reset)."""
        with self.lock:
            self.tasks = []
            self.goal = ""

    # ------------------------------------------------------------------
    # Readers (called by the harness, not the model)
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable view of the current list."""
        with self.lock:
            return {
                "goal": self.goal,
                "tasks": [
                    {"id": t.id, "content": t.content,
                     "status": t.status, "notes": t.notes}
                    for t in self.tasks
                ],
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
            lines = [f"# WORKING MEMORY (your active plan)"]
            if self.goal:
                lines.append(f"Goal: {self.goal}")
            for t in self.tasks:
                mark = {"completed": "[x]", "in_progress": "[~]",
                        "pending": "[ ]"}.get(t.status, "[ ]")
                line = f"{mark} {t.id}. {t.content}"
                if t.notes:
                    line += f"  — {t.notes}"
                lines.append(line)
            done = sum(1 for t in self.tasks if t.status == "completed")
            lines.append(f"Progress: {done}/{len(self.tasks)} done")
            if done == len(self.tasks) and self.tasks:
                lines.append("ALL TASKS COMPLETE — synthesize your final "
                             "answer now. Do NOT call more tools.")
            return "\n".join(lines)

    def all_done(self) -> bool:
        """Deterministic stop signal: is every task completed?"""
        with self.lock:
            return bool(self.tasks) and all(
                t.status == "completed" for t in self.tasks)

    def has_plan(self) -> bool:
        """Is there an active plan (non-empty task list)?"""
        with self.lock:
            return len(self.tasks) > 0