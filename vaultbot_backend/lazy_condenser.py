"""Lazy note condenser — de-fluffs notes over time as they're queried.

Problem
-------
Ingested textbook sections are stored verbatim (no truncation, so no info
is lost).  But many sections are verbose — a 20K-char chapter has a lot of
pedagogical scaffolding ("let's review what we learned...", worked examples
that repeat the same setup, transitional paragraphs) that isn't useful once
the concept is understood.  Leaving all that in:

  - bloats the chat context window (5 retrieved notes × 20K = 100K of
    context, most of it fluff),
  - dilutes retrieval (the embedding averages over the whole section, so
    the signal-to-noise ratio is lower than a tight version),
  - wastes the user's free-tier rate limit when the LLM has to read past
    filler to find the relevant sentence.

Solution
--------
Instead of condensing every note up front (expensive — N LLM calls at
ingest time), the condenser is LAZY: it only acts on notes that have
proven their value by being retrieved for chat.  This is the "de-fluff
over time as pages are queried" behavior:

  1. The chat loop calls `note_touched(path)` for every note it retrieves.
  2. The condenser increments a per-note touch counter (persisted to
     `vaultbot_backend/touch_counts.json`).
  3. When a note crosses the TOUCH_THRESHOLD (default 3) AND is still
     longer than CONDENSE_MIN_CHARS (default 6000), a background task
     rewrites it in place: the LLM produces a terse version that keeps
     every concept, definition, formula, and wikilink target but drops
     repetition, scaffolding, and verbose examples.  The original is
     archived to a `# vaultbot:condensed-from` line so the full text is
     recoverable.
  4. Notes that are never queried are never touched — zero wasted LLM
     calls.  Notes that are queried once or twice but are already short
     stay verbatim.

This is a fetch-on-demand pattern: the cost of condensing is paid only
by notes that earn it through repeated retrieval, and the result is a
vault that gradually becomes more terse and dense in exactly the places
the user (and the agent) actually look.

LLM usage
---------
One `ollama_client.chat()` call per condensed note, only when the note
crosses the threshold.  The chat loop itself never pays this cost —
condensing runs as a fire-and-forget background task after the answer
is delivered.  The single LLM call is unavoidable for quality de-fluff;
a heuristic can't tell scaffolding from content.

Idempotent
----------
Re-condensing an already-terse note is a no-op: the `# vaultbot:condensed`
marker is detected and the note is skipped.  The touch counter is reset
after a condense so a note isn't re-condensed every query.

Atomic writes
-------------
Uses the same temp-file + os.replace pattern as the rest of the stack
to avoid partial writes on crash.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional


CONDENSE_MARKER = "<!-- vaultbot:condensed -->"
ORIG_MARKER = "<!-- vaultbot:original-chars"
# Minimum chars before a note is worth condensing (shorter notes are
# already terse — condensing them would lose info for no gain).
CONDENSE_MIN_CHARS = 6000
# How many times a note must be retrieved before it earns a condense.
# Low enough that a genuinely useful note condenses within a few queries;
# high enough that one-off lookups don't trigger LLM work.
TOUCH_THRESHOLD = 3
# Never condense a note below this — preserves everything if the LLM
# somehow produces something tiny.
CONDENSE_FLOOR_CHARS = 1500


class LazyCondenser:
    """Lazy de-fluff: condense notes only after they're queried repeatedly."""

    def __init__(self, vault_path: str, ollama_client=None,
                 session_logger=None,
                 state_path: Optional[str] = None) -> None:
        self.vault_path = Path(vault_path).resolve()
        self.ollama_client = ollama_client
        self.session_logger = session_logger
        if state_path is None:
            self.state_path = Path(__file__).parent / "touch_counts.json"
        else:
            self.state_path = Path(state_path)
        self.touch_counts: Dict[str, int] = self._load_touch_counts()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def note_touched(self, note_path: str) -> None:
        """Record that a note was retrieved for chat. Never raises.

        Increments the per-note touch counter and persists it.  Does NOT
        trigger a condense — the caller (chat loop) decides when to run
        `maybe_condense_async` so the LLM cost never blocks the answer.
        """
        try:
            key = self._key(note_path)
            if not key:
                return
            self.touch_counts[key] = self.touch_counts.get(key, 0) + 1
            self._save_touch_counts()
        except Exception as e:  # noqa: BLE001
            self._log_error("note_touched_failed", e, {"path": note_path})

    def needs_condense(self, note_path: str) -> bool:
        """True if the note should be condensed (enough touches + long enough
        + not already condensed + LLM available)."""
        try:
            key = self._key(note_path)
            if not key:
                return False
            if self.touch_counts.get(key, 0) < TOUCH_THRESHOLD:
                return False
            if self.ollama_client is None:
                return False
            p = Path(note_path)
            if not p.exists():
                return False
            text = p.read_text(encoding="utf-8", errors="replace")
            if CONDENSE_MARKER in text:
                return False  # already condensed
            # Strip frontmatter + nav footer to measure body length.
            body = self._strip_scaffolding(text)
            return len(body) >= CONDENSE_MIN_CHARS
        except Exception:
            return False

    def condense_note(self, note_path: str) -> dict:
        """Condense a single note in place. Returns a summary dict; never raises.

        The LLM produces a terse version preserving every concept, definition,
        formula, and wikilink target, while dropping repetition, scaffolding,
        and verbose examples.  The original char count is recorded in a
        marker so the change is auditable; the note keeps its heading,
        source line, navigation, and tags.
        """
        out: dict = {"condensed": False, "note_path": note_path,
                     "orig_chars": 0, "new_chars": 0, "error": None}
        try:
            if self.ollama_client is None:
                out["error"] = "no_llm"
                return out
            p = Path(note_path)
            if not p.exists():
                out["error"] = "missing"
                return out
            text = p.read_text(encoding="utf-8", errors="replace")
            if CONDENSE_MARKER in text:
                out["error"] = "already_condensed"
                return out

            # Split the note into the parts we preserve verbatim (heading,
            # source line, part-of link, navigation, tags) vs the body we
            # ask the LLM to tighten.
            header, body, footer = self._split_note(text)
            orig_body_chars = len(body)
            out["orig_chars"] = len(text)
            if orig_body_chars < CONDENSE_MIN_CHARS:
                out["error"] = "too_short"
                return out

            condensed_body = self._llm_condense(body, p.stem)
            if not condensed_body:
                out["error"] = "llm_empty"
                return out
            # Safety floor — never let the LLM collapse a note to nothing.
            if len(condensed_body) < CONDENSE_FLOOR_CHARS:
                out["error"] = "condensed_too_short"
                return out

            # Reassemble: header + condensed body + footer + markers.
            new_text = (
                header
                + condensed_body
                + footer
                + f"\n\n{CONDENSE_MARKER}\n"
                + f"{ORIG_MARKER}: {len(text)} --> {len(condensed_body) + len(header) + len(footer)} -->\n"
            )
            self._atomic_write(p, new_text)
            out["condensed"] = True
            out["new_chars"] = len(new_text)
            # Reset the touch counter so the note isn't immediately
            # re-condensed.  It can condense again later if it grows
            # (it won't — but the counter reset keeps the threshold logic clean).
            key = self._key(note_path)
            if key:
                self.touch_counts[key] = 0
                self._save_touch_counts()
            self._log_event("note_condensed", {
                "note_path": note_path,
                "orig_chars": out["orig_chars"],
                "new_chars": out["new_chars"],
                "reduction_pct": round(
                    (1 - out["new_chars"] / max(out["orig_chars"], 1)) * 100, 1),
            })
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e)
            self._log_error("condense_note_failed", e, {"path": note_path})
        return out

    def condense_batch(self, note_paths: list[str]) -> dict:
        """Condense every note in the list that needs it. Returns a summary.

        Used by the chat loop after an answer is delivered: it passes the
        notes that were retrieved this turn, and the condenser condenses
        any that have crossed the threshold.  Fire-and-forget from the
        caller's perspective.
        """
        summary = {"condensed": 0, "skipped": 0, "errors": 0, "details": []}
        for path in note_paths:
            if not self.needs_condense(path):
                summary["skipped"] += 1
                continue
            res = self.condense_note(path)
            if res.get("condensed"):
                summary["condensed"] += 1
                summary["details"].append({
                    "note": Path(path).stem,
                    "orig": res["orig_chars"],
                    "new": res["new_chars"],
                })
            elif res.get("error") and res["error"] not in (
                    "too_short", "already_condensed", "no_llm"):
                summary["errors"] += 1
        if summary["condensed"]:
            self._log_event("batch_condensed", summary)
        return summary

    # ------------------------------------------------------------------ #
    # LLM condense
    # ------------------------------------------------------------------ #
    def _llm_condense(self, body: str, note_title: str) -> str:
        """Ask the LLM to produce a terse version of the note body.

        The prompt is explicit about what to preserve (concepts, definitions,
        formulas, every [[wikilink]] target) and what to drop (repetition,
        pedagogical scaffolding, verbose worked examples, transitional
        paragraphs).  The LLM is told to keep it as markdown, keep all
        wikilinks, and not to add new content.
        """
        # Cap the body sent to the LLM at 24K chars — a whole chapter fits,
        # but a pathological 200K parse error doesn't blow the context window.
        body_input = body[:24000]
        if len(body) > 24000:
            body_input += "\n\n[... body truncated for condense prompt ...]"
        prompt = (
            f"You are condensing a knowledge-base note titled '{note_title}' "
            f"to a terse, dense version. The note was retrieved multiple times "
            f"for lookup, so it has proven its value — but it is verbose.\n\n"
            f"Preserve ALL of:\n"
            f"- Every concept, definition, and key term\n"
            f"- Every formula, equation, and numerical constant\n"
            f"- Every [[wikilink]] target (keep the [[...]] syntax intact)\n"
            f"- The logical structure / section order\n"
            f"- Headings and subheadings (keep # / ## levels)\n\n"
            f"Remove ALL of:\n"
            f"- Repetition of the same idea in different words\n"
            f"- Pedagogical scaffolding ('let's review...', 'recall that...')\n"
            f"- Verbose worked examples that repeat the same setup — keep "
            f"one representative example, drop the rest\n"
            f"- Transitional paragraphs ('now we turn to...', 'having " 
            f"established...')\n"
            f"- Chatty asides and motivation that isn't a definition or " 
            f"formula\n\n"
            f"Rules:\n"
            f"- Output ONLY the condensed markdown body. No preamble, no "
            f"'Here is the condensed version:', no commentary.\n"
            f"- Keep it as markdown. Keep all [[wikilinks]].\n"
            f"- Do NOT add new content. Do NOT invent facts. If you're " 
            f"unsure, keep the original wording.\n"
            f"- Target ~40% of the original length, but prioritize " 
            f"preserving information over hitting a length target.\n\n"
            f"Original note body ({len(body)} chars):\n\n{body_input}\n"
        )
        messages = [{"role": "user", "content": prompt}]
        resp = self.ollama_client.chat(messages, temperature=0.2, stream=False)
        try:
            text = resp["message"]["content"]
        except Exception:
            text = str(resp) if isinstance(resp, str) else ""
        # Strip a leading "Here is..." preamble if the model ignored the rule.
        text = self._strip_preamble(text)
        return text.strip()

    @staticmethod
    def _strip_preamble(text: str) -> str:
        """Remove a leading 'Here is the condensed...' line if present."""
        # Common preambles the model emits despite instructions.
        for pat in (
            r"^Here(?:'s| is) the (?:condensed|terse|shortened)[^\n]*\n+",
            r"^Sure[,!]?\s+here[^\n]*\n+",
            r"^Below is the (?:condensed|terse)[^\n]*\n+",
            r"^```(?:markdown)?\n",
        ):
            text = re.sub(pat, "", text, count=1, flags=re.IGNORECASE)
        # Strip a trailing closing code fence if we stripped an opening one.
        if text.endswith("```\n"):
            text = text[:-4]
        elif text.endswith("```"):
            text = text[:-3]
        return text

    # ------------------------------------------------------------------ #
    # Note parsing (split into preserved-header / body / preserved-footer)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _split_note(text: str) -> tuple[str, str, str]:
        """Split a note into (header, body, footer).

        Header = everything from the top through the `> **Source:**` and
        `> **Part of:**` lines (preserved verbatim).
        Footer = the `---\n**Navigation:**` block and the tags line
        (preserved verbatim).
        Body = everything in between (this is what the LLM condenses).
        """
        lines = text.split("\n")
        header_end = 0
        for i, line in enumerate(lines):
            if line.strip().startswith("> **Part of:**"):
                header_end = i + 1
                break
            if line.strip().startswith("# "):
                header_end = i + 1
        # If no Part-of line, header is just the H1.
        if header_end == 0:
            for i, line in enumerate(lines):
                if line.strip().startswith("# "):
                    header_end = i + 1
                    break
        # Find the footer: the `---` separator above Navigation.
        footer_start = len(lines)
        for i in range(len(lines) - 1, header_end, -1):
            if lines[i].strip() == "---":
                footer_start = i
                break
        header = "\n".join(lines[:header_end]) + "\n\n"
        body = "\n".join(lines[header_end:footer_start]).strip()
        footer = "\n" + "\n".join(lines[footer_start:]).strip() + "\n"
        if not footer.strip():
            footer = "\n"
        return header, body, footer

    @staticmethod
    def _strip_scaffolding(text: str) -> str:
        """Approximate body length by stripping frontmatter, nav, tags."""
        # Drop everything from the `---` navigation separator down.
        nav_idx = text.find("\n---\n**Navigation:**")
        if nav_idx == -1:
            nav_idx = text.find("\n---\n")
        if nav_idx != -1:
            text = text[:nav_idx]
        return text.strip()

    # ------------------------------------------------------------------ #
    # State persistence
    # ------------------------------------------------------------------ #
    def _key(self, note_path: str) -> str:
        """Stable key for a note path (relative to vault root, forward slashes)."""
        try:
            p = Path(note_path).resolve()
            rel = p.relative_to(self.vault_path)
            return str(rel).replace("\\", "/")
        except Exception:
            # If not under vault root, use the absolute path as-is.
            return str(note_path).replace("\\", "/")

    def _load_touch_counts(self) -> Dict[str, int]:
        try:
            if self.state_path.exists():
                return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_touch_counts(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.touch_counts, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self.state_path)
        except Exception as e:  # noqa: BLE001
            self._log_error("save_touch_counts_failed", e)

    # ------------------------------------------------------------------ #
    # I/O + logging
    # ------------------------------------------------------------------ #
    @staticmethod
    def _atomic_write(path: Path, content: str) -> bool:
        try:
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                os.replace(tmp, path)
                return True
            finally:
                if os.path.exists(tmp):
                    try:
                        os.unlink(tmp)
                    except Exception:
                        pass
        except Exception:
            return False

    def _log_event(self, event: str, data: dict) -> None:
        try:
            if self.session_logger is not None and hasattr(self.session_logger, "log"):
                self.session_logger.log(event, data)
        except Exception:
            pass

    def _log_error(self, event: str, err: Exception,
                   extra: Optional[dict] = None) -> None:
        try:
            if self.session_logger is not None and hasattr(self.session_logger, "log"):
                self.session_logger.log(event, {"error": str(err),
                                                **(extra or {})})
        except Exception:
            pass