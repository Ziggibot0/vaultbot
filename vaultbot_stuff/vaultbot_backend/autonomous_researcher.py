"""
Autonomous researcher: identifies the vault's own knowledge gaps and fills
them without being asked.

A knowledge gap is one of:
  - A dangling wikilink (a red link the vault itself declared it wants).
  - A thin note (exists but says < min_content_length).
  - A note that links to many dangling concepts.

The researcher runs on a schedule inside the backend. It picks the
highest-priority gap, runs the LLM-light ResearchEngine, writes a research
note under Knowledge/Research/, links it back to the notes that referenced
the gap, re-indexes, and repeats — until it runs out of gaps or hits a
budget cap per cycle.

The LLM is NEVER used inside the dig. The LLM only ever sees the finished,
sourced synthesis (when the user later chats about the topic).

Search backend: DuckDuckGo (free, no API key, no signup). Zero setup.
"""

import asyncio
import re
import threading
import time
from datetime import UTC
from pathlib import Path
from typing import Any
from collections.abc import Callable

from note_creator import NoteCreator
from research_engine import ResearchEngine
from session_logger import SessionLogger
from vault_graph import VaultGraph
from vault_indexer import VaultIndexer

# Reuse the curriculum's structural filters so the researcher's second-layer
# gate and the curriculum's first-layer gate apply the SAME rules — no
# duplicated, divergent blocklists. The curriculum owns the canonical set
# of placeholder patterns, single-word stopics, and template-var patterns.
from knowledge_curriculum import (
    _PLACEHOLDER_RE as _CURRICULUM_PLACEHOLDER_RE,
    _SINGLE_WORD_STOPICS as _CURRICULUM_SINGLE_WORD_STOPICS,
    _TEMPLATE_VAR_RE as _CURRICULUM_TEMPLATE_VAR_RE,
)

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
# not researchable concepts. Extends the curriculum's _PLACEHOLDER_RE with
# researcher-specific exact-match patterns (file artifacts like README/LICENSE).
_BAD_TOPIC_PATTERNS = re.compile(
    r"^(?:partial|untitled|draft|todo|tbd|readme|license)$",
    re.IGNORECASE,
)

# VaultBot's own tool / API names. Topics that are "how to <tool_name>"
# are procedural gaps about OUR tools, not web-researchable concepts —
# they should be documented from the vault's code, not the web. Without
# this filter the autonomous researcher wasted cycles searching the web
# for "code_run" (got Haskell c_safe_write + Docker Hub pages) and
# "safe_write" (got Rust docs). These are internal identifiers; the web
# has nothing useful on them.
_INTERNAL_TOOL_NAMES = frozenset({
    # Builtin vault tools
    "vault_research", "vault_search", "vault_gaps", "vaultbot_status",
    "plan_task", "update_task", "set_goal",
    # Meta / self-improve tools
    "code_read", "code_run", "code_write", "tool_create", "self_reflect",
    "git_rollback", "safe_write", "js_safe_write", "capability_audit",
    "execute_procedure",
    # Common custom tools
    "textbook_ingest", "textbook_read_page", "web_read_source",
    "vault_append", "vault_delete", "vault_graph_analyzer", "vault_lint",
    "vault_list", "preflight_safety_check", "backend_restart",
    "plugin_reload",
})

# How often to run consolidation instead of gap-filling.
# Every Nth cycle, the researcher runs the semantic consolidation pipeline
# (hippocampal replay) instead of web research. This mines chat logs for
# patterns and writes semantic knowledge notes.
_CONSOLIDATION_INTERVAL = 5


def _is_internal_tool_topic(topic: str) -> bool:
    """True if the topic is a how-to about one of VaultBot's own tools/APIs.

    Matches "how to code_run", "how to safe_write", "code_run", etc. The
    core token (after stripping 'how to ' / 'how to') is checked against
    the internal tool name set, AND any underscored token ≤20 chars is
    treated as an API/tool name (code_run, safe_write, vault_lint pattern).
    """
    t = topic.strip().lower()
    # Strip leading "how to " / "how to" / "what is " prefixes.
    for prefix in ("how to ", "how to", "what is ", "what is"):
        if t.startswith(prefix):
            t = t[len(prefix):].strip()
            break
    # Direct tool-name match (e.g. "code_run", "safe_write").
    if t in _INTERNAL_TOOL_NAMES:
        return True
    # Match the first underscored token in a longer topic
    # ("how to code_run", "how to safe_write properly").
    for token in re.split(r"[\s,]+", t):
        token = token.strip()
        if "_" in token and len(token) <= 20:
            # Underscored short token = internal API/tool name.
            return True
    return False

# Maximum number of words in a researchable topic. Topics with more words
# are almost certainly note titles (e.g. "Giant-pandas-biology-habitat-diet-
# conservation-status-and-ecological-role") not concepts.
_MAX_TOPIC_WORDS = 8

# Minimum number of alphanumeric characters in a topic (catches identifiers
# like "PT2399" while rejecting "to", "up", etc.).
_MIN_TOPIC_ALNUM_CHARS = 3


def _is_researchable_gap(gap: dict[str, Any]) -> bool:
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

        # Apply the curriculum's structural filters so both layers use the
        # same rules: placeholder patterns ([[Related Note]], [[Note-Title]]),
        # code-template vars ([[{n}]]), and single-word stopics (note, target,
        # wikilink, todo, etc.). This avoids a divergent bespoke blocklist.
        if _CURRICULUM_PLACEHOLDER_RE.match(topic):
            return False
        if _CURRICULUM_TEMPLATE_VAR_RE.match(topic):
            return False
        # Single-word stopic check (mirrors the curriculum's logic).
        alpha = re.sub(r"[^a-zA-Z\s]+", " ", topic).strip()
        alpha_words = [w for w in alpha.split() if w]
        if len(alpha_words) == 1 and alpha_words[0].lower() in _CURRICULUM_SINGLE_WORD_STOPICS:
            return False

        # Reject file paths — dead links to learningMaterial/web/*.html or
        # any path containing "/" or ending in a file extension. These are
        # broken file references, not researchable concepts.
        if "/" in topic or topic.endswith((".html", ".md", ".pdf", ".py",
                                           ".js", ".json", ".txt")):
            return False

        # Reject VaultBot's own tool / API names — "how to code_run",
        # "how to safe_write", etc. are procedural gaps about OUR tools,
        # not web-researchable concepts. The web has nothing useful on
        # internal identifiers; these should be documented from code.
        if _is_internal_tool_topic(topic):
            return False

        # thin_community gaps are "MOC for: ..." — not web-researchable.
        # They need a different handler (create a hub note), not web search.
        kind = (gap.get("kind") or "").strip()
        if kind == "thin_community":
            return False

        # link_density gaps are structural ("this note has no in-links") —
        # not web-researchable. They need a bridge note, not web research.
        if kind == "link_density":
            return False

        # Failing/stale procedure gaps reference procedure NOTE NAMES (e.g.
        # "Find-Redundant-Procedures"), not web-researchable concepts. The
        # web has nothing useful on them — the failure log's error_details +
        # the procedure file's own content are the real signal. These need
        # code-level inspection, not a DuckDuckGo dig. Without this filter the
        # researcher gets stuck in an infinite loop: the procedure tracker
        # reports a failing procedure every cycle, the researcher web-searches
        # the procedure name, writes a worthless research note, and the
        # failure count is never reset — so the same gap comes back forever.
        if kind in ("failing_procedure", "failing_step", "stale_procedure"):
            return False

        # Reject topics that are too long (note titles, not concepts).
        words = re.split(r"[\s-]+", topic)
        if len(words) > _MAX_TOPIC_WORDS:
            return False

        # Minimum alphanumeric content (catches "PT2399" but rejects "to").
        alnum = re.sub(r"[^a-zA-Z0-9]+", "", topic)
        if len(alnum) < _MIN_TOPIC_ALNUM_CHARS:
            return False

        return True
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        return False


class AutonomousResearcher:
    """Background loop that researches the vault's own gaps."""

    def __init__(
        self,
        vault_path: str,
        vault_graph: VaultGraph,
        vault_indexer: VaultIndexer,
        note_creator: NoteCreator,
        session_logger: SessionLogger | None = None,
        interval_seconds: int = 600,
        max_researches_per_cycle: int = 2,
        min_dangling_references: int = 1,
        thin_note_threshold: int = 200,
        search_client=None,
        curriculum=None,
        checkpointer=None,
        procedure_tracker=None,
        ollama_client=None,
        on_crash: Callable[[str], None] | None = None,
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
        self.ollama_client = ollama_client  # OllamaClient (LLM-assisted note structuring)
        self.on_crash = on_crash  # called if the background thread crashes

        self.engine = ResearchEngine(
            session_logger=session_logger,
            max_rounds=4,
            max_sources_per_round=5,
            max_follow_ups=3,
            search_client=self.search_client,
        )

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        # Chat-priority pause: when a chat turn is in flight, the researcher
        # yields the Ollama GPU so the interactive user isn't queued behind
        # background research. The chat loop calls pause_for_chat() at the
        # start of handle_chat and resume_after_chat() when the turn ends.
        # This is a threading.Event (not asyncio) so it works across the
        # researcher's own thread + the main event loop without locks.
        self._chat_active = threading.Event()
        self.last_run: dict[str, Any] | None = None
        self.history: list[dict[str, Any]] = []
        self.enabled = True
        # Consolidation cycle counter — every Nth cycle, run consolidation
        # instead of gap-filling (hippocampal replay).
        self._cycle_count = 0

    def _log(self, event: str, data: dict[str, Any] | None = None):
        """Log an event to the session logger."""
        if self.session_logger:
            self.session_logger.log(event, data or {})

    def pause_for_chat(self):
        """Signal the researcher to yield the GPU for a chat turn."""
        self._chat_active.set()

    def resume_after_chat(self):
        """Signal the researcher that the chat turn is done."""
        self._chat_active.clear()

    def _identify_gaps(self) -> list[dict[str, Any]]:
        """Identify knowledge gaps in the vault.

        Uses the knowledge curriculum (Voyager-style self-directed growth).
        Raises if the curriculum is unavailable or fails — no fallback to
        the simple dangling-link scanner. The operator needs to know if
        gap detection is broken, not silently get a different mechanism.
        """
        if self.curriculum is None:
            raise ValueError(
                "_identify_gaps: no knowledge curriculum configured")
        gaps = self.curriculum.propose_next_gaps()
        # Filter out non-researchable gaps.
        gaps = [g for g in gaps if _is_researchable_gap(g)]
        return gaps

    def _find_existing_research_note(self, topic: str) -> Path | None:
        """Check if a research note for this topic already exists on disk.

        Mirrors vault_maintenance._safe_filename so the check uses the exact
        same filename the note creator would produce. Returns the Path if
        the file exists, None otherwise.
        """
        safe_topic = re.sub(r"[^\w\s-]", "", topic).strip()
        safe_topic = re.sub(r"[-\s]+", "-", safe_topic)[:80] or "note"
        research_dir = (
            self.vault_path / "vaultbot_stuff/Knowledge/Research")
        candidate = research_dir / f"{safe_topic}.md"
        if candidate.exists():
            return candidate
        return None

    def _research_to_note(self, gap: dict[str, Any]) -> str | None:
        """Research a single gap and write a note. Returns note path or None."""
        topic = gap["topic"]

        # --- Already-researched guard (deterministic, zero LLM) -------------
        # If a research note for this topic already exists on disk, skip the
        # web research + LLM synthesis entirely. The note was already written
        # — re-researching it just burns Ollama cycles producing a near-clone
        # (the create_research_note similarity check would delete the old one
        # and write the new one at 0.99 similarity, accomplishing nothing).
        # This is the single most impactful guard against wasted small-model
        # cycles: without it, any gap that persists across cycles (e.g. a
        # dangling wikilink that hasn't been re-indexed yet) triggers a full
        # 4-round web search + LLM synthesis every 10 minutes.
        existing = self._find_existing_research_note(topic)
        if existing is not None:
            self._log("autonomous_skip_already_researched", {
                "topic": topic,
                "existing_path": str(existing),
            })
            return str(existing)

        try:
            result = self.engine.research(topic)
            if not result or not result.get("synthesis"):
                return None

            # Use deterministic note structuring (token economy: the note_creator
            # + synthesize_note_markdown already handle frontmatter + sections).
            # The old LLM _structure_note call was a redundant second LLM call per
            # note — the synthesis LLM call already produced the content.
            synthesis = result["synthesis"]

            # Write the note. NoteCreator.create_note_from_research takes
            # (topic, research_content, summary) — synthesis is the content,
            # a short slice of it serves as the summary blurb.
            note_path = self.note_creator.create_note_from_research(
                topic=topic,
                research_content=synthesis,
                summary=synthesis[:500] if synthesis else None,
            )

            # Re-index.
            if self.vault_indexer:
                try:
                    self.vault_indexer.index_note(note_path)
                except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                    self._log("autonomous_index_note_failed",
                        {"path": note_path, "error": str(e)})

            # Add to QA queue so the next idle window checks this note's
            # frontmatter quality. The researcher creates notes; QA heals
            # them. This is the "switch back and forth" coordination: the
            # researcher only runs when QA is done, and QA gets the
            # researcher's output as new work.
            try:
                from qa_worker import load_qa_queue, save_qa_queue
                rel = os.path.relpath(note_path, str(self.vault_path)).replace("\\", "/")
                qa_queue = load_qa_queue()
                # Insert at the FRONT of the queue — freshly researched notes
                # are high-priority for QA (verify they have good frontmatter
                # before they get used in retrieval).
                qa_queue.insert(0, {"path": rel, "touch_count": 0})
                save_qa_queue(qa_queue)
            except Exception:  # noqa: BLE001 — best-effort
                pass

            return note_path
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            self._log("autonomous_research_error", {
                "topic": topic, "error": str(e),
            })
            return None

    def _structure_note(self, synthesis: str, topic: str) -> str:
        """Deterministic note structuring (token economy: no LLM call).

        The old version called the LLM to "structure" the synthesis. But the
        synthesis already has the content, and the note_creator +
        synthesize_note_markdown already produce frontmatter + H2 sections +
        citations. This method is kept for backward compat but now just returns
        the synthesis unchanged — the caller handles formatting.
        """
        return synthesis

    async def _run_consolidation(self) -> dict[str, Any]:
        """Run the semantic consolidation pipeline (hippocampal replay).

        Mines chat logs for patterns, clusters them, synthesizes semantic
        notes using the LLM, validates, and stores them. This is the
        biological equivalent of hippocampal replay during sleep —
        converting episodic experiences into semantic knowledge.

        Returns a summary of what was consolidated.
        """
        try:
            from consolidation_pipeline import ConsolidationPipeline
            pipeline = ConsolidationPipeline(
                vault_path=str(self.vault_path),
                backend_path=str(self.vault_path / "vaultbot_stuff/vaultbot_backend"),
            )

            # Phases 1-4: Extract, Cluster, build Synthesis prompts
            result = pipeline.run()

            clusters = result.get("clusters", [])
            prompts = result.get("synthesis_prompts", [])

            if not prompts:
                self._log("autonomous_consolidation_empty", {
                    "clusters": len(clusters),
                })
                return {"ok": False, "reason": "no clusters to consolidate"}

            consolidated = []
            for sp in prompts[:2]:  # Max 2 per cycle to keep it light
                if self._chat_active.is_set():
                    break  # Yield for chat

                cluster = sp["cluster"]
                prompt = sp["prompt"]

                # Phase 4: Synthesis (template by default, LLM only if enabled)
                import os as _os
                _use_llm = _os.getenv(
                    "VAULTBOT_CONSOLIDATION_MODE", "template").lower() == "llm"

                content = None
                if _use_llm and self.ollama_client:
                    # LLM synthesis path (old behavior).
                    try:
                        response = self.ollama_client.chat(
                            messages=[{"role": "user", "content": prompt}],
                            model="latest",
                        )
                        content = response.get("message", {}).get("content", "")
                        if not content or len(content) < 100:
                            content = None
                    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                        self._log("autonomous_consolidation_error", {
                            "theme": cluster["theme"],
                            "error": str(e),
                        })

                if content is None:
                    # Template synthesis (zero LLM, default path).
                    try:
                        content = pipeline.build_synthesis_template(cluster)
                    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                        self._log("autonomous_consolidation_error", {
                            "theme": cluster["theme"],
                            "error": str(e),
                        })
                        continue

                if not content or len(content) < 100:
                    continue

                # Phases 5-6: Validate + Store
                note_result = pipeline.finalize_note(cluster, content)
                if note_result.get("ok"):
                    consolidated.append({
                        "theme": cluster["theme"],
                        "note_path": note_result["note_path"],
                        "warnings": note_result.get("warnings", []),
                    })
                    self._log("autonomous_consolidation_note", {
                        "theme": cluster["theme"],
                        "note_path": note_result["note_path"],
                    })
                    # Add consolidation notes to QA queue too
                    try:
                        from qa_worker import load_qa_queue, save_qa_queue
                        rel = os.path.relpath(
                            note_result["note_path"],
                            str(self.vault_path),
                        ).replace("\\", "/")
                        qa_queue = load_qa_queue()
                        qa_queue.insert(0, {"path": rel, "touch_count": 0})
                        save_qa_queue(qa_queue)
                    except Exception:  # noqa: BLE001 — best-effort
                        pass

            return {
                "ok": True,
                "clusters_found": len(clusters),
                "notes_written": len(consolidated),
                "consolidated": consolidated,
            }
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            self._log("autonomous_consolidation_failed", {"error": str(e)})
            return {"ok": False, "error": str(e)}

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
        # QA-priority: if the QA worker still has notes to heal, skip this
        # cycle. Existing vault notes get healed BEFORE the researcher
        # creates new ones. The QA worker runs during chat idle windows;
        # the researcher only fires when the queue is drained. This prevents
        # the two background processes from fighting over the GPU — QA
        # heals what's there, then the researcher expands, then QA heals
        # what the researcher made.
        try:
            from qa_worker import load_qa_queue
            qa_queue = load_qa_queue()
            if qa_queue:
                self._log("autonomous_cycle_skipped_qa_pending", {
                    "qa_queue_size": len(qa_queue),
                })
                return
        except Exception:  # noqa: BLE001 — best-effort
            pass
        cycle_t0 = time.time()
        if hasattr(self, '_heartbeat'):
            self._heartbeat("cycle starting")

        # --- Consolidation check ---
        # Every Nth cycle, run the semantic consolidation pipeline
        # (hippocampal replay) instead of gap-filling. This mines chat
        # logs for patterns and writes semantic knowledge notes.
        self._cycle_count += 1
        if self._cycle_count % _CONSOLIDATION_INTERVAL == 0:
            self._log("autonomous_consolidation_cycle", {
                "cycle": self._cycle_count,
            })
            consolidation_result = await self._run_consolidation()
            self.last_run = {
                "timestamp": time.time(),
                "kind": "consolidation",
                "result": consolidation_result,
                "duration_ms": (time.time() - cycle_t0) * 1000,
            }
            self.history.append(self.last_run)
            if len(self.history) > 50:
                self.history = self.history[-50:]
            self._log("autonomous_cycle_end", self.last_run)
            return

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
            # Partition into researchable gaps and rejected procedure gaps.
            # Procedure-name gaps (failing_procedure, failing_step,
            # stale_procedure) are NOT web-researchable — the procedure's
            # name is an internal identifier, not a concept the web knows
            # about. Reset their failure counts so they stop coming back
            # every cycle (the infinite-loop bug). Procedural gaps
            # ("how to <task>") ARE researchable and pass through.
            researchable_proc = []
            rejected_proc = []
            for g in proc_gaps:
                if _is_researchable_gap(g):
                    researchable_proc.append(g)
                else:
                    rejected_proc.append(g)
            # Reset failure counts for rejected procedure gaps so the tracker
            # stops feeding them back every cycle. This is the deterministic
            # fix that replaces the old 7-hour small-model loop.
            for g in rejected_proc:
                proc_name = g.get("procedure") or g.get("topic", "")
                if proc_name:
                    try:
                        self.procedure_tracker.update_after_research(
                            proc_name, vault_path=str(self.vault_path))
                        self._log("autonomous_procedure_gap_reset", {
                            "procedure": proc_name,
                            "kind": g.get("kind"),
                            "reason": "not web-researchable",
                        })
                    except Exception as e:  # noqa: BLE001 — best-effort
                        self._log("autonomous_procedure_reset_failed", {
                            "procedure": proc_name, "error": str(e),
                        })
            if rejected_proc:
                self._log("autonomous_procedure_gaps_rejected", {
                    "count": len(rejected_proc),
                    "topics": [g.get("topic", "") for g in rejected_proc[:5]],
                })
            if researchable_proc:
                gaps = researchable_proc
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
        filled: list[dict[str, Any]] = []
        budget = min(self.max_researches_per_cycle, len(gaps))
        # Checkpoint the cycle's gaps so a crash mid-research can be recovered.
        cycle_checkpoints = []
        from datetime import datetime
        now_iso = lambda: datetime.now(UTC).isoformat()
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
                except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                    # Checkpoint failure means no resume after restart.
                    # Log loudly — the operator needs to know the researcher
                    # can't recover from a crash.
                    self._log("checkpoint_save_failed", {
                        "error": str(e), "category": "compaction_broken",
                    })
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
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
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
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                self._log("checkpoint_clear_failed", {"error": str(e)})

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
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
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
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                self._log("autonomous_thread_crashed", {"error": str(e)})
                # Notify the user via the on_crash callback (wired in main.py
                # to broadcast a type:"problem" WS event). Without this the
                # vault silently stops growing and the user has no idea.
                if self.on_crash is not None:
                    try:
                        self.on_crash(str(e))
                    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                        pass  # the callback must never crash the thread
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
            except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                pass  # best-effort cleanup during shutdown

    def status(self) -> dict[str, Any]:
        """Return a status summary for the GUI / vaultbot_status tool."""
        return {
            "enabled": self.enabled,
            "running": self._thread is not None and self._thread.is_alive(),
            "paused_for_chat": self._chat_active.is_set(),
            "interval_seconds": self.interval_seconds,
            "max_researches_per_cycle": self.max_researches_per_cycle,
            "last_run": self.last_run,
            "history_count": len(self.history),
            "recent_history": self.history[-3:] if self.history else [],
        }
