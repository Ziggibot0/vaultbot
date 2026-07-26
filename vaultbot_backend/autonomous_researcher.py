"""
Autonomous researcher: identifies the vault's own knowledge gaps and fills
them without being asked.

A knowledge gap is one of:
  - A dangling wikilink (a red link the vault itself declared it wants).
  - A thin note (exists but says < min_content_length).
  - A note that links to many dangling concepts.

The researcher runs on a schedule inside the backend. It picks the
highest-priority gap, runs the LLM-light ResearchEngine, writes a research
note under vaultbot/research/, links it back to the notes that referenced
the gap, re-indexes, and repeats — until it runs out of gaps or hits a
budget cap per cycle.

The LLM is NEVER used inside the dig. The LLM only ever sees the finished,
sourced synthesis (when the user later chats about the topic).

Search backend: DuckDuckGo (free, no API key, no signup). Zero setup.
"""

import asyncio
import re
import time
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from research_engine import ResearchEngine
from vault_graph import VaultGraph
from vault_indexer import VaultIndexer
from note_creator import NoteCreator
from session_logger import SessionLogger


# ---------------------------------------------------------------------------
# Gap quality gate — prevents the researcher from wasting cycles on topics
# that are clearly not researchable knowledge concepts.
# ---------------------------------------------------------------------------

# Topics that start with these prefixes are conversation logs or synthetic
# hub proposals, NOT knowledge concepts worth web-researching.
_BAD_TOPIC_PREFIXES = (
    "chat-",          # conversation log titles (Chat-what-can-you-do, etc.)
    "moc for:",       # thin_community synthetic hub proposals
    "moc-for:",       # variant normalization
    "partial_",       # crash-recovery partial answer files
)

# Topics matching these regex patterns are note titles / file artifacts,
# not researchable concepts.
_BAD_TOPIC_PATTERNS = re.compile(
    r"^(?:partial|untitled|draft|todo|tbd|readme|license)$",
    re.IGNORECASE,
)

# Maximum number of words in a researchable topic. Topics with more words
# are almost certainly note titles (e.g. "Giant-pandas-biology-habitat-diet-
# conservation-status-and-ecological-role") not concepts.
_MAX_TOPIC_WORDS = 8

# Minimum number of alphanumeric characters in a topic (catches identifiers
# like "PT2399" while rejecting "to", "up", etc.).
_MIN_TOPIC_ALNUM_CHARS = 3


def _is_researchable_gap(gap: Dict[str, Any]) -> bool:
    """Quality gate: should the autonomous researcher spend a cycle on this?

    This is a SECOND layer of filtering on top of the knowledge curriculum's
    own _is_researchable_topic. The curriculum filters trivial single-word
    topics; this filter catches the structural garbage that slips through:

      - Chat log titles ("Chat-what-can-you-do")
      - MOC proposals ("MOC for: ...")
      - Note titles masquerading as concepts (too many words, file-path-like)
      - Crash recovery artifacts ("partial_...")
      - Empty / whitespace-only topics

    Returns True only if the topic is a genuine knowledge concept worth
    spending a web research cycle on.
    """
    try:
        topic = (gap.get("topic") or "").strip()
        if not topic:
            return False

        # Check bad prefixes (case-insensitive).
        topic_lower = topic.lower()
        for prefix in _BAD_TOPIC_PREFIXES:
            if topic_lower.startswith(prefix):
                return False

        # Check bad exact-match patterns.
        if _BAD_TOPIC_PATTERNS.match(topic):
            return False

        # thin_community gaps are "MOC for: ..." — not web-researchable.
        # They need a different handler (create a hub note), not web search.
        kind = (gap.get("kind") or "").strip()
        if kind == "thin_community":
            return False

        # link_density gaps are structural ("this note has no in-links") —
        # not a knowledge concept to research.
        if kind == "link_density":
            return False

        # Count words (split on hyphens, spaces, underscores — note titles
        # use hyphens as word separators).
        alpha = re.sub(r"[^a-zA-Z0-9\s\-]+", " ", topic).strip()
        if not alpha:
            return False
        # Split on hyphens, spaces, underscores to get the real word count.
        words = re.split(r"[\s\-_]+", alpha)
        words = [w for w in words if w]
        if not words:
            return False
        if len(words) > _MAX_TOPIC_WORDS:
            return False

        # Minimum alphanumeric content (catches "PT2399" but rejects "to").
        alnum = re.sub(r"[^a-zA-Z0-9]+", "", topic)
        if len(alnum) < _MIN_TOPIC_ALNUM_CHARS:
            return False

        return True
    except Exception:
        return False


class AutonomousResearcher:
    """Background loop that researches the vault's own gaps."""

    def __init__(
        self,
        vault_path: str,
        vault_graph: VaultGraph,
        vault_indexer: VaultIndexer,
        note_creator: NoteCreator,
        session_logger: Optional[SessionLogger] = None,
        interval_seconds: int = 600,
        max_researches_per_cycle: int = 2,
        min_dangling_references: int = 1,
        thin_note_threshold: int = 200,
        search_client=None,
        curriculum=None,
        checkpointer=None,
        procedure_tracker=None,
    ):
        self.vault_path = Path(vault_path).resolve()
        self.search_client = search_client
        self.vault_graph = vault_graph
        self.vault_indexer = vault_indexer
        self.note_creator = note_creator
        self.session_logger = session_logger
        self.interval_seconds = interval_seconds
        self.max_researches_per_cycle = max_researches_per_cycle
        self.min_dangling_references = min_dangling_references
        self.thin_note_threshold = thin_note_threshold
        self.curriculum = curriculum  # KnowledgeCurriculum (Voyager-style self-directed growth)
        self.checkpointer = checkpointer  # Checkpointer (crash recovery)
        self.procedure_tracker = procedure_tracker  # ProcedureTracker (failure-driven evolution)

        self.engine = ResearchEngine(
            session_logger=session_logger,
            max_rounds=4,
            max_sources_per_round=5,
            max_follow_ups=3,
            search_client=self.search_client,
        )

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Chat-priority pause: when a chat turn is in flight, the researcher
        # yields the Ollama GPU so the interactive user isn't queued behind
        # background research. The chat loop calls pause_for_chat() at the
        # start of handle_chat and resume_after_chat() when the turn ends.
        # This is a threading.Event (not asyncio) so it works across the
        # researcher's own thread + the main event loop without locks.
        self._chat_active = threading.Event()
        self.last_run: Optional[Dict[str, Any]] = None
        self.history: List[Dict[str, Any]] = []
        self.enabled = True

    def _log(self, event: str, data: Optional[Dict[str, Any]] = None):
        if self.session_logger is None:
            return
        self.session_logger.log(event, data)

    def _identify_gaps(self) -> List[Dict[str, Any]]:
        """Refresh the graph and collect prioritized knowledge gaps.

        If a knowledge curriculum is wired in, use its Voyager-style diversity-
        aware ranking (which tracks completed/failed topics and avoids
        re-proposing what was just filled). Otherwise fall back to the simple
        reference-count ranking on dangling links + thin notes.

        Both paths now run through _is_researchable_gap() to filter out
        non-researchable topics (chat logs, MOC proposals, note titles, etc.)
        BEFORE any cycle is spent on them.
        """
        if self.curriculum is not None:
            try:
                raw_gaps = self.curriculum.propose_next_gaps(
                    n=max(self.max_researches_per_cycle * 5, 10))
                # Filter out non-researchable gaps (chat logs, MOC proposals,
                # link-density anomalies, note-title-as-topic, etc.)
                gaps = [g for g in raw_gaps if _is_researchable_gap(g)]
                skipped = len(raw_gaps) - len(gaps)
                if skipped:
                    self._log("autonomous_gaps_filtered", {
                        "raw_count": len(raw_gaps),
                        "filtered_count": len(gaps),
                        "skipped": skipped,
                        "skipped_topics": [g.get("topic", "")[:80] for g in raw_gaps
                                          if not _is_researchable_gap(g)],
                    })
                self._log("autonomous_curriculum_gaps", {
                    "gap_count": len(gaps),
                    "source": "knowledge_curriculum",
                })
                return gaps
            except Exception as e:
                self._log("autonomous_curriculum_failed", {"error": str(e)})
                # fall through to the legacy ranking below

        try:
            self.vault_graph.refresh()
        except Exception as e:
            self._log("autonomous_graph_refresh_failed", {"error": str(e)})
            return []
        gaps: List[Dict[str, Any]] = []
        for d in self.vault_graph.dangling_links(
                min_references=self.min_dangling_references):
            gaps.append({
                "kind": "dangling_link",
                "topic": d["name"],
                "priority": d["reference_count"] * 10,  # weight heavily
                "referenced_by": d.get("referenced_by", []),
                "normalized_name": d.get("normalized_name", d["name"].lower()),
            })
        for t in self.vault_graph.thin_notes(
                min_content_length=self.thin_note_threshold):
            # Skip notes that are themselves generated research/chat notes —
            # those are the bot's own outputs, not user knowledge to fill.
            if "vaultbot" in Path(t["file_path"]).parts:
                continue
            gaps.append({
                "kind": "thin_note",
                "topic": t["name"],
                "priority": 1,
                "file_path": t["file_path"],
                "normalized_name": t["normalized_name"],
            })
        # Apply the same quality gate to the legacy path.
        gaps = [g for g in gaps if _is_researchable_gap(g)]
        gaps.sort(key=lambda g: g["priority"], reverse=True)
        return gaps

    def _research_to_note(self, gap: Dict[str, Any]) -> Optional[str]:
        """Research one gap and persist a linked note. Returns note path."""
        topic = gap["topic"]
        try:
            report = self.engine.research(topic)
        except Exception as e:
            self._log("autonomous_research_failed",
                      {"topic": topic, "error": str(e)})
            return None
        if not report.get("source_count"):
            self._log("autonomous_research_empty", {"topic": topic})
            return None

        summary = (f"Autonomous research into '{topic}' to fill a "
                   f"{gap['kind']} gap. "
                   f"{report['source_count']} sources, "
                   f"{report['synthesis_facts']} corroborated facts.")
        try:
            markdown = self.engine.synthesize_note_markdown(report, summary)
            note_path = self.note_creator.create_note_from_research(
                topic=topic,
                research_content=report["synthesis"],
                summary=summary,
            )
            # create_note_from_research writes its own structure; overwrite
            # with the richer markdown so we keep sources + follow-ups.
            # Respect the vault write guard (sacred date-only / LOCKED notes).
            try:
                from vault_guard import assert_writable
                assert_writable(Path(note_path))
                Path(note_path).write_text(markdown, encoding="utf-8")
            except Exception:
                pass
            self._log("autonomous_note_created", {
                "topic": topic, "note_path": note_path,
                "sources": report["source_count"],
                "facts": report["synthesis_facts"],
            })
            # Re-index the new note so it's immediately searchable.
            try:
                self.vault_indexer._add_file_to_index(Path(note_path))
            except Exception as e:
                self._log("autonomous_index_failed",
                          {"note_path": note_path, "error": str(e)})
            # Refresh the graph so subsequent gaps see the new note.
            try:
                self.vault_graph.refresh()
            except Exception:
                pass
            # Mark the topic completed in the curriculum BEFORE returning so
            # the diversity-aware ranking actually steers the next cycle
            # elsewhere.
            if self.curriculum is not None:
                try:
                    self.curriculum.mark_completed(topic)
                except Exception:
                    pass
            # --- Phase 3: Update procedural note frontmatter after re-research ---
            # If this gap was a failing or stale procedure, reset its failure
            # count and update its frontmatter (status -> experimental,
            # last_reviewed -> today, stats reset to 0).
            if self.procedure_tracker is not None and gap.get("kind") in (
                    "failing_procedure", "stale_procedure"):
                proc_name = gap.get("procedure", "")
                if proc_name:
                    try:
                        self.procedure_tracker.update_after_research(
                            proc_name, str(self.vault_path))
                        self._log("autonomous_procedure_updated", {
                            "procedure": proc_name,
                            "kind": gap["kind"],
                        })
                    except Exception as e:
                        self._log("autonomous_procedure_update_failed", {
                            "procedure": proc_name, "error": str(e),
                        })
            return note_path
        except Exception as e:
            self._log("autonomous_note_create_failed",
                      {"topic": topic, "error": str(e)})
            # Mark this gap as failed in the curriculum so it isn't
            # re-proposed immediately (Voyager negative memory).
            if self.curriculum is not None:
                try:
                    self.curriculum.mark_failed(topic, str(e)[:200])
                except Exception:
                    pass
            return None

    async def _cycle(self):
        """Run one autonomous research cycle."""
        if not self.enabled:
            return
        # Chat-priority: if an interactive chat turn is in flight, skip this
        # cycle entirely. The user's embedding/LLM calls must not queue behind
        # background research on a single-GPU laptop. The next cycle (after
        # the interval) runs normally once the chat ends.
        if self._chat_active.is_set():
            self._log("autonomous_cycle_skipped_chat_active", {})
            return
        cycle_t0 = time.time()
        if hasattr(self, '_heartbeat'):
            self._heartbeat(f"cycle starting")
        # If there are recovered gaps from a previous crash, research those
        # FIRST before the curriculum proposes new ones. This is the retry.
        recovered = getattr(self, '_recovered_gaps', None)
        if recovered:
            # Filter recovered gaps too — they may have been pre-filter garbage.
            recovered = [g for g in recovered if _is_researchable_gap(g)]
            self._log("autonomous_recovering_interrupted", {
                "count": len(recovered),
                "topics": [g.get("topic") for g in recovered],
            })
            gaps = recovered
            self._recovered_gaps = None  # consume them
        elif self.procedure_tracker is not None:
            # Check for failing/stale procedures and procedural gaps first.
            # These are higher priority than normal knowledge gaps because
            # they represent tasks where the system is actively failing.
            proc_gaps = self.procedure_tracker.get_research_gaps(
                vault_path=str(self.vault_path))
            if proc_gaps:
                gaps = proc_gaps
                self._log("autonomous_procedure_gaps", {
                    "count": len(gaps),
                    "topics": [g.get("topic", "") for g in gaps[:5]],
                })
            else:
                gaps = self._identify_gaps()
        else:
            gaps = self._identify_gaps()
        self._log("autonomous_cycle_begin", {
            "gap_count": len(gaps),
            "top_gaps": [g["topic"] for g in gaps[:5]],
        })
        filled: List[Dict[str, Any]] = []
        budget = min(self.max_researches_per_cycle, len(gaps))
        # Checkpoint the cycle's gaps so a crash mid-research can be recovered.
        cycle_checkpoints = []
        from datetime import datetime, timezone
        now_iso = lambda: datetime.now(timezone.utc).isoformat()
        for gap in gaps[:budget]:
            if self._stop_event.is_set():
                break
            # Chat-priority: stop the cycle mid-way if a chat turn starts,
            # so the remaining researches don't keep Ollama busy. Already-
            # started research finishes; the next gap waits for the next
            # cycle (after the chat ends + the interval).
            if self._chat_active.is_set():
                self._log("autonomous_cycle_paused_mid_cycle", {
                    "completed": len(cycle_checkpoints),
                    "remaining": budget - len(cycle_checkpoints),
                })
                break
            # Mark this gap as 'running' in the checkpoint before researching.
            ckpt = {
                "topic": gap["topic"], "kind": gap["kind"],
                "status": "running", "started_at": now_iso(),
                "completed_at": None, "note_path": None, "error": None,
                "gap": gap,
            }
            cycle_checkpoints.append(ckpt)
            if self.checkpointer is not None:
                try:
                    self.checkpointer.save([
                        __import__("checkpointer").ResearchCheckpoint(**c) for c in cycle_checkpoints
                    ])
                except Exception:
                    pass
            note_path = self._research_to_note(gap)
            # Update the checkpoint with the result.
            ckpt["status"] = "done" if note_path else "failed"
            ckpt["completed_at"] = now_iso()
            ckpt["note_path"] = note_path
            if not note_path:
                ckpt["error"] = "research returned no note"
            filled.append({
                "topic": gap["topic"],
                "kind": gap["kind"],
                "note_path": note_path,
                "ok": note_path is not None,
            })
        # --- Phase 3: Run the promotion cycle after each research cycle ---
        # Scan all procedural notes in the vault, check their success rates,
        # and promote/flag them based on the deterministic thresholds.
        # This is purely mechanical: read stats, compare to thresholds,
        # write frontmatter. No LLM judgment.
        if self.procedure_tracker is not None:
            try:
                promo_result = self.procedure_tracker.run_promotion_cycle(
                    str(self.vault_path))
                if promo_result["promoted"] or promo_result["flagged"]:
                    self._log("autonomous_procedure_promotion", promo_result)
            except Exception as e:
                self._log("autonomous_promotion_cycle_failed",
                          {"error": str(e)})
        self.last_run = {
            "timestamp": time.time(),
            "gap_count": len(gaps),
            "researched": filled,
            "duration_ms": (time.time() - cycle_t0) * 1000,
        }
        self.history.append(self.last_run)
        # Keep history bounded.
        if len(self.history) > 50:
            self.history = self.history[-50:]
        self._log("autonomous_cycle_end", self.last_run)
        # Clear the checkpoint — the cycle completed cleanly.
        if self.checkpointer is not None:
            try:
                self.checkpointer.clear()
            except Exception:
                pass

    async def _run(self):
        """Main loop: sleep, cycle, repeat until stopped."""
        self._loop = asyncio.get_event_loop()
        self._log("autonomous_researcher_start", {
            "interval_seconds": self.interval_seconds,
            "max_per_cycle": self.max_researches_per_cycle,
        })
        # Run an initial cycle shortly after start so the user sees value
        # without waiting the full interval.
        initial_delay = 15
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(initial_delay)
                initial_delay = self.interval_seconds
                await self._cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log("autonomous_researcher_error", {"error": str(e)})
                # Avoid a tight error loop.
                await asyncio.sleep(self.interval_seconds)
        self._log("autonomous_researcher_stop", {})

    def start(self):
        """Start the background researcher thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()

        def runner():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._run())
            except Exception as e:
                self._log("autonomous_thread_crashed", {"error": str(e)})
            finally:
                loop.close()

        self._thread = threading.Thread(
            target=runner, name="autonomous-researcher", daemon=True)
        self._thread.start()

    def stop(self):
        """Signal the background researcher to stop."""
        self._stop_event.set()
        if self._loop:
            try:
                # Nudge any sleeping coroutine.
                for task in asyncio.all_tasks(loop=self._loop):
                    task.cancel()
            except Exception:
                pass

    def status(self) -> Dict[str, Any]:
        """Return a snapshot for the status endpoint."""
        return {
            "enabled": self.enabled,
            "running": bool(self._thread and self._thread.is_alive()),
            "paused_for_chat": self._chat_active.is_set(),
            "interval_seconds": self.interval_seconds,
            "max_researches_per_cycle": self.max_researches_per_cycle,
            "last_run": self.last_run,
            "history_count": len(self.history),
            "recent_history": self.history[-5:],
        }

    def pause_for_chat(self) -> None:
        """Signal the researcher to yield Ollama to an interactive chat turn.

        Sets a flag the researcher checks before starting each research cycle
        and between researches within a cycle. A cycle already in progress
        (mid web-search/scrape) finishes that step first — only the Ollama-
        heavy synthesis is gated. Safe to call repeatedly; resume_after_chat()
        clears it.
        """
        self._chat_active.set()

    def resume_after_chat(self) -> None:
        """Clear the chat-priority pause so the researcher can resume."""
        self._chat_active.clear()

    def trigger_now(self) -> Dict[str, Any]:
        """Run a cycle immediately (synchronously) on demand.

        Used by the /autonomous/trigger endpoint so the user can kick the
        researcher without waiting for the interval.
        """
        if not self.enabled:
            return {"status": "disabled"}
        before = len(self.history)
        # Run a cycle in the researcher's loop if it exists, else inline.
        if self._loop and self._thread and self._thread.is_alive():
            future = asyncio.run_coroutine_threadsafe(self._cycle(), self._loop)
            try:
                future.result(timeout=300)
            except Exception as e:
                return {"status": "error", "error": str(e)}
        else:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._cycle())
            finally:
                loop.close()
        return {
            "status": "ok",
            "ran": len(self.history) > before,
            "last_run": self.last_run,
        }
