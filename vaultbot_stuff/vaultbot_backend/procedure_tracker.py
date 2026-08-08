"""
Procedure Tracker: the deterministic feedback loop for procedural notes.

This module implements the four evolution mechanisms from the
Procedural Bootstrap and Evolution Plan:

  1. Failure-driven evolution: log pass/fail per procedure, trigger
     re-research when failures exceed a threshold.
  2. Time-driven re-research: find procedural notes whose last_reviewed
     date is older than their review_interval_days.
  3. Quality-driven promotion: after N uses, promote procedures with
     high success rates to "verified" and flag low performers.
  4. Procedural gap detection: when validation fails with NO procedure
     in context, log the task type so the autonomous researcher can
     find a procedure for it.

All mechanisms are deterministic: counters, date comparisons, and
string matching. No LLM judgment is used for any decision here.

Step-level tracking (added for the step-gate runtime): each step's
pass/fail is logged with a ``step_number`` field, enabling per-step
failure detection and re-research targeting. See
``step_gate_runtime.py`` and [[Procedural-Bootstrap-and-Evolution-Plan]].
"""

import json
import os
import re
from collections import defaultdict
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Directories to skip when scanning the vault for procedural notes. Mirrors
# the indexer's IGNORED_DIRS so the tracker doesn't waste time reading venv
# files, Obsidian config, session logs, etc. Kept inline (not imported from
# vault_indexer) so this module stays dependency-free.
_TRACKER_IGNORED_DIRS = {
    ".venv", "vaultbot_venv", "vaultbot_index", "sessions", "partials",
    ".git", ".obsidian", "trash",
}


# --- Task-name validation for procedural gap detection ---
# Prevents leaked chat messages (user_message[:100]) from becoming
# "how to {chat_message}" research topics. Valid task names are short
# identifiers like "code_run", "safe_write", "vault_lint", "tool_create".
# A task name with spaces + sentence punctuation + >5 words is prose, not
# an identifier — reject it so the autonomous researcher doesn't waste a
# web-search cycle on the user's literal chat message.

_MAX_TASK_WORDS = 5          # "code_run" = 1 word; a sentence = 10+
_MAX_TASK_LEN = 40          # tool names are short; chat messages are 100
_TASK_PROSE_RE = re.compile(r"[.!?,;:'\"]")  # sentence punctuation = prose


def _is_valid_task_name(task: str) -> bool:
    """True if ``task`` looks like a real tool/task identifier, not prose."""
    if not task or not task.strip():
        return False
    t = task.strip()
    if len(t) > _MAX_TASK_LEN:
        return False
    # Sentence punctuation = leaked prose, not an identifier.
    if _TASK_PROSE_RE.search(t):
        return False
    words = t.split()
    if len(words) > _MAX_TASK_WORDS:
        return False
    return True


# --- Structured failure categories (not free-text) ---

FAILURE_CATEGORIES = {
    "broken_wikilinks": "Note has broken wikilinks",
    "missing_frontmatter": "Note is missing YAML frontmatter",
    "argument_quality": "Note fails argument quality checks",
    "syntax_error": "Code has syntax errors",
    "import_error": "Code fails to import",
    "user_correction": "the operator corrected the output",
    "validation_error": "Generic validation failure",
}

# --- Thresholds (start simple, make adaptive later) ---

FAILURE_THRESHOLD = 3          # failures before re-research triggered
FAILURE_WINDOW_DAYS = 30       # only count failures in this window
PROMOTION_THRESHOLD = 5        # uses before promotion check
PROMOTION_SUCCESS_RATE = 0.7   # 70% success rate to promote to verified
DEMOTION_SUCCESS_RATE = 0.4     # below 40% -> flag for re-research
MAX_LOG_ENTRIES = 500          # keep the log bounded
STEP_FAILURE_THRESHOLD = 3     # step failures before step-level re-research


# --- Frontmatter update helper ---

def update_frontmatter(file_path: Path, updates: dict) -> bool:
    """Update specific frontmatter fields in a markdown note.

    Reads the file, parses the YAML frontmatter (simple key: value format),
    updates the specified fields, and writes the file back.
    Preserves everything outside the frontmatter (body, code blocks, etc.).

    Returns True if updated, False if no frontmatter found.
    """
    text = file_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return False
    end_idx = text.find("---", 3)
    if end_idx == -1:
        return False

    fm_lines = text[3:end_idx].strip().split("\n")
    body = text[end_idx:]

    for key, value in updates.items():
        found = False
        for i, line in enumerate(fm_lines):
            if line.strip().startswith(f"{key}:"):
                if isinstance(value, float):
                    fm_lines[i] = f"{key}: {round(value, 2)}"
                elif isinstance(value, str):
                    fm_lines[i] = f"{key}: {value}"
                else:
                    fm_lines[i] = f"{key}: {value}"
                found = True
                break
        if not found:
            fm_lines.append(f"{key}: {value}")

    new_text = "---\n" + "\n".join(fm_lines) + "\n" + body
    file_path.write_text(new_text, encoding="utf-8")
    return True


class ProcedureTracker:
    """Tracks procedure usage, logs failures, and manages quality promotion.

    This is the deterministic feedback loop: it records what happened,
    counts it, and triggers re-research when counts exceed thresholds.
    No LLM judgment -- just counters and date comparisons.
    """

    def __init__(self, log_path: str, vault_path: str = "."):
        self.log_path = Path(log_path)
        self.vault_path = Path(vault_path)
        self._ensure_log()

    # --- Vault scanning ---

    def _iter_procedural_notes(self, vault_path: str = "."
                               ) -> Iterator[tuple[Path, str, str]]:
        """Yield (path, frontmatter_str, full_text) for every procedural note.

        Procedural notes are markdown files whose YAML frontmatter contains
        ``type: procedure``. This is the shared scan used by the time-driven,
        promotion, and update-after-research paths so the vault is walked
        ONCE per cycle instead of three times. Uses a pruned ``os.walk``
        (skips venv/.git/.obsidian/etc. in-place) and reads each file only
        once. Non-procedural notes are filtered out without a full read of
        the body where possible (frontmatter is at the top).
        """
        vault = Path(vault_path)
        if not vault.is_dir():
            return
        for root, dirs, files in os.walk(vault):
            # Prune ignored subtrees in-place so os.walk doesn't descend.
            dirs[:] = [d for d in dirs if d not in _TRACKER_IGNORED_DIRS]
            for fname in files:
                if not fname.endswith(".md"):
                    continue
                md = Path(root) / fname
                try:
                    text = md.read_text(encoding="utf-8", errors="replace")
                except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                    continue
                if not text.startswith("---"):
                    continue
                end = text.find("---", 3)
                if end == -1:
                    continue
                fm = text[3:end]
                if "type: procedure" not in fm:
                    continue
                yield md, fm, text

    # --- Procedure stem index (for fast lookup by name) -----------------
    #
    # A stem -> {path, frontmatter} map so the chat handler can resolve
    # ``execute_procedure("How-to-Verify-Claims-in-a-Research-Note")`` in
    # O(1) instead of rglob-walking the vault on every call.  The index
    # is rebuilt on demand; callers (main.py) should call this once at
    # startup and refresh it on file-change events.  Stale entries (a
    # note that was deleted) are simply absent from the next rebuild.

    def get_procedure_index(self, vault_path: str = "."
                            ) -> dict[str, dict[str, Any]]:
        """Build and return a stem -> {path, frontmatter} index.

        Walks the vault once, restricted to procedural notes.  The
        frontmatter dict is the simple key-value parse used elsewhere
        in this module (no nested mappings).  Returned dict is owned
        by the caller — caching it is the caller's responsibility.
        """
        index: dict[str, dict[str, Any]] = {}
        for md, fm_str, _text in self._iter_procedural_notes(vault_path):
            fm: dict[str, Any] = {}
            current_key: str | None = None
            current_list: list | None = None
            for line in fm_str.split("\n"):
                line = line.rstrip()
                if not line:
                    continue
                if line.startswith("  - ") and current_key:
                    value = line[4:].strip().strip('"').strip("'")
                    if current_list is None:
                        current_list = []
                        fm[current_key] = current_list
                    current_list.append(value)
                    continue
                if ":" in line:
                    current_list = None
                    key, _, value = line.partition(":")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if value:
                        fm[key] = value
                    # Always track the key even when the value is empty —
                    # the following ``  - item`` lines belong to THIS key,
                    # not whatever scalar came before.  Without this a bare
                    # ``provides:`` (or any list-only key) silently attaches
                    # its items to the previous scalar key.
                    current_key = key
            index[md.stem] = {"path": str(md), "frontmatter": fm}
        return index

    # --- Log I/O ---

    def _ensure_log(self):
        """Create the failure log if it doesn't exist."""
        if not self.log_path.exists():
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_log({"entries": [], "summary": {}})

    def _read_log(self) -> dict:
        try:
            return json.loads(self.log_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            return {"entries": [], "summary": {}}

    def _write_log(self, data: dict):
        self.log_path.write_text(
            json.dumps(data, indent=2, default=str), encoding="utf-8")

    def _recompute_summary(self, data: dict) -> dict:
        """Recompute the summary from entries (only within the failure window)."""
        now = datetime.now(UTC)
        window_start = now - timedelta(days=FAILURE_WINDOW_DAYS)
        summary = defaultdict(lambda: {
            "total": 0, "failures": 0, "passes": 0,
            "last_failure": None, "last_pass": None
        })
        for entry in data.get("entries", []):
            ts = entry.get("timestamp", "")
            try:
                entry_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                continue
            if entry_dt < window_start:
                continue
            proc = entry.get("procedure", "no_procedure")
            summary[proc]["total"] += 1
            if entry.get("validation_result") == "fail":
                summary[proc]["failures"] += 1
                summary[proc]["last_failure"] = ts
            else:
                summary[proc]["passes"] += 1
                summary[proc]["last_pass"] = ts
        return dict(summary)

    # --- Logging ---

    def log_result(self, procedure: str, task: str, validation_result: str,
                   validation_tool: str, error_details: str = "",
                   category: str = "validation_error", severity: str = "medium"):
        """Log a pass or fail for a procedure.

        Args:
            procedure: The procedure note name (or "no_procedure" if
                none was in context).
            task: What was being attempted (e.g. "write research note",
                "create tool"). Used to identify procedural gaps.
            validation_result: "pass" or "fail".
            validation_tool: Which tool caught it (vault_lint,
                safe_write, user_correction).
            error_details: What specifically failed.
            category: Structured failure category from FAILURE_CATEGORIES.
            severity: low, medium, high.
        """
        data = self._read_log()
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "procedure": procedure,
            "task": task,
            "validation_result": validation_result,
            "validation_tool": validation_tool,
            "error_details": error_details,
            "category": category,
            "severity": severity,
        }
        data["entries"].append(entry)
        if len(data["entries"]) > MAX_LOG_ENTRIES:
            data["entries"] = data["entries"][-MAX_LOG_ENTRIES:]
        data["summary"] = self._recompute_summary(data)
        self._write_log(data)

    # --- Step-level logging (for the step-gate runtime) ---

    def log_step_result(self, procedure: str, step_number: int,
                        passed: bool, error: str = ""):
        """Log a pass or fail for a single step within a procedure.

        This is called by the step-gate runtime after each step's
        validation. Step-level entries share the same log file as
        procedure-level entries, distinguished by the ``step_number``
        field (procedure-level entries have no ``step_number``).

        Args:
            procedure: The procedure note name.
            step_number: Which step (1-indexed).
            passed: Whether the step's output passed validation.
            error: Validation error message if failed, empty if passed.
        """
        data = self._read_log()
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "procedure": procedure,
            "step_number": step_number,
            "validation_result": "pass" if passed else "fail",
            "validation_tool": "step_gate",
            "error_details": error,
            "category": "validation_error",
            "severity": "medium" if not passed else "low",
        }
        data["entries"].append(entry)
        if len(data["entries"]) > MAX_LOG_ENTRIES:
            data["entries"] = data["entries"][-MAX_LOG_ENTRIES:]
        data["summary"] = self._recompute_summary(data)
        self._write_log(data)

    def get_step_stats(self, procedure: str, step_number: int) -> dict[str, Any]:
        """Return pass/fail stats for a specific step within a procedure.

        Only counts entries within the failure window. Returns a dict
        with total, passes, failures, and success_rate.
        """
        data = self._read_log()
        now = datetime.now(UTC)
        window_start = now - timedelta(days=FAILURE_WINDOW_DAYS)
        total = 0
        passes = 0
        failures = 0
        for entry in data.get("entries", []):
            if (entry.get("procedure") != procedure
                    or entry.get("step_number") != step_number):
                continue
            ts = entry.get("timestamp", "")
            try:
                entry_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                continue
            if entry_dt < window_start:
                continue
            total += 1
            if entry.get("validation_result") == "pass":
                passes += 1
            else:
                failures += 1
        return {
            "procedure": procedure,
            "step_number": step_number,
            "total": total,
            "passes": passes,
            "failures": failures,
            "success_rate": passes / total if total > 0 else 0.0,
        }

    def get_failing_steps(self, procedure: str) -> list[dict[str, Any]]:
        """Return steps within a procedure that have exceeded the step
        failure threshold.

        These are specific steps that consistently fail validation and
        should be re-researched or rewritten. The autonomous researcher
        can use this to target the exact step that needs fixing, not
        the whole procedure.
        """
        data = self._read_log()
        now = datetime.now(UTC)
        window_start = now - timedelta(days=FAILURE_WINDOW_DAYS)
        step_failures: dict[int, int] = defaultdict(int)
        for entry in data.get("entries", []):
            if entry.get("procedure") != procedure:
                continue
            step_num = entry.get("step_number")
            if step_num is None:
                continue
            ts = entry.get("timestamp", "")
            try:
                entry_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                continue
            if entry_dt < window_start:
                continue
            if entry.get("validation_result") == "fail":
                step_failures[step_num] += 1
        return [
            {
                "procedure": procedure,
                "step_number": step_num,
                "failures": count,
            }
            for step_num, count in sorted(step_failures.items())
            if count >= STEP_FAILURE_THRESHOLD
        ]

    # --- Failure-driven evolution ---

    def get_failing_procedures(self) -> list[dict[str, Any]]:
        """Return procedures that have exceeded the failure threshold.

        These should be re-researched by the autonomous researcher.
        """
        data = self._read_log()
        summary = data.get("summary", {})
        failing = []
        for proc, stats in summary.items():
            if proc == "no_procedure":
                continue
            if stats.get("failures", 0) >= FAILURE_THRESHOLD:
                failing.append({
                    "procedure": proc,
                    "failures": stats["failures"],
                    "passes": stats.get("passes", 0),
                    "total": stats.get("total", 0),
                    "last_failure": stats.get("last_failure"),
                })
        return failing

    # --- Procedural gap detection ---

    def get_procedural_gaps(self) -> list[dict[str, Any]]:
        """Return task types that have failures but no procedure in context.

        These are tasks where a procedure is needed but doesn't exist yet.
        The autonomous researcher should research "how to [task_type]".
        """
        data = self._read_log()
        summary = data.get("summary", {})
        no_proc = summary.get("no_procedure", {})
        if no_proc.get("failures", 0) >= FAILURE_THRESHOLD:
            task_counts = defaultdict(int)
            for entry in data.get("entries", []):
                if (entry.get("procedure") == "no_procedure"
                        and entry.get("validation_result") == "fail"):
                    task_counts[entry.get("task", "unknown")] += 1
            gaps = []
            for task, count in task_counts.items():
                if count >= FAILURE_THRESHOLD:
                    # Guard: only emit gaps for task values that look like
                    # actual tool/task identifiers, not leaked chat messages
                    # or prose. A raw user message ("keep going yes please...")
                    # would become "how to keep going yes please..." and waste
                    # a web-research cycle. Valid task names are short
                    # identifiers (code_run, safe_write, vault_lint, etc.).
                    if not _is_valid_task_name(task):
                        continue
                    gaps.append({
                        "kind": "procedural_gap",
                        "topic": f"how to {task}",
                        "priority": count * 10,
                        "failure_count": count,
                    })
            return gaps
        return []

    # --- Time-driven re-research ---

    def get_stale_procedures(self, vault_path: str = ".") -> list[dict[str, Any]]:
        """Return procedural notes whose last_reviewed date is older than
        their review_interval_days.

        This is the time-driven evolution mechanism -- purely mechanical
        date comparison.
        """
        stale = []
        now = datetime.now(UTC)
        for md, fm, _text in self._iter_procedural_notes(vault_path):
            try:
                last_reviewed = None
                interval = 90
                for line in fm.split("\n"):
                    if line.strip().startswith("last_reviewed:"):
                        date_str = (line.split(":", 1)[1].strip()
                                    .strip('"').strip("'"))
                        try:
                            last_reviewed = datetime.fromisoformat(date_str)
                        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                            pass
                    elif line.strip().startswith("review_interval_days:"):
                        try:
                            interval = int(line.split(":", 1)[1].strip())
                        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                            pass
                if last_reviewed is None:
                    continue
                if last_reviewed.tzinfo is None:
                    last_reviewed = last_reviewed.replace(tzinfo=UTC)
                age_days = (now - last_reviewed).days
                if age_days > interval:
                    stale.append({
                        "procedure": md.stem,
                        "file_path": str(md),
                        "age_days": age_days,
                        "interval": interval,
                    })
            except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                continue
        return stale

    # --- Quality-driven promotion ---

    def check_promotion(self, procedure: str) -> str | None:
        """Check if a procedure should be promoted or flagged.

        Returns:
            "verified" if it should be promoted (success rate >= 70%)
            "flagged" if it should be re-researched (success rate < 40%)
            None if not enough data yet (< 5 uses)
        """
        data = self._read_log()
        summary = data.get("summary", {})
        stats = summary.get(procedure, {})
        total = stats.get("total", 0)
        if total < PROMOTION_THRESHOLD:
            return None
        success_rate = stats.get("passes", 0) / total if total > 0 else 0
        if success_rate >= PROMOTION_SUCCESS_RATE:
            return "verified"
        elif success_rate < DEMOTION_SUCCESS_RATE:
            return "flagged"
        return None

    def run_promotion_cycle(self, vault_path: str = ".") -> dict[str, list[str]]:
        """Scan all procedural notes in the vault and update their frontmatter.

        For each procedural note:
        - If check_promotion() returns "verified", update status to "verified"
          and write the current success stats into frontmatter.
        - If check_promotion() returns "flagged", update status to "flagged"
          and write the current failure stats into frontmatter.
        - Otherwise, leave the note unchanged (not enough data yet).

        This is called by the autonomous researcher after each cycle.
        It is purely deterministic: read stats, compare to thresholds,
        write frontmatter. No LLM judgment.

        Returns:
            {"promoted": [...], "flagged": [...], "unchanged": [...]}
        """
        result = {"promoted": [], "flagged": [], "unchanged": []}
        for md, fm, _text in self._iter_procedural_notes(vault_path):
            try:
                proc_name = md.stem
                promotion = self.check_promotion(proc_name)

                if promotion is None:
                    result["unchanged"].append(proc_name)
                    continue

                # Get current stats from the log
                data = self._read_log()
                stats = data.get("summary", {}).get(proc_name, {})
                total = stats.get("total", 0)
                passes = stats.get("passes", 0)
                failures = stats.get("failures", 0)
                rate = passes / total if total > 0 else 0.0

                if promotion == "verified":
                    # Only update if not already verified
                    if "status: verified" not in fm:
                        update_frontmatter(md, {
                            "status": "verified",
                            "success_count": passes,
                            "failure_count": failures,
                            "success_rate": round(rate, 2),
                        })
                        result["promoted"].append(proc_name)
                    else:
                        result["unchanged"].append(proc_name)
                elif promotion == "flagged":
                    # Only update if not already flagged
                    if "status: flagged" not in fm:
                        update_frontmatter(md, {
                            "status": "flagged",
                            "success_count": passes,
                            "failure_count": failures,
                            "success_rate": round(rate, 2),
                        })
                        result["flagged"].append(proc_name)
                    else:
                        result["unchanged"].append(proc_name)
            except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                continue

        return result

    def reset_failures(self, procedure: str):
        """Reset failure count for a procedure after re-research.

        Removes all entries for this procedure from the log so the
        counter starts fresh after an update.
        """
        data = self._read_log()
        data["entries"] = [e for e in data.get("entries", [])
                           if e.get("procedure") != procedure]
        data["summary"] = self._recompute_summary(data)
        self._write_log(data)

    def update_after_research(self, procedure: str, vault_path: str = ".") -> bool:
        """Update a procedural note's frontmatter after it's been re-researched.

        - Sets status back to "experimental" (fresh slate after update)
        - Updates last_reviewed to today's date
        - Resets success_count, failure_count, success_rate to 0
        - Resets the failure log entries for this procedure

        This is called by the autonomous researcher after successfully
        re-researching a failing or stale procedure.

        Returns True if the note was found and updated, False otherwise.
        """
        Path(vault_path)
        today = datetime.now(UTC).strftime("%Y-%m-%d")

        # Find the procedural note file by stem. The shared iterator walks
        # the vault once with the ignore-dir filter; we early-exit on match.
        for md, fm, _text in self._iter_procedural_notes(vault_path):
            if md.stem != procedure:
                continue
            try:
                # Update frontmatter
                update_frontmatter(md, {
                    "status": "experimental",
                    "last_reviewed": today,
                    "success_count": 0,
                    "failure_count": 0,
                    "success_rate": 0.0,
                })

                # Reset the failure log for this procedure
                self.reset_failures(procedure)

                return True
            except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                continue

        return False

    # --- Combined gap report (for the autonomous researcher) ---

    def get_research_gaps(self, vault_path: str = ".") -> list[dict[str, Any]]:
        """Return all gaps the autonomous researcher should address.

        Combines:
        1. Failing procedures (re-research the procedure's topic)
        2. Failing steps within procedures (re-research the specific step)
        3. Procedural gaps (find a procedure for a task type)
        4. Stale procedures (re-research on schedule)

        Returns a prioritized list suitable for feeding into the
        autonomous researcher's cycle.
        """
        gaps = []

        # Failing procedures -> re-research
        for proc in self.get_failing_procedures():
            gaps.append({
                "kind": "failing_procedure",
                "topic": proc["procedure"],
                "priority": proc["failures"] * 15,
                "procedure": proc["procedure"],
                "failures": proc["failures"],
            })

        # Failing steps -> re-research the specific step
        for proc in self.get_failing_procedures():
            for step in self.get_failing_steps(proc["procedure"]):
                gaps.append({
                    "kind": "failing_step",
                    "topic": f"{proc['procedure']} step {step['step_number']}",
                    "priority": step["failures"] * 12,
                    "procedure": proc["procedure"],
                    "step_number": step["step_number"],
                    "failures": step["failures"],
                })

        # Procedural gaps -> find a new procedure
        gaps.extend(self.get_procedural_gaps())

        # Stale procedures -> re-research on schedule
        for stale in self.get_stale_procedures(vault_path):
            gaps.append({
                "kind": "stale_procedure",
                "topic": stale["procedure"],
                "priority": 5,  # lower priority than failing ones
                "procedure": stale["procedure"],
                "age_days": stale["age_days"],
            })

        return gaps


# --- Standalone helper (used by main.py after FUSED retrieval) ---

def parse_procedures_from_results(results: list[dict[str, Any]]) -> list[str]:
    """Extract procedure note names from FUSED retrieval results.

    Checks each retrieved note's frontmatter for `type: procedure`.
    Returns a list of note stems (titles) that are procedures.

    This is the "procedure context tracking" piece: after retrieval,
    we know which procedures were in the vault context for this turn.

    Uses the ``content`` field carried by each result (a bounded preview
    cached at index time by the indexer) instead of re-reading the file
    from disk — the frontmatter is always in the first few hundred chars,
    well within the 2000-char preview. Falls back to a disk read only if
    the result carries no content at all.
    """
    procedures = []
    for r in results:
        if not isinstance(r, dict):
            continue
        fp = r.get("file_path", "")
        if not fp:
            continue
        # Prefer the in-result content (cached preview) over a disk read.
        text = r.get("content") or r.get("snippet") or ""
        if not text:
            try:
                text = Path(fp).read_text(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                continue
        if not text.startswith("---"):
            continue
        end = text.find("---", 3)
        if end == -1:
            continue
        fm = text[3:end]
        if "type: procedure" in fm:
            procedures.append(Path(fp).stem)
    return procedures


# --- Validation result interpretation (used by main.py after tool calls) ---

def interpret_validation_result(tool_name: str, tool_result: dict) -> tuple:
    """Interpret a tool result as pass/fail for procedure tracking.

    Returns (validation_result, category, error_details).
    - validation_result: "pass" or "fail"
    - category: structured failure category
    - error_details: human-readable description of what failed

    Only called for tools that produce validation signals:
    vault_lint, safe_write, code_run.
    """
    if not isinstance(tool_result, dict):
        return ("pass", "validation_error", "")

    if tool_name == "vault_lint":
        # vault_lint returns a report with broken wikilinks, quality issues, etc.
        broken = tool_result.get("broken_wikilinks", [])
        has_frontmatter = tool_result.get("has_frontmatter", True)
        passes_quality = tool_result.get("passes_quality", True)
        issues = []
        if broken:
            issues.append(f"{len(broken)} broken wikilinks")
        if not has_frontmatter:
            issues.append("missing frontmatter")
        if not passes_quality:
            issues.append("argument quality issues")
        if issues:
            category = "broken_wikilinks" if broken else (
                "missing_frontmatter" if not has_frontmatter else "argument_quality")
            return ("fail", category, "; ".join(issues))
        return ("pass", "validation_error", "")

    if tool_name == "safe_write":
        status = tool_result.get("status", "")
        if status in ("written", "dry_run_ok"):
            return ("pass", "validation_error", "")
        elif status in ("rejected", "error"):
            error = tool_result.get("error", "")
            if "syntax" in error.lower():
                return ("fail", "syntax_error", error)
            elif "import" in error.lower():
                return ("fail", "import_error", error)
            return ("fail", "validation_error", error)
        return ("pass", "validation_error", "")

    if tool_name == "js_safe_write":
        status = tool_result.get("status", "")
        if status in ("written", "dry_run_ok"):
            return ("pass", "validation_error", "")
        elif status in ("rejected", "error"):
            error = tool_result.get("error", "")
            if "syntax" in error.lower() or "SyntaxError" in error:
                return ("fail", "syntax_error", error)
            return ("fail", "validation_error", error)
        return ("pass", "validation_error", "")

    if tool_name == "code_run":
        exit_code = tool_result.get("exit_code", 0)
        if exit_code != 0:
            stderr = tool_result.get("stderr", "")[:200]
            return ("fail", "syntax_error", f"exit {exit_code}: {stderr}")
        return ("pass", "validation_error", "")

    # Not a validation tool
    return ("pass", "validation_error", "")
