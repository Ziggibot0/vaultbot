"""Idle-time QA worker for vault notes.

Runs in the background after each chat reply finishes, while the user reads
and types their next message.  The worker pulls notes from a priority queue
(ordered by usage — most-retrieved first, never-retrieved last) and runs
quality assurance on each note's frontmatter.  Issues found are fixed in
place (missing fields auto-injected, weak summaries upgraded via small
model, missing tags generated).

**Interruptible**: the worker checks an ``asyncio.Event`` after every note.
When the user sends a new message, the WS router sets the event and the
worker stops within one note's processing time.  The current note is
completed (not abandoned) so no file is left half-written.  Unprocessed
notes stay in the queue for the next idle window.

**Queue persistence**: the queue is a JSON file on disk so it survives
restarts.  On boot, the queue is rebuilt from touch_counts.json (priority
order) intersected with notes that have weak frontmatter.

Design (see /memories/session/plan.md):
- Triggered after ``answer_done`` fires, before the user sends next message.
- Priority: most-used notes first (touch_counts), never-retrieved last.
- Each note: run vault_lint-style checks → fix issues → write back.
- Small model call ONLY if summary is weak (just title, no descriptive
  content) or tags are generic (only type+dir).  One call per weak note.
- Worker stops the moment a new WS message arrives — in-flight note is
  completed, rest stay queued.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import threading
from pathlib import Path
from typing import Any

# ── Constants ────────────────────────────────────────────────────────

# Queue file: persists the QA work list across restarts.
_QUEUE_FILE = Path(__file__).parent / "qa_queue.json"

# How many notes to process per idle window before yielding.
# (The interrupt check happens after every note, so this is just a cap
# to avoid running forever if the user never sends another message.)
_MAX_PER_IDLE_WINDOW = 50

# Minimum summary length to be considered "good enough" (chars).
_MIN_GOOD_SUMMARY_LEN = 25

# Summary is "weak" if it's just the H1 title or starts with "Chat:".
_WEAK_SUMMARY_PREFIXES = ("Chat:",)

# Tags are "weak" if they only contain type + directory name generics.
_GENERIC_TAGS = frozenset(
    {
        "research",
        "chat",
        "semantic",
        "claim",
        "architecture",
        "procedure",
        "exemplar",
        "pattern",
        "concept",
        "diagnostic",
        "synthesis",
        "roadmap",
        "plan",
        "audit",
        "bridge",
        "pattern-highway",
        "system-design",
        "architecture-plan",
        "research-note",
    }
)


# ── Interrupt signal ─────────────────────────────────────────────────


class QAInterrupt:
    """Thread-safe interrupt signal for the QA worker.

    The WS router calls ``trigger()`` when a new message arrives.
    The worker calls ``is_triggered()`` after each note.
    The chat handler calls ``reset()`` when it starts a new idle window.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def trigger(self) -> None:
        """Signal the worker to stop after the current note."""
        self._event.set()

    def reset(self) -> None:
        """Clear the interrupt for a new idle window."""
        self._event.clear()

    def is_triggered(self) -> bool:
        """Check if the worker should stop."""
        return self._event.is_set()


# Global singleton — one interrupt for the whole backend.
_qa_interrupt = QAInterrupt()


def get_qa_interrupt() -> QAInterrupt:
    """Return the global QA interrupt singleton."""
    return _qa_interrupt


# ── Queue management ─────────────────────────────────────────────────


def _touch_counts_path() -> Path:
    """Path to the lazy_condenser's touch_counts.json."""
    return Path(__file__).parent / "touch_counts.json"


def _load_touch_counts() -> dict[str, int]:
    """Load the touch counts from lazy_condenser's state file."""
    try:
        p = _touch_counts_path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return {k: int(v) for k, v in data.items()}
    except Exception:  # noqa: BLE001
        pass
    return {}


def _scan_vault_notes(vault_root: Path) -> list[str]:
    """Walk the vault and return all .md file paths (relative to vault root)."""
    IGNORED = {
        ".venv",
        "vaultbot_venv",
        "vaultbot_index",
        "sessions",
        "partials",
        ".git",
        ".obsidian",
        "node_modules",
        "__pycache__",
        "vaultbot_backend",
        ".vscode",
        "trash",
        ".github",
        "learningMaterial",
    }
    # NOTE: narrowed from ("vaultbot/", "User/") — the broad "vaultbot/" prefix
    # matched the repo's SOURCE docs (vaultbot/README.md, vaultbot/docs/*.md,
    # vaultbot-stuff/baseline/*.md) and QA'd them as vault notes. Only actual
    # knowledge zones are QA'd.
    ALLOWED = (
        "vaultbot-stuff/Knowledge/",
        "vaultbot-stuff/Memory/",
        "vaultbot-stuff/System/",
        "User/",
    )
    notes: list[str] = []
    for root, dirs, files in os.walk(vault_root):
        dirs[:] = [d for d in dirs if d not in IGNORED]
        for fname in files:
            if not fname.endswith(".md"):
                continue
            fpath = Path(root) / fname
            rel = str(fpath.relative_to(vault_root)).replace("\\", "/")
            if not any(rel.startswith(p) for p in ALLOWED):
                continue
            notes.append(rel)
    return notes


# Directories that are ephemeral/derived and should NOT go through the QA
# frontmatter pipeline. Session log events are conversation traces, not
# knowledge notes — they get consolidated by the semantic consolidation
# pipeline (hippocampal replay), not QA'd for frontmatter quality.
_QA_EXCLUDE_DIRS = ("vaultbot-stuff/Memory/Logs",)

# Cap the QA queue so it actually drains. Without a cap, a large vault fills
# the queue with every note (thousands), the QA worker processes 50 per idle
# window, and the autonomous researcher refuses to run until the queue is
# empty — a permanent deadlock. The cap keeps QA focused on the most-used
# notes (highest touch counts) so the queue drains in a few idle windows.
_QA_QUEUE_CAP = 200


def build_qa_queue(vault_root: str | Path) -> list[dict[str, Any]]:
    """Build the QA queue: vault notes, ordered by usage (most first).

    Each entry: ``{"path": "vaultbot/.../Note.md", "touch_count": N}``
    Notes with higher touch counts come first.  Notes never retrieved
    (touch_count=0) come last.

    Excludes ephemeral directories (Memory/Chat — consolidated by the
    semantic pipeline, not QA'd) and caps at ``_QA_QUEUE_CAP`` entries so
    the queue drains in a few idle windows instead of deadlocking the
    autonomous researcher.
    """
    vault = Path(vault_root).resolve()
    touch_counts = _load_touch_counts()
    all_notes = _scan_vault_notes(vault)

    entries: list[dict[str, Any]] = []
    for rel in all_notes:
        # Skip excluded (ephemeral/derived) directories
        if any(rel.replace("\\", "/").startswith(ex) for ex in _QA_EXCLUDE_DIRS):
            continue
        # Match touch_counts keys (which are resolved absolute paths)
        abs_path = str((vault / rel).resolve())
        tc = touch_counts.get(abs_path, touch_counts.get(rel, 0))
        entries.append({"path": rel, "touch_count": tc})

    # Sort: highest touch_count first, then alphabetical for stable order
    entries.sort(key=lambda e: (-e["touch_count"], e["path"]))

    # Cap the queue — keep only the top N most-used notes.
    if len(entries) > _QA_QUEUE_CAP:
        entries = entries[:_QA_QUEUE_CAP]

    return entries


def save_qa_queue(queue: list[dict[str, Any]]) -> None:
    """Persist the QA queue to disk."""
    with contextlib.suppress(Exception):
        _QUEUE_FILE.write_text(json.dumps(queue, indent=2), encoding="utf-8")


def load_qa_queue() -> list[dict[str, Any]]:
    """Load the persisted QA queue from disk."""
    try:
        if _QUEUE_FILE.exists():
            return json.loads(_QUEUE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return []


# ── QA checks (deterministic, no LLM) ────────────────────────────────


def _parse_fm(text: str) -> dict[str, Any]:
    """Parse frontmatter (reuses note_schema if available)."""
    try:
        from note_schema import parse_frontmatter

        return parse_frontmatter(text)
    except ImportError:
        return {}


def _is_summary_weak(fm: dict[str, Any], body: str) -> bool:
    """Check if a note's summary is weak (just the title, no description)."""
    summary = fm.get("summary", "")
    if not summary:
        return True
    if len(summary) < _MIN_GOOD_SUMMARY_LEN:
        return True
    for prefix in _WEAK_SUMMARY_PREFIXES:
        if summary.startswith(prefix):
            return True
    # Check if summary is just the H1 title
    h1 = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    if h1:
        title = h1.group(1).strip()
        if summary.strip() == title.strip():
            return True
    return False


def _are_tags_weak(fm: dict[str, Any]) -> bool:
    """Check if tags are only generic (type + dir name, no topic tags)."""
    tags = fm.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]
    if not tags:
        return True
    non_generic = [t for t in tags if t.lower() not in _GENERIC_TAGS]
    return len(non_generic) == 0


def qa_check_note(
    content: str,
    file_path: str,
) -> dict[str, Any]:
    """Run deterministic QA checks on a note.

    Uses ``vault_lint`` (the existing custom tool) for broken wikilinks,
    argument quality, and schema validation — NOT a re-implementation.
    Adds two lightweight checks that vault_lint doesn't cover:
    ``weak_summary`` and ``weak_tags`` (for LLM-assisted fixes).

    Returns a dict with:
        ``issues`` (list[str]): descriptions of issues found.
        ``needs_llm`` (bool): True if summary/tags need an LLM call to fix.
        ``lint_report`` (dict): full vault_lint report (if available).
    """
    fm = _parse_fm(content)
    body = re.sub(r"^\s*---.*?---\s*", "", content, count=1, flags=re.DOTALL)

    issues: list[str] = []

    # --- Delegate to vault_lint for existing checks ---
    lint_report: dict[str, Any] = {}
    try:
        import sys as _sys

        backend_dir = Path(__file__).parent
        if str(backend_dir) not in _sys.path:
            _sys.path.insert(0, str(backend_dir))
        from custom_tools.vault_lint import run as lint_run

        lint_report = lint_run({"file_path": file_path})
        for issue in lint_report.get("issues", []):
            itype = issue.get("type", "")
            if itype == "broken_wikilinks":
                issues.append("broken_wikilinks")
            elif itype == "missing_frontmatter":
                issues.append("missing_frontmatter")
            elif itype == "too_short":
                issues.append("too_short")
            elif itype == "no_wikilinks":
                issues.append("no_wikilinks")
            elif itype == "no_reasoning_language":
                issues.append("no_reasoning_language")
            elif itype == "schema_error":
                issues.append(f"schema_error:{issue.get('message', '')}")
            elif itype == "schema_warning":
                issues.append("schema_warning")
    except Exception:  # noqa: BLE001
        # vault_lint unavailable — fall back to inline checks
        from note_schema import REQUIRED_FIELDS

        for field in REQUIRED_FIELDS:
            if field not in fm:
                issues.append(f"missing {field}")
        clean = re.sub(r"```.*?```", "", content, flags=re.DOTALL)
        wikilinks = re.findall(r"\[\[([^\]|]+)", clean)
        if len(wikilinks) == 0:
            issues.append("no_wikilinks")

    # --- QA worker's own checks (not in vault_lint) ---
    if _is_summary_weak(fm, body):
        issues.append("weak_summary")

    if _are_tags_weak(fm):
        issues.append("weak_tags")

    needs_llm = "weak_summary" in issues or "weak_tags" in issues

    return {
        "issues": issues,
        "needs_llm": needs_llm,
        "lint_report": lint_report,
    }


# ── LLM-assisted summary/tag generation ──────────────────────────────


def _run_procedure_sync(
    procedure_name: str,
    args: dict[str, Any],
    vault_root: Path,
) -> dict[str, Any] | None:
    """Run a procedure synchronously via run_procedure.py subprocess.

    Returns the parsed JSON result or None on failure.
    """
    import subprocess
    import sys

    from subprocess_utils import scrubbed_env

    venv_py = sys.executable
    run_proc = Path(__file__).parent / "run_procedure.py"
    if not run_proc.exists():
        return None

    try:
        cmd = [
            str(venv_py),
            str(run_proc),
            "--procedure-name",
            procedure_name,
            "--vault-path",
            str(vault_root),
            "--procedure-args",
            json.dumps(args),
        ]
        # Scrubbed env: run_procedure.py executes LLM-authored procedure code
        # and must not inherit API keys/tokens/passwords.
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(Path(__file__).parent),
            env=scrubbed_env(),
        )
        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return None
    except Exception:  # noqa: BLE001
        pass
    return None


def _generate_summary_and_tags(
    content: str,
    file_path: str,
    ollama_client: Any,
    vault_root: Path,
) -> tuple[str, list[str]] | None:
    """Generate a descriptive summary and topic tags for a note.

    Uses a single inline small-model call for BOTH summary and tags.
    The ``Note-Tags-From-Content`` procedure exists but is too slow
    (60s subprocess timeout) for the idle-time QA loop.  Instead, we ask
    the small model for both in one call and parse defensively.

    Returns (summary, tags) or None on failure.
    """
    from note_schema import _split_frontmatter

    _, body = _split_frontmatter(content)
    body_snippet = body[:1200].strip()

    prompt = (
        "Analyze this note and produce:\n"
        "1. A one-sentence summary (max 120 chars) describing what the note "
        "SAYS, not just its title. Use a verb.\n"
        "2. 3-5 topic tags (single words, lowercase, no # prefix, no spaces) "
        "for the KEY TOPICS the note covers.\n\n"
        "Format: SUMMARY|||tag1,tag2,tag3\n\n"
        f"Note content:\n{body_snippet}"
    )

    try:
        result = ollama_client.generate(
            prompt=prompt,
            system="You are a metadata generator. Output only the summary "
            "and tags in the exact format requested.",
            stream=False,
            think=False,
        )
        text = result.get("response", "") if isinstance(result, dict) else str(result)
        text = text.strip()
    except Exception:  # noqa: BLE001
        return None

    summary = ""
    tags: list[str] = []

    # Parse: SUMMARY|||tag1,tag2,tag3
    if "|||" in text:
        summary_part, tags_part = text.split("|||", 1)
        summary = summary_part.strip().strip('"').strip("'")[:200]
        raw_tags = tags_part.strip().strip('"').strip("'")
        for t in raw_tags.split(","):
            t = t.strip().lower().strip("#").strip()
            # Reject tags that contain newlines, YAML markers, or are too long
            if t and len(t) > 1 and len(t) <= 30 and "\n" not in t and "---" not in t:
                tags.append(t)
        tags = tags[:5]
    elif text:
        summary = text.strip().strip('"').strip("'")[:200]

    if summary or tags:
        return summary, tags
    return None


# ── Worker ───────────────────────────────────────────────────────────


def _fix_note(
    content: str,
    file_path: str,
    ollama_client: Any | None,
    vault_root: Path,
) -> tuple[str, list[str]]:
    """Fix a note's frontmatter issues. Returns (new_content, changes).

    1. Run inject_schema to fill missing required fields (deterministic).
    2. If summary is weak and ollama_client is available, generate one.
    3. If tags are weak and ollama_client is available, generate them.
    """
    from note_schema import (
        _format_frontmatter,
        _split_frontmatter,
        inject_schema,
        parse_frontmatter,
    )

    changes: list[str] = []

    # Step 1: inject missing required fields (deterministic)
    original = content
    content = inject_schema(content, file_path, existing_content=content)
    if content != original:
        changes.append("injected_missing_fields")

    # Step 2: check if summary/tags are still weak
    fm = parse_frontmatter(content)
    _, body = _split_frontmatter(content)

    needs_llm = _is_summary_weak(fm, body) or _are_tags_weak(fm)

    if needs_llm and ollama_client is not None:
        result = _generate_summary_and_tags(
            content, file_path, ollama_client, vault_root
        )
        if result:
            new_summary, new_tags = result
            # Re-inject with the new summary and tags
            if _is_summary_weak(fm, body):
                fm["summary"] = new_summary
                changes.append("generated_summary")
            if _are_tags_weak(fm) and new_tags:
                # Merge new tags with existing non-generic ones
                existing_tags = fm.get("tags", [])
                if isinstance(existing_tags, str):
                    existing_tags = [existing_tags]
                existing_non_generic = [
                    t for t in existing_tags if t.lower() not in _GENERIC_TAGS
                ]
                fm["tags"] = list(dict.fromkeys(existing_non_generic + new_tags))[:8]
                changes.append("generated_tags")

            # Rebuild the note
            new_fm_str = _format_frontmatter(fm)
            content = f"---\n{new_fm_str}\n---\n\n{body.lstrip()}"

    return content, changes


async def run_qa_idle_window(
    vault_root: str | Path,
    ollama_client: Any | None,
    logger: Any = None,
    max_notes: int = _MAX_PER_IDLE_WINDOW,
) -> dict[str, Any]:
    """Run one idle window of QA work.

    Pulls notes from the queue, fixes issues, writes back.  Stops when:
    - The interrupt is triggered (user sent a message).
    - ``max_notes`` reached.
    - Queue is empty.

    Returns a summary dict.
    """
    vault = Path(vault_root).resolve()
    interrupt = get_qa_interrupt()
    interrupt.reset()

    queue = load_qa_queue()
    if not queue:
        queue = build_qa_queue(vault)
        save_qa_queue(queue)

    processed = 0
    fixed = 0
    llm_calls = 0
    skipped = 0
    errors = 0
    remaining: list[dict[str, Any]] = []

    for idx, entry in enumerate(queue):
        if interrupt.is_triggered():
            remaining = queue[idx:]  # unprocessed stay in queue
            if logger:
                logger(
                    f"QA interrupted: stopping after {processed} notes, "
                    f"{len(remaining)} remaining"
                )
            break

        if processed >= max_notes:
            remaining = queue[idx:]
            break

        rel_path = entry["path"]
        full_path = vault / rel_path

        if not full_path.exists():
            processed += 1
            continue

        try:
            content = full_path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            errors += 1
            processed += 1
            continue

        # Run QA check
        qa = qa_check_note(content, rel_path)

        if not qa["issues"]:
            skipped += 1
            processed += 1
            if logger and processed % 10 == 0:
                logger(
                    f"QA progress: {processed} checked, {fixed} fixed, "
                    f"{skipped} skipped"
                )
            continue

        # Fix the note
        try:
            needs_llm = qa["needs_llm"]
            new_content, changes = await asyncio.get_event_loop().run_in_executor(
                None,
                _fix_note,
                content,
                rel_path,
                ollama_client if needs_llm else None,
                vault,
            )
            if needs_llm and "generated_summary" in changes:
                llm_calls += 1

            if new_content != content:
                full_path.write_text(new_content, encoding="utf-8")
                fixed += 1
                if logger:
                    logger(f"QA fixed: {rel_path} ({', '.join(changes)})")
            else:
                skipped += 1
        except Exception as e:  # noqa: BLE001
            errors += 1
            if logger:
                logger(f"QA error on {rel_path}: {e}")

        processed += 1

    # Save remaining queue for next idle window
    save_qa_queue(remaining)

    summary = {
        "processed": processed,
        "fixed": fixed,
        "skipped": skipped,
        "llm_calls": llm_calls,
        "errors": errors,
        "remaining": len(remaining),
    }
    if logger:
        logger(
            f"QA idle window done: processed {processed}, fixed {fixed}, "
            f"skipped {skipped}, llm_calls {llm_calls}, "
            f"errors {errors}, remaining {len(remaining)}"
        )
    return summary
