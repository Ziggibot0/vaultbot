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

# --- Token-economy condense mode ---
# llm        = always LLM (raises if no LLM client or LLM fails)
# extractive = never call LLM, always TF-IDF sentence selection
# There is no "auto" mode — it silently degraded LLM→extractive on failure.
_CONDENSE_MODE = os.getenv("VAULTBOT_CONDENSE_MODE", "llm").lower()

# Scaffolding sentence patterns to drop during extractive condense.
_SCAFFOLDING_PATTERNS = re.compile(
    r"^\s*(let's review|let us review|recall that|now we turn to|having established|"
    r"as we have seen|as mentioned earlier|to summarize|in this section we|"
    r"it is worth noting|note that we|we begin by|we have already)",
    re.IGNORECASE,
)
# Lines that MUST be preserved (contain wikilinks, formulas, or definitions).
_MUST_KEEP_PATTERNS = re.compile(
    r"(\[\[|\$\$?|is defined as|means that|is called|consists of|is equal to)"
)

# TF-IDF stopword set for extractive condense sentence scoring.
_CONDENSE_STOPWORDS = {
    "the", "and", "for", "are", "was", "were", "but", "not", "you", "that",
    "this", "with", "from", "they", "have", "has", "had", "its", "it",
    "is", "be", "been", "being", "as", "at", "by", "an", "or", "if", "so",
    "do", "does", "did", "about", "we", "us", "our", "chapter", "section",
    "figure", "table", "example", "exercise", "note", "see", "shown",
    "shows", "using", "use", "used", "one", "two", "three", "first",
    "second", "third", "also", "these", "those", "which", "who", "whom",
    "what", "when", "where", "why", "how", "will", "would", "could",
    "should", "may", "might", "must", "can", "into", "upon", "such",
    "very", "more", "most", "some", "any", "all", "both", "each", "other",
}


class LazyCondenser:
    """Lazy de-fluff: condense notes only after they're queried repeatedly."""

    def __init__(self, vault_path: str, ollama_client=None,
                 session_logger=None,
                 state_path: str | None = None) -> None:
        self.vault_path = Path(vault_path).resolve()
        self.ollama_client = ollama_client
        self.session_logger = session_logger
        if state_path is None:
            self.state_path = Path(__file__).parent / "touch_counts.json"
        else:
            self.state_path = Path(state_path)
        self.touch_counts: dict[str, int] = self._load_touch_counts()
        # Dirty flag: note_touched() only marks the in-memory dict dirty;
        # the caller flushes once per chat turn via flush_touch_counts().
        # This avoids writing the whole JSON file once per retrieved note.
        self._dirty: bool = False

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def note_touched(self, note_path: str) -> None:
        """Record that a note was retrieved for chat. Never raises.

        Increments the per-note touch counter in memory and marks the state
        dirty; the caller is responsible for calling `flush_touch_counts()`
        once per chat turn so the JSON file is written at most once, not once
        per retrieved note.  Does NOT trigger a condense — the caller (chat
        loop) decides when to run `maybe_condense_async` so the LLM cost never
        blocks the answer.
        """
        try:
            key = self._key(note_path)
            if not key:
                return
            self.touch_counts[key] = self.touch_counts.get(key, 0) + 1
            self._dirty = True
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            self._log_error("note_touched_failed", e, {"path": note_path})

    def flush_touch_counts(self) -> None:
        """Persist touch counts if dirty. Never raises. Call once per chat turn."""
        try:
            if self._dirty:
                self._save_touch_counts()
                self._dirty = False
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            self._log_error("flush_touch_counts_failed", e)

    def needs_condense(self, note_path: str) -> bool:
        """True if the note should be condensed (enough touches + long enough
        + not already condensed + a condense method available).

        In ``auto``/``llm`` mode, needs an LLM client. In ``extractive`` mode,
        works without any LLM (pure TF-IDF sentence selection).
        """
        try:
            key = self._key(note_path)
            if not key:
                return False
            if self.touch_counts.get(key, 0) < TOUCH_THRESHOLD:
                return False
            # In extractive mode, no LLM is needed.
            if _CONDENSE_MODE != "extractive" and self.ollama_client is None:
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
        except (FileNotFoundError, PermissionError, OSError):
            # File access failure — the note doesn't exist or can't be read.
            # Return False (don't condense) rather than crashing the chat loop.
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

            condensed_body = self._condense_body(body, p.stem)
            if not condensed_body:
                out["error"] = "condense_empty"
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
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
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
    # Condense body (mode-routed: LLM or extractive)
    # ------------------------------------------------------------------ #
    def _condense_body(self, body: str, note_title: str) -> str:
        """Route to the configured condense method.

        ``llm`` (default): always LLM (raises if no LLM client or LLM fails).
        ``extractive``: always TF-IDF sentence selection (zero LLM).
        """
        mode = _CONDENSE_MODE
        if mode == "extractive":
            return self._extractive_condense(body)
        if mode == "llm":
            if self.ollama_client is None:
                raise ValueError(
                    "_condense_body: VAULTBOT_CONDENSE_MODE=llm but "
                    "ollama_client is None")
            return self._llm_condense(body, note_title)
        raise ValueError(
            f"_condense_body: unknown VAULTBOT_CONDENSE_MODE={mode!r} "
            f"(use 'llm' or 'extractive')")

    # ------------------------------------------------------------------ #
    # Extractive condense (zero LLM)
    # ------------------------------------------------------------------ #
    def _extractive_condense(self, body: str) -> str:
        """TF-IDF sentence selection condense — zero LLM calls.

        Splits the body into sentences, scores each by content-word density,
        drops scaffolding sentences, and keeps the top ~40% by score in
        document order.  Preserves every line containing [[wikilinks]],
        formulas ($...$, $$...$$), or definitions.  Headings (# / ## / ###)
        are always kept.

        This is dumber than the LLM (it can't rephrase) but perfectly
        preserves wikilinks and formulas — they're in the kept sentences.
        """
        lines = body.splitlines()
        kept: list[str] = []  # lines kept unconditionally (headings, wikilinks, etc.)
        candidates: list[tuple[float, int, str]] = []  # (score, orig_index, line)
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            # Always keep headings.
            if stripped.startswith(("#", "> source:", "> cluster:", "<!--")):
                kept.append(line)
                continue
            # Always keep lines with wikilinks, formulas, or definitions.
            if _MUST_KEEP_PATTERNS.search(stripped):
                kept.append(line)
                continue
            # Drop scaffolding sentences.
            if _SCAFFOLDING_PATTERNS.match(stripped):
                continue
            # Score by content-word density.
            tokens = re.findall(r"\b[a-z][a-z0-9-]{2,}\b", stripped.lower())
            content_words = [t for t in tokens if t not in _CONDENSE_STOPWORDS]
            score = len(content_words) / max(len(tokens), 1) if tokens else 0
            if len(stripped) > 100:
                score *= 1.2  # bonus for substantive sentences
            candidates.append((score, i, line))

        if not candidates:
            return body.strip()

        # Keep top ~40% by score, at least 3.
        target_count = max(3, int(len(candidates) * 0.4))
        ranked = sorted(candidates, key=lambda c: -c[0])
        selected_indices = set(c[1] for c in ranked[:target_count])

        # Rebuild in document order: kept lines + selected candidates.
        result_lines: list[str] = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                if result_lines and result_lines[-1].strip():
                    result_lines.append(line)
                continue
            if line in kept or i in selected_indices:
                result_lines.append(line)

        condensed = "\n".join(result_lines).strip()
        if len(condensed) < CONDENSE_FLOOR_CHARS:
            return body.strip()
        return condensed

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
        # Use the SMALL model — note condensing is a summarization task that
        # doesn't need the big model's reasoning power. The instructions are
        # precise (preserve wikilinks, drop scaffolding) and a small model
        # can follow them. Saves cloud tokens on every condense.
        from llm_client import get_small_client_or_big
        _condense_client = get_small_client_or_big()
        resp = _condense_client.chat(messages, temperature=0.2, stream=False)
        # LLMClient.chat() returns {"response": str, "thinking": str,
        # "tool_calls": list} -- NOT {"message": {"content": ...}} which is
        # the raw Ollama /api/chat shape.  The old code read ["message"]
        # ["content"] which KeyError'd on every call -> text="" -> the
        # entire lazy-condense feature was silently dead.
        try:
            text = resp.get("response", "") if isinstance(resp, dict) else str(resp)
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            text = str(resp) if resp else ""
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
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            # If not under vault root, use the absolute path as-is.
            return str(note_path).replace("\\", "/")

    def _load_touch_counts(self) -> dict[str, int]:
        try:
            if self.state_path.exists():
                return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            pass
        return {}

    def _save_touch_counts(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.touch_counts, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self.state_path)
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
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
                    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                        pass
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            return False

    def _log_event(self, event: str, data: dict) -> None:
        try:
            if self.session_logger is not None and hasattr(self.session_logger, "log"):
                self.session_logger.log(event, data)
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            pass

    def _log_error(self, event: str, err: Exception,
                   extra: dict | None = None) -> None:
        try:
            if self.session_logger is not None and hasattr(self.session_logger, "log"):
                self.session_logger.log(event, {"error": str(err),
                                                **(extra or {})})
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            pass
