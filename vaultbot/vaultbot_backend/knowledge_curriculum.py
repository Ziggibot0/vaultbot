"""
KnowledgeCurriculum â€” the Voyager knowledge-curriculum port for VaultBot.

Voyager (NVIDIA, 2023) teaches an LLM agent in Minecraft to *decide what to
learn next* instead of blindly grinding the same skill. The curriculum ranks
candidate tasks by:

    priority = base_priority * diversity_bonus * achievability_bonus * context_bonus

VaultBot's old gap-detection was "pick the dangling link with the most
references." That collapses to a greedy, repetitive loop: the bot researches
the hottest red link, writes a note, refreshes, and the *same* neighborhood is
still hottest â€” so it researches it again. The curriculum breaks that loop by
penalizing gaps too similar to recently-completed topics (diversity),
rewarding gaps that are cheap to close (achievability), and boosting gaps that
wedge into a rich neighborhood (context).

This module ports that idea to the vault graph. It collects five gap signals:

    1. dangling_link   â€” a [[wikilink]] to a note that doesn't exist yet.
    2. thin_note       â€” an existing note whose body is too short to be useful.
    3. missing_entity  â€” a red link re-declared from recent notes (deduped
                          against dangling_link so we don't double-count).
    4. thin_community  â€” a clique of â‰¥3 linked notes with no MOC/Index hub.
    5. link_density    â€” a note with â‰¥5 out-links but zero in-links: a sink
                          that should be linked back into the graph.

Each candidate gap is scored by the multiplicative curriculum priority, the
completed/failed history is filtered out, and the top-N are returned. State
(completed/failed topics, last-run timestamp) persists to
``curriculum_state.json`` so the curriculum survives backend restarts and
doesn't re-propose a topic it just finished.

Pure stdlib + the existing ``vault_graph`` / ``session_logger`` imports. No
new dependencies.
"""

from __future__ import annotations

import json
import os
import re
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# The curriculum imports ONLY from the existing, already-integrated modules.
# ---------------------------------------------------------------------------
import gap_collectors
from vault_graph import VaultGraph

# How many recently-completed topics the diversity bonus looks back at.
_DEFAULT_DIVERSITY_WINDOW: int = 5
# Tokens that carry almost no signal and are dropped before overlap scoring.
_STOP_TOKENS: set[str] = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "for",
    "with",
    "is",
    "are",
    "be",
    "by",
    "at",
    "as",
    "it",
    "this",
    "that",
    "from",
}
# Single-token common English words that are valid dictionary entries but
# are NOT worth a research note — researching them yields dictionary-scraping
# junk. Only rejected when they appear ALONE as the entire topic.
_SINGLE_WORD_STOPICS: set[str] = {
    # prepositions / conjunctions / articles / particles
    "from",
    "to",
    "of",
    "in",
    "on",
    "at",
    "by",
    "for",
    "with",
    "about",
    "into",
    "over",
    "under",
    "after",
    "before",
    "between",
    "through",
    "during",
    "without",
    "within",
    "against",
    "among",
    "around",
    "above",
    "below",
    "up",
    "down",
    "out",
    "off",
    "than",
    "then",
    "when",
    "where",
    "while",
    "and",
    "or",
    "but",
    "nor",
    "yet",
    "so",
    "if",
    "as",
    "like",
    "per",
    "via",
    "versus",
    "vs",
    # pronouns / determiners
    "i",
    "you",
    "he",
    "she",
    "it",
    "we",
    "they",
    "me",
    "him",
    "her",
    "us",
    "them",
    "my",
    "your",
    "his",
    "its",
    "our",
    "their",
    "this",
    "that",
    "these",
    "those",
    "who",
    "whom",
    "which",
    "what",
    "whose",
    # be / auxiliaries
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "am",
    "do",
    "does",
    "did",
    "done",
    "have",
    "has",
    "had",
    "will",
    "would",
    "can",
    "could",
    "should",
    "shall",
    "may",
    "might",
    "must",
    # trivial / UI / placeholder words that yield junk notes when alone
    "welcome",
    "home",
    "index",
    "readme",
    "notes",
    "note",
    "title",
    "summary",
    "references",
    "sources",
    "see",
    "also",
    "todo",
    "draft",
    "untitled",
    "new",
    "old",
    "next",
    "prev",
    "previous",
    "back",
    "top",
    "yes",
    "no",
    "ok",
    "okay",
    "test",
    "example",
    "examples",
    "sample",
    "here",
    "there",
    "now",
    "today",
    "yesterday",
    "tomorrow",
    # generic illustrative wikilinks from exemplar/chat notes — not research concepts
    "wikilink",
    "wikilinks",
    "target",
    "chat-name",
}
# Template / placeholder patterns that show up as dangling links from
# scaffolding or example notes ("Actual-Note-Title", "Note Name", "Topic").
# Also covers multi-word illustrative wikilinks from exemplar notes
# ([[Related Note]], [[Other post]], [[Note C]], [[Some-Note]]) and
# code-template placeholders ([[{n}]]) that the graph treats as dangling.
_PLACEHOLDER_RE = re.compile(
    r"^(?:note[-_ ]?name|actual[-_ ]?note[-_ ]?title|note[-_ ]?title|"
    r"topic|placeholder|untitled|example[-_ ]?note|new[-_ ]?note|"
    r"lorem[-_ ]?ipsum|todo|tbd|foo|bar|baz|qux|test[-_ ]?note|"
    r"related[-_ ]?note|other[-_ ]?post|other[-_ ]?procedure|"
    r"note[-_ ]?[a-z]|some[-_ ]?note)$",
    re.IGNORECASE,
)
# Code-template placeholder patterns like [[{n}]], [[{variable}]], [[%s]].
_TEMPLATE_VAR_RE = re.compile(r"^\{[^}]+\}$|^\%[sd]$")

# Minimum number of alphabetic characters a single-word topic must have to
# be worth researching (filters "to", "up", "ok", ...).
_MIN_SINGLE_WORD_LEN: int = 3


def _is_researchable_topic(gap: dict[str, Any]) -> bool:
    """Quality gate: should the autonomous researcher spend a cycle on this?

    Rejects topics that are almost certainly junk:
      - empty / whitespace-only
      - single common English words (prepositions, pronouns, "welcome", ...)
      - template placeholders ("Note Name", "Actual-Note-Title", "Topic")
      - single tokens shorter than ``_MIN_SINGLE_WORD_LEN`` alpha chars

    Multi-word topics are allowed through even if they contain a stop word,
    because the additional words carry the actual concept. Synthetic
    ``thin_community`` topics ("MOC for: ...") always pass.

    Never raises â€” a False here just suppresses the gap for this cycle.
    """
    try:
        kind = (gap.get("kind") or "").strip()
        # thin_community gaps are "MOC for: ..." synthetic hub proposals.
        # They are NOT web-researchable knowledge concepts â€” they need a
        # different handler (create a hub note linking the clique members).
        if kind == "thin_community":
            return False
        # link_density gaps are structural ("this note has no in-links") â€”
        # not a knowledge concept to research on the web.
        if kind == "link_density":
            return False
        topic = (gap.get("topic") or "").strip()
        if not topic:
            return False
        # Reject chat-log titles â€” they are conversation logs, not concepts.
        if topic.lower().startswith("chat-"):
            return False
        if _PLACEHOLDER_RE.match(topic):
            return False
        # Reject code-template placeholders ([[{n}]], [[%s]]) that the graph
        # treats as dangling links. These are code artifacts, not concepts.
        if _TEMPLATE_VAR_RE.match(topic):
            return False
        # Reject file paths â€” dead links to learningMaterial/web/*.html or
        # any path with "/" or a file extension. These are broken file
        # references, not researchable concepts.
        if "/" in topic or topic.endswith(
            (".html", ".md", ".pdf", ".py", ".js", ".json", ".txt")
        ):
            return False
        # Strip non-alpha for length/word checks but keep the original for
        # the placeholder regex above.
        alpha = re.sub(r"[^a-zA-Z\s]+", " ", topic).strip()
        if not alpha:
            return False
        words = [w for w in alpha.split() if w]
        if not words:
            return False
        if len(words) == 1:
            w = words[0].lower()
            if len(w) < _MIN_SINGLE_WORD_LEN:
                return False
            if w in _SINGLE_WORD_STOPICS:
                return False
        return True
    except Exception:  # noqa: BLE001 â€” best-effort, returns error/empty to caller â€” see CONTRIBUTING.md no-silent-fallbacks
        return False


def _now_iso() -> str:
    """UTC timestamp in ISO-8601, for the state file."""
    return datetime.now(UTC).isoformat()


def _tokenize(text: str) -> set[str]:
    """Split on non-alphanumeric, lowercase, drop stop words + single chars.

    No external deps â€” this is the embedding-free token set used for the
    diversity bonus and community-name overlap. Kept deliberately coarse.
    """
    if not text:
        return set()
    raw = re.split(r"[^a-z0-9]+", text.lower())
    out: set[str] = set()
    for tok in raw:
        if len(tok) <= 1:
            continue
        if tok in _STOP_TOKENS:
            continue
        out.add(tok)
    return out


def _token_overlap(s1: str, s2: str) -> float:
    """Embedding-free similarity in [0,1] between two strings.

    Uses the Jaccard index on the two token sets â€” cheap, deterministic, and
    dependency-free. 0 = no shared tokens, 1 = identical token sets. Empty
    inputs return 0.
    """
    t1 = _tokenize(s1)
    t2 = _tokenize(s2)
    if not t1 or not t2:
        return 0.0
    inter = len(t1 & t2)
    if inter == 0:
        return 0.0
    union = len(t1 | t2)
    return inter / union if union else 0.0


class KnowledgeCurriculum:
    """Voyager-style automatic curriculum over the vault graph.

    The vault decides what to learn next based on diversity + state +
    completed/failed attempts + context, instead of just ranking gaps by
    reference count.

    Lifecycle:
        curriculum = KnowledgeCurriculum(vault_graph)
        gaps = curriculum.propose_next_gaps(n=5)   # refresh + score + filter
        ...
        curriculum.mark_completed(gaps[0]["topic"])
        curriculum.mark_failed(gaps[1]["topic"], "no sources found")
    """

    STATE_FILENAME: str = "curriculum_state.json"

    def __init__(
        self,
        vault_graph: VaultGraph,
        session_logger: Any | None = None,
        state_path: str | None = None,
        min_content_length: int = 200,
        thin_community_min_size: int = 3,
        link_density_min_outlinks: int = 5,
        diversity_window: int = _DEFAULT_DIVERSITY_WINDOW,
        skip_vaultbot_paths: bool = True,
    ) -> None:
        """Configure the curriculum.

        Args:
            vault_graph: the live ``VaultGraph`` instance (will be refreshed
                on every ``propose_next_gaps`` call).
            session_logger: optional ``SessionLogger`` for tool-call tracing.
            state_path: override for the persistent state JSON file. Defaults
                to ``vaultbot_backend/curriculum_state.json``.
            min_content_length: passthrough to ``thin_notes()``.
            thin_community_min_size: minimum clique size for a thin-community
                gap (default 3).
            link_density_min_outlinks: out-link threshold for the
                link-density sink signal (default 5).
            diversity_window: how many recently-completed topics the diversity
                bonus penalizes against (default 5).
            skip_vaultbot_paths: drop thin notes under Memory/Chat/ or Knowledge/Research/ (the bot's
                own outputs) so the curriculum doesn't chase its own drafts.
        """
        self.graph: VaultGraph = vault_graph
        self.session_logger = session_logger

        if state_path is None:
            state_path = str(Path(__file__).resolve().parent / self.STATE_FILENAME)
        self.state_path: str = state_path

        self.min_content_length: int = int(min_content_length)
        self.thin_community_min_size: int = int(thin_community_min_size)
        self.link_density_min_outlinks: int = int(link_density_min_outlinks)
        self.diversity_window: int = int(diversity_window)
        self.skip_vaultbot_paths: bool = bool(skip_vaultbot_paths)

        # Persistent curriculum state, loaded from disk (or seeded empty).
        self.state: dict[str, Any] = self._load_state()

        # ---- Gaps TTL cache -------------------------------------------------
        # propose_next_gaps() is query-INDEPENDENT and only feeds a summary
        # string into the chat system prompt. Scoring the whole vault every
        # message is pure TTFT overhead. Cache the scored gaps for a short
        # TTL so a warm chat session reuses one computation across turns.
        # Invalidated by mark_completed / mark_failed (state changes) and by
        # the env-configurable TTL below. The autonomous researcher, which
        # runs on its own schedule, also benefits: rapid re-queries are free.
        self._GAPS_TTL: float = float(os.getenv("VAULTBOT_GAPS_TTL", "60"))
        self._gaps_cache: list[dict[str, Any]] | None = None
        self._gaps_cache_ts: float = 0.0
        # ---- Thin-communities sub-cache ---------------------------------
        # ``_collect_thin_communities`` is the most expensive gap signal
        # (O(n * k^2) clique detection over every note). Its result depends
        # ONLY on the graph topology, which is mtime-tracked on
        # ``VaultGraph._last_refresh_mtime``. So we cache it keyed on that
        # mtime: a back-to-back scoring pass that finds the graph unchanged
        # reuses the previous clique list for free. The main gaps cache is
        # invalidated on mark_completed/mark_failed; this sub-cache is
        # invalidated only when the graph actually changes (a note added /
        # edited / linked), which is the correct condition for a
        # topology-only signal.
        self._thin_communities_cache: list[dict[str, Any]] | None = None
        self._thin_communities_graph_mtime: float = -1.0

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load_state(self) -> dict[str, Any]:
        """Load curriculum state from disk, returning a sane default shape."""
        default: dict[str, Any] = {
            "completed_topics": [],
            "failed_topics": [],
            "last_run": None,
        }
        try:
            p = Path(self.state_path)
            if not p.exists():
                return default
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return default
            data.setdefault("completed_topics", [])
            data.setdefault("failed_topics", [])
            data.setdefault("last_run", None)
            if not isinstance(data["completed_topics"], list):
                data["completed_topics"] = []
            if not isinstance(data["failed_topics"], list):
                data["failed_topics"] = []
            return data
        except Exception as e:  # corrupt/missing state must never be fatal
            self._log_error("load_state", e)
            return default

    def _persist_state(self) -> None:
        """Write curriculum state to disk. Failures are logged, not raised."""
        try:
            p = Path(self.state_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(p.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2, default=str)
            os.replace(tmp, p)
        except Exception as e:  # noqa: BLE001 â€” best-effort, returns error/empty to caller â€” see CONTRIBUTING.md no-silent-fallbacks
            self._log_error("persist_state", e)

    def _log_error(self, context: str, exc: BaseException) -> None:
        """Best-effort error log through the session logger."""
        try:
            if self.session_logger is not None:
                self.session_logger.log(
                    "curriculum_error",
                    {
                        "context": context,
                        "error": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc(),
                    },
                )
        except Exception:  # noqa: BLE001 â€” best-effort, returns error/empty to caller â€” see CONTRIBUTING.md no-silent-fallbacks
            pass

    def _log_event(self, event: str, data: dict[str, Any] | None = None) -> None:
        try:
            if self.session_logger is not None:
                self.session_logger.log(event, data)
        except Exception:  # noqa: BLE001 â€” best-effort, returns error/empty to caller â€” see CONTRIBUTING.md no-silent-fallbacks
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def propose_next_gaps(self, n: int = 5) -> list[dict[str, Any]]:
        """Refresh the graph, score all gap signals, return the top-N.

        This is the curriculum's main entry point. It:
          1. Refreshes the vault graph from disk (now mtime-gated, so the
             common no-edit case is a cheap stat-only scan).
          2. Collects all five gap-signal types.
          3. Filters out completed topics and recently-failed topics.
          4. Scores survivors by the multiplicative curriculum priority.
          5. Returns the top-N as gap dicts.

        Results are cached for ``VAULTBOT_GAPS_TTL`` seconds (default 60) so
        consecutive chat messages in a warm session don't re-score the whole
        vault â€” gaps only change when notes are created/edited or when the
        curriculum state (completed/failed) changes. Raises on failure so
        the caller knows gap detection is broken.
        """
        # Fast path: serve cached gaps if still fresh. Slicing respects the
        # caller's `n` without recomputing.
        if (
            self._gaps_cache is not None
            and (time.time() - self._gaps_cache_ts) < self._GAPS_TTL
        ):
            if self.session_logger is not None:
                try:
                    self.session_logger.log(
                        "gaps_cache_hit",
                        {
                            "age_s": round(time.time() - self._gaps_cache_ts, 1),
                            "returned": min(len(self._gaps_cache), max(0, int(n))),
                        },
                    )
                except Exception:  # noqa: BLE001 â€” best-effort, returns error/empty to caller â€” see CONTRIBUTING.md no-silent-fallbacks
                    pass
            return self._gaps_cache[: max(0, int(n))] if self._gaps_cache else []

        try:
            self.graph.refresh()
        except Exception as e:
            self._log_error("graph_refresh", e)
            raise

        self.state["last_run"] = _now_iso()
        # Persist last_run even if scoring below fails â€” it's cheap and useful.
        self._persist_state()

        try:
            candidates = self._collect_all_gaps()
            filtered = self._filter_candidates(candidates)
            scored = [self._score_gap(g) for g in filtered]
            scored.sort(
                key=lambda g: (g.get("priority", 0.0), g.get("base_priority", 0.0)),
                reverse=True,
            )
            # Deduplicate by normalized_name so the same concept (surfaced as
            # both a dangling_link AND a missing_entity, etc.) appears only
            # once in the returned top-N. Keep the highest-scoring instance.
            seen_names: set[str] = set()
            top: list[dict[str, Any]] = []
            for g in scored:
                norm = g.get("normalized_name", "").strip().lower()
                if norm and norm in seen_names:
                    continue
                if norm:
                    seen_names.add(norm)
                top.append(g)

            # Cache the full scored list (not just top-N) so callers asking
            # for different N values reuse one computation.
            self._gaps_cache = top
            self._gaps_cache_ts = time.time()

            self._log_event(
                "curriculum_proposed",
                {
                    "candidate_count": len(candidates),
                    "filtered_count": len(filtered),
                    "returned": len(top[: max(0, int(n))]),
                    "top_topics": [g.get("topic") for g in top[: max(0, int(n))]],
                },
            )
            return top[: max(0, int(n))]
        except Exception as e:
            self._log_error("propose_next_gaps", e)
            raise

    def mark_completed(self, topic: str) -> None:
        """Record a topic as successfully researched. Persists immediately."""
        try:
            if not topic:
                return
            topic = topic.strip()
            completed: list[str] = list(self.state.get("completed_topics", []))
            if topic not in completed:
                completed.append(topic)
            # Keep the list bounded so the diversity window stays meaningful
            # and the state file doesn't grow unbounded.
            if len(completed) > 200:
                completed = completed[-200:]
            self.state["completed_topics"] = completed

            # Clear any matching failure record â€” it's no longer failing.
            failed: list[dict[str, Any]] = []
            for fr in self.state.get("failed_topics", []):
                if fr.get("topic") == topic:
                    continue
                failed.append(fr)
            self.state["failed_topics"] = failed

            self._persist_state()
            # A completion changes what gaps should surface next; drop the cache.
            self._gaps_cache = None
            self._log_event("curriculum_completed", {"topic": topic})
        except Exception as e:  # noqa: BLE001 â€” best-effort, returns error/empty to caller â€” see CONTRIBUTING.md no-silent-fallbacks
            self._log_error("mark_completed", e)

    def mark_failed(self, topic: str, reason: str = "") -> None:
        """Record a failed attempt at a topic. Persists immediately.

        Repeated failures (â‰¥3 attempts) collapse a gap's achievability to
        Ã—0.1 so the curriculum stops hammering a dead-end.
        """
        try:
            if not topic:
                return
            topic = topic.strip()
            reason = (reason or "").strip() or "unknown"
            failed: list[dict[str, Any]] = list(self.state.get("failed_topics", []))

            found = False
            for fr in failed:
                if fr.get("topic") == topic:
                    fr["attempts"] = int(fr.get("attempts", 0)) + 1
                    fr["reason"] = reason
                    fr["last_failed"] = _now_iso()
                    found = True
                    break
            if not found:
                failed.append(
                    {
                        "topic": topic,
                        "reason": reason,
                        "attempts": 1,
                        "last_failed": _now_iso(),
                    }
                )

            # Keep the failure log bounded.
            if len(failed) > 200:
                failed = failed[-200:]
            self.state["failed_topics"] = failed
            self._persist_state()
            # A failure changes achievability scoring; drop the gaps cache so
            # the next propose_next_gaps reflects the updated failure history.
            self._gaps_cache = None
            self._log_event(
                "curriculum_failed",
                {
                    "topic": topic,
                    "reason": reason,
                    "attempts": next(
                        (
                            fr.get("attempts")
                            for fr in failed
                            if fr.get("topic") == topic
                        ),
                        1,
                    ),
                },
            )
        except Exception as e:  # noqa: BLE001 â€” best-effort, returns error/empty to caller â€” see CONTRIBUTING.md no-silent-fallbacks
            self._log_error("mark_failed", e)

    def state_summary(self) -> dict[str, Any]:
        """Return a compact, JSON-safe summary for the status endpoint.

        Never raises.
        """
        try:
            completed = self.state.get("completed_topics", []) or []
            failed = self.state.get("failed_topics", []) or []
            return {
                "completed_count": len(completed),
                "failed_count": len(failed),
                "recently_completed": list(completed[-self.diversity_window :]),
                "failed_topics": [
                    {"topic": f.get("topic"), "attempts": f.get("attempts", 0)}
                    for f in failed
                ],
                "last_run": self.state.get("last_run"),
                "state_path": self.state_path,
            }
        except Exception as e:  # noqa: BLE001 â€” best-effort, returns error/empty to caller â€” see CONTRIBUTING.md no-silent-fallbacks
            self._log_error("state_summary", e)
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Gap-signal collection (delegates to gap_collectors.py)
    # ------------------------------------------------------------------
    def _collect_all_gaps(self) -> list[dict[str, Any]]:
        """Gather all five gap-signal types into a flat candidate list.

        Computes dangling_links() once and shares it with the missing-entity
        collector to avoid doubling the scan cost.
        """
        gaps: list[dict[str, Any]] = []
        try:
            dangling = self.graph.dangling_links(min_references=1)
        except Exception as e:  # noqa: BLE001 â€” best-effort, returns error/empty to caller â€” see CONTRIBUTING.md no-silent-fallbacks
            self._log_error("collect_dangling_links", e)
            dangling = []

        gaps.extend(self._collect_dangling_links(dangling=dangling))
        gaps.extend(self._collect_thin_notes())
        gaps.extend(self._collect_missing_entities(dangling=dangling))
        gaps.extend(self._collect_thin_communities())
        gaps.extend(self._collect_link_density_anomalies())
        return gaps

    def _collect_dangling_links(
        self, dangling: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        return gap_collectors.collect_dangling_links(
            self.graph, dangling, self.session_logger
        )

    def _collect_thin_notes(self) -> list[dict[str, Any]]:
        return gap_collectors.collect_thin_notes(
            self.graph,
            self.min_content_length,
            self.skip_vaultbot_paths,
            self.session_logger,
        )

    def _collect_missing_entities(
        self, dangling: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        return gap_collectors.collect_missing_entities(
            self.graph, dangling, self.session_logger
        )

    def _collect_thin_communities(self) -> list[dict[str, Any]]:
        out, mtime = gap_collectors.collect_thin_communities(
            self.graph,
            self.thin_community_min_size,
            self._thin_communities_cache,
            self._thin_communities_graph_mtime,
            self.session_logger,
        )
        self._thin_communities_cache = list(out)
        self._thin_communities_graph_mtime = mtime
        return out

    def _collect_link_density_anomalies(self) -> list[dict[str, Any]]:
        return gap_collectors.collect_link_density_anomalies(
            self.graph, self.link_density_min_outlinks, self.session_logger
        )

    # ------------------------------------------------------------------
    # Filtering + scoring (delegates to gap_collectors.py)
    # ------------------------------------------------------------------
    def _filter_candidates(
        self, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return gap_collectors.filter_candidates(
            candidates,
            self.state.get("completed_topics") or [],
            self.state.get("failed_topics") or [],
            _is_researchable_topic,
        )

    def _score_gap(self, gap: dict[str, Any]) -> dict[str, Any]:
        return gap_collectors.score_gap(
            gap,
            self.graph,
            self.state.get("completed_topics") or [],
            self.state.get("failed_topics") or [],
            self.diversity_window,
        )

    def _diversity_bonus(self, gap: dict[str, Any]) -> float:
        return gap_collectors.diversity_bonus(
            gap,
            self.graph,
            self.state.get("completed_topics") or [],
            self.diversity_window,
        )

    def _achievability_bonus(self, gap: dict[str, Any]) -> float:
        return gap_collectors.achievability_bonus(
            gap, self.state.get("failed_topics") or []
        )

    def _context_bonus(self, gap: dict[str, Any]) -> float:
        return gap_collectors.context_bonus(gap, self.graph)

    def _explain(self, gap: dict[str, Any], breakdown: dict[str, float]) -> str:
        return gap_collectors.explain(gap, breakdown)


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------
def _is_hub_name(name: str) -> bool:
    """True if a note name looks like a MOC/Index hub note."""
    if not name:
        return False
    n = name.lower()
    return "moc" in n or "index" in n
