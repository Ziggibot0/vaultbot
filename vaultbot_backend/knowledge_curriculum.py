"""
KnowledgeCurriculum — the Voyager knowledge-curriculum port for VaultBot.

Voyager (NVIDIA, 2023) teaches an LLM agent in Minecraft to *decide what to
learn next* instead of blindly grinding the same skill. The curriculum ranks
candidate tasks by:

    priority = base_priority * diversity_bonus * achievability_bonus * context_bonus

VaultBot's old gap-detection was "pick the dangling link with the most
references." That collapses to a greedy, repetitive loop: the bot researches
the hottest red link, writes a note, refreshes, and the *same* neighborhood is
still hottest — so it researches it again. The curriculum breaks that loop by
penalizing gaps too similar to recently-completed topics (diversity),
rewarding gaps that are cheap to close (achievability), and boosting gaps that
wedge into a rich neighborhood (context).

This module ports that idea to the vault graph. It collects five gap signals:

    1. dangling_link   — a [[wikilink]] to a note that doesn't exist yet.
    2. thin_note       — an existing note whose body is too short to be useful.
    3. missing_entity  — a red link re-declared from recent notes (deduped
                          against dangling_link so we don't double-count).
    4. thin_community  — a clique of ≥3 linked notes with no MOC/Index hub.
    5. link_density    — a note with ≥5 out-links but zero in-links: a sink
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# The curriculum imports ONLY from the existing, already-integrated modules.
# It must never edit them.
# ---------------------------------------------------------------------------
from vault_graph import VaultGraph, WIKILINK_RE


# How many recently-completed topics the diversity bonus looks back at.
_DEFAULT_DIVERSITY_WINDOW: int = 5
# Tokens that carry almost no signal and are dropped before overlap scoring.
_STOP_TOKENS: Set[str] = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "be", "by", "at", "as", "it", "this", "that", "from",
}
# Single-token common English words that are valid dictionary entries but
# are NOT worth a research note — researching them yields dictionary-scraping
# junk ("from", "to", "welcome"). These are only rejected when they appear
# ALONE as the entire topic; multi-word topics containing them pass.
_SINGLE_WORD_STOPICS: Set[str] = {
    # prepositions / conjunctions / articles / particles
    "from", "to", "of", "in", "on", "at", "by", "for", "with", "about",
    "into", "over", "under", "after", "before", "between", "through",
    "during", "without", "within", "against", "among", "around", "above",
    "below", "up", "down", "out", "off", "than", "then", "when", "where",
    "while", "and", "or", "but", "nor", "yet", "so", "if", "as", "like",
    "per", "via", "versus", "vs",
    # pronouns / determiners
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us",
    "them", "my", "your", "his", "its", "our", "their", "this", "that",
    "these", "those", "who", "whom", "which", "what", "whose",
    # be / auxiliaries
    "is", "are", "was", "were", "be", "been", "being", "am", "do", "does",
    "did", "done", "have", "has", "had", "will", "would", "can", "could",
    "should", "shall", "may", "might", "must",
    # trivial / UI / placeholder words that yield junk notes when alone
    "welcome", "home", "index", "readme", "notes", "note", "title",
    "summary", "references", "sources", "see", "also", "todo", "draft",
    "untitled", "new", "old", "next", "prev", "previous", "back", "top",
    "yes", "no", "ok", "okay", "test", "example", "examples", "sample",
    "here", "there", "now", "today", "yesterday", "tomorrow",
}
# Template / placeholder patterns that show up as dangling links from
# scaffolding or example notes ("Actual-Note-Title", "Note Name", "Topic").
_PLACEHOLDER_RE = re.compile(
    r"^(?:note[-_ ]?name|actual[-_ ]?note[-_ ]?title|note[-_ ]?title|"
    r"topic|placeholder|untitled|example[-_ ]?note|new[-_ ]?note|"
    r"lorem[-_ ]?ipsum|todo|tbd|foo|bar|baz|qux|test[-_ ]?note)$",
    re.IGNORECASE,
)
# Minimum number of alphabetic characters a single-word topic must have to
# be worth researching (filters "to", "up", "ok", ...).
_MIN_SINGLE_WORD_LEN: int = 3


def _is_researchable_topic(gap: Dict[str, Any]) -> bool:
    """Quality gate: should the autonomous researcher spend a cycle on this?

    Rejects topics that are almost certainly junk:
      - empty / whitespace-only
      - single common English words (prepositions, pronouns, "welcome", ...)
      - template placeholders ("Note Name", "Actual-Note-Title", "Topic")
      - single tokens shorter than ``_MIN_SINGLE_WORD_LEN`` alpha chars

    Multi-word topics are allowed through even if they contain a stop word,
    because the additional words carry the actual concept. Synthetic
    ``thin_community`` topics ("MOC for: ...") always pass.

    Never raises — a False here just suppresses the gap for this cycle.
    """
    try:
        kind = (gap.get("kind") or "").strip()
        # thin_community gaps are "MOC for: ..." synthetic hub proposals.
        # They are NOT web-researchable knowledge concepts — they need a
        # different handler (create a hub note linking the clique members).
        if kind == "thin_community":
            return False
        # link_density gaps are structural ("this note has no in-links") —
        # not a knowledge concept to research on the web.
        if kind == "link_density":
            return False
        topic = (gap.get("topic") or "").strip()
        if not topic:
            return False
        # Reject chat-log titles — they are conversation logs, not concepts.
        if topic.lower().startswith("chat-"):
            return False
        if _PLACEHOLDER_RE.match(topic):
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
    except Exception:
        return False


def _now_iso() -> str:
    """UTC timestamp in ISO-8601, for the state file."""
    return datetime.now(timezone.utc).isoformat()


def _tokenize(text: str) -> Set[str]:
    """Split on non-alphanumeric, lowercase, drop stop words + single chars.

    No external deps — this is the embedding-free token set used for the
    diversity bonus and community-name overlap. Kept deliberately coarse.
    """
    if not text:
        return set()
    raw = re.split(r"[^a-z0-9]+", text.lower())
    out: Set[str] = set()
    for tok in raw:
        if len(tok) <= 1:
            continue
        if tok in _STOP_TOKENS:
            continue
        out.add(tok)
    return out


def _token_overlap(s1: str, s2: str) -> float:
    """Embedding-free similarity in [0,1] between two strings.

    Uses the Jaccard index on the two token sets — cheap, deterministic, and
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
        session_logger: Optional[Any] = None,
        state_path: Optional[str] = None,
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
            skip_vaultbot_paths: drop thin notes under ``vaultbot/`` (the bot's
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
        self.state: Dict[str, Any] = self._load_state()

        # ---- Gaps TTL cache -------------------------------------------------
        # propose_next_gaps() is query-INDEPENDENT and only feeds a summary
        # string into the chat system prompt. Scoring the whole vault every
        # message is pure TTFT overhead. Cache the scored gaps for a short
        # TTL so a warm chat session reuses one computation across turns.
        # Invalidated by mark_completed / mark_failed (state changes) and by
        # the env-configurable TTL below. The autonomous researcher, which
        # runs on its own schedule, also benefits: rapid re-queries are free.
        self._GAPS_TTL: float = float(
            os.getenv("VAULTBOT_GAPS_TTL", "60")
        )
        self._gaps_cache: Optional[List[Dict[str, Any]]] = None
        self._gaps_cache_ts: float = 0.0

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load_state(self) -> Dict[str, Any]:
        """Load curriculum state from disk, returning a sane default shape."""
        default: Dict[str, Any] = {
            "completed_topics": [],
            "failed_topics": [],
            "last_run": None,
        }
        try:
            p = Path(self.state_path)
            if not p.exists():
                return default
            with open(p, "r", encoding="utf-8") as f:
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
        except Exception as e:
            self._log_error("persist_state", e)

    def _log_error(self, context: str, exc: BaseException) -> None:
        """Best-effort error log through the session logger."""
        try:
            if self.session_logger is not None:
                self.session_logger.log("curriculum_error", {
                    "context": context,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                })
        except Exception:
            pass

    def _log_event(self, event: str, data: Optional[Dict[str, Any]] = None) -> None:
        try:
            if self.session_logger is not None:
                self.session_logger.log(event, data)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def propose_next_gaps(self, n: int = 5) -> List[Dict[str, Any]]:
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
        vault — gaps only change when notes are created/edited or when the
        curriculum state (completed/failed) changes. Never raises — on any
        failure it returns an empty list so the calling autonomous loop can
        keep running.
        """
        # Fast path: serve cached gaps if still fresh. Slicing respects the
        # caller's `n` without recomputing.
        if (self._gaps_cache is not None
                and (time.time() - self._gaps_cache_ts) < self._GAPS_TTL):
            if self.session_logger is not None:
                try:
                    self.session_logger.log("gaps_cache_hit", {
                        "age_s": round(time.time() - self._gaps_cache_ts, 1),
                        "returned": min(len(self._gaps_cache), max(0, int(n))),
                    })
                except Exception:
                    pass
            return self._gaps_cache[:max(0, int(n))] if self._gaps_cache else []

        try:
            self.graph.refresh()
        except Exception as e:
            self._log_error("graph_refresh", e)
            return []

        self.state["last_run"] = _now_iso()
        # Persist last_run even if scoring below fails — it's cheap and useful.
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
            seen_names: Set[str] = set()
            top: List[Dict[str, Any]] = []
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

            self._log_event("curriculum_proposed", {
                "candidate_count": len(candidates),
                "filtered_count": len(filtered),
                "returned": len(top[:max(0, int(n))]),
                "top_topics": [g.get("topic") for g in top[:max(0, int(n))]],
            })
            return top[:max(0, int(n))]
        except Exception as e:
            self._log_error("propose_next_gaps", e)
            return []

    def mark_completed(self, topic: str) -> None:
        """Record a topic as successfully researched. Persists immediately."""
        try:
            if not topic:
                return
            topic = topic.strip()
            completed: List[str] = list(self.state.get("completed_topics", []))
            if topic not in completed:
                completed.append(topic)
            # Keep the list bounded so the diversity window stays meaningful
            # and the state file doesn't grow unbounded.
            if len(completed) > 200:
                completed = completed[-200:]
            self.state["completed_topics"] = completed

            # Clear any matching failure record — it's no longer failing.
            failed: List[Dict[str, Any]] = []
            for fr in self.state.get("failed_topics", []):
                if fr.get("topic") == topic:
                    continue
                failed.append(fr)
            self.state["failed_topics"] = failed

            self._persist_state()
            # A completion changes what gaps should surface next; drop the cache.
            self._gaps_cache = None
            self._log_event("curriculum_completed", {"topic": topic})
        except Exception as e:
            self._log_error("mark_completed", e)

    def mark_failed(self, topic: str, reason: str = "") -> None:
        """Record a failed attempt at a topic. Persists immediately.

        Repeated failures (≥3 attempts) collapse a gap's achievability to
        ×0.1 so the curriculum stops hammering a dead-end.
        """
        try:
            if not topic:
                return
            topic = topic.strip()
            reason = (reason or "").strip() or "unknown"
            failed: List[Dict[str, Any]] = list(self.state.get("failed_topics", []))

            found = False
            for fr in failed:
                if fr.get("topic") == topic:
                    fr["attempts"] = int(fr.get("attempts", 0)) + 1
                    fr["reason"] = reason
                    fr["last_failed"] = _now_iso()
                    found = True
                    break
            if not found:
                failed.append({
                    "topic": topic,
                    "reason": reason,
                    "attempts": 1,
                    "last_failed": _now_iso(),
                })

            # Keep the failure log bounded.
            if len(failed) > 200:
                failed = failed[-200:]
            self.state["failed_topics"] = failed
            self._persist_state()
            # A failure changes achievability scoring; drop the gaps cache so
            # the next propose_next_gaps reflects the updated failure history.
            self._gaps_cache = None
            self._log_event("curriculum_failed", {
                "topic": topic,
                "reason": reason,
                "attempts": next(
                    (fr.get("attempts") for fr in failed if fr.get("topic") == topic),
                    1,
                ),
            })
        except Exception as e:
            self._log_error("mark_failed", e)

    def state_summary(self) -> Dict[str, Any]:
        """Return a compact, JSON-safe summary for the status endpoint.

        Never raises.
        """
        try:
            completed = self.state.get("completed_topics", []) or []
            failed = self.state.get("failed_topics", []) or []
            return {
                "completed_count": len(completed),
                "failed_count": len(failed),
                "recently_completed": list(completed[-self.diversity_window:]),
                "failed_topics": [
                    {"topic": f.get("topic"), "attempts": f.get("attempts", 0)}
                    for f in failed
                ],
                "last_run": self.state.get("last_run"),
                "state_path": self.state_path,
            }
        except Exception as e:
            self._log_error("state_summary", e)
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Gap-signal collection
    # ------------------------------------------------------------------
    def _collect_all_gaps(self) -> List[Dict[str, Any]]:
        """Gather all five gap-signal types into a flat candidate list."""
        gaps: List[Dict[str, Any]] = []

        gaps.extend(self._collect_dangling_links())
        gaps.extend(self._collect_thin_notes())
        # missing_entity is deduped against dangling_link inside the collector.
        gaps.extend(self._collect_missing_entities())
        gaps.extend(self._collect_thin_communities())
        gaps.extend(self._collect_link_density_anomalies())

        return gaps

    def _collect_dangling_links(self) -> List[Dict[str, Any]]:
        """Signal 1: red links the vault has declared it wants to know."""
        try:
            dangling = self.graph.dangling_links(min_references=1)
            out: List[Dict[str, Any]] = []
            for d in dangling:
                out.append({
                    "kind": "dangling_link",
                    "topic": d.get("name", d.get("normalized_name", "")),
                    "normalized_name": d.get("normalized_name", ""),
                    "reference_count": int(d.get("reference_count", 1)),
                    "referenced_by": list(d.get("referenced_by", []) or []),
                    "file_path": None,
                    "base_priority": int(d.get("reference_count", 1)) * 10,
                })
            return out
        except Exception as e:
            self._log_error("collect_dangling_links", e)
            return []

    def _collect_thin_notes(self) -> List[Dict[str, Any]]:
        """Signal 2: existing notes with too-short bodies.

        Skips anything under ``vaultbot/`` (the bot's own drafts) so the
        curriculum doesn't chase its own work-in-progress.
        """
        try:
            thin = self.graph.thin_notes(min_content_length=self.min_content_length)
            out: List[Dict[str, Any]] = []
            for t in thin:
                file_path = t.get("file_path", "") or ""
                if self.skip_vaultbot_paths and "vaultbot" in file_path.replace("\\", "/").lower():
                    continue
                out.append({
                    "kind": "thin_note",
                    "topic": t.get("name", t.get("normalized_name", "")),
                    "normalized_name": t.get("normalized_name", ""),
                    "reference_count": 0,
                    "referenced_by": self.graph.neighbors(
                        t.get("normalized_name", ""), direction="in"
                    ),
                    "file_path": file_path,
                    "content_length": int(t.get("content_length", 0)),
                    "base_priority": 1,
                })
            return out
        except Exception as e:
            self._log_error("collect_thin_notes", e)
            return []

    def _collect_missing_entities(self) -> List[Dict[str, Any]]:
        """Signal 3: red links re-declared from recent notes, deduped.

        The set of dangling-link normalized names is the authoritative "what's
        missing" set; ``missing_entity`` is a thin wrapper that surfaces the
        same holes from the angle of *which notes keep asking for them*. To
        avoid double-counting we dedupe against the dangling-link candidate
        set by normalized name and only emit entries that contribute extra
        context (e.g. a referenced_by source the bare dangling scan missed).
        """
        try:
            dangling = self.graph.dangling_links(min_references=1)
            dangling_names: Set[str] = {
                d.get("normalized_name", "") for d in dangling if d.get("normalized_name")
            }
            # Re-scan every note's raw content for wikilinks to non-existent
            # notes — same logic as dangling_links but we keep only entries
            # whose reference count from *recent* notes (by mtime) differs.
            ref_counts: Dict[str, int] = {}
            ref_sources: Dict[str, Set[str]] = {}
            for name, node in self.graph.nodes.items():
                raw_links = WIKILINK_RE.findall(node.get("content", "") or "")
                for link in raw_links:
                    norm = self.graph._normalize_name(link)  # noqa: SLF001
                    if norm in self.graph.nodes:
                        continue  # resolved — not missing
                    if norm not in dangling_names:
                        continue  # dangling_links already covers this
                    ref_counts[norm] = ref_counts.get(norm, 0) + 1
                    ref_sources.setdefault(norm, set()).add(name)

            out: List[Dict[str, Any]] = []
            # missing_entity only adds value when it surfaces a topic with
            # multiple recent re-declarations; otherwise it's pure dup of the
            # dangling_link signal. Emit only the ones referenced from ≥2
            # distinct notes (dangling_links already covers the 1-ref case).
            for norm, count in ref_counts.items():
                if count < 2:
                    continue
                display = norm
                for src in ref_sources.get(norm, set()):
                    node = self.graph.nodes.get(src)
                    if not node:
                        continue
                    for m in WIKILINK_RE.findall(node.get("content", "") or ""):
                        if self.graph._normalize_name(m) == norm:  # noqa: SLF001
                            display = m.strip().lstrip("[")
                            break
                    if display != norm:
                        break
                out.append({
                    "kind": "missing_entity",
                    "topic": display,
                    "normalized_name": norm,
                    "reference_count": count,
                    "referenced_by": sorted(ref_sources.get(norm, set())),
                    "file_path": None,
                    "base_priority": count * 10,
                })
            return out
        except Exception as e:
            self._log_error("collect_missing_entities", e)
            return []

    def _collect_thin_communities(self) -> List[Dict[str, Any]]:
        """Signal 4: cliques of ≥3 linked notes with no MOC/Index hub.

        For each note, check whether its neighbor set forms a clique of at
        least ``thin_community_min_size`` notes where none of the members is a
        hub (name contains "MOC" or "Index"). A clique here is approximated by
        "every pair of neighbors is mutually linked" — a strict but cheap
        check. We emit one gap per detected clique, keyed by its smallest
        member so duplicates collapse naturally.
        """
        try:
            min_size = self.thin_community_min_size
            out: List[Dict[str, Any]] = []
            seen_cliques: Set[Tuple[str, ...]] = set()

            for name in self.graph.nodes:
                neighbors = self.graph.neighbors(name, direction="both")
                # Restrict to neighbors that aren't themselves hubs.
                non_hub_neighbors = [
                    n for n in neighbors
                    if not _is_hub_name(self.graph.nodes.get(n, {}).get("name", n))
                    and not _is_hub_name(name)
                ]
                if len(non_hub_neighbors) < min_size - 1:
                    # The clique includes `name` itself, so we need ≥ min_size-1
                    # neighbors to reach min_size total.
                    continue

                # Build the candidate clique: name + non-hub neighbors.
                members = [name] + non_hub_neighbors
                # Keep only members that are mutually linked to *every* other
                # member (strict clique). This is O(k^2) per note but k is tiny.
                clique: List[str] = []
                for m in members:
                    linked = set(self.graph.neighbors(m, direction="both"))
                    if all(other in linked for other in members if other != m):
                        clique.append(m)

                if len(clique) < min_size:
                    continue

                clique_sorted = sorted(set(clique))
                key = tuple(clique_sorted)
                if key in seen_cliques:
                    continue
                seen_cliques.add(key)

                # Represent the clique by its smallest member's display name;
                # the topic is the *missing* hub, phrased as the clique.
                display_members = [
                    self.graph.nodes.get(n, {}).get("name", n) for n in clique_sorted
                ]
                topic = "MOC for: " + ", ".join(display_members[:6])
                # Referenced_by = the clique members (they'd backlink a hub).
                out.append({
                    "kind": "thin_community",
                    "topic": topic,
                    "normalized_name": "|".join(clique_sorted),
                    "reference_count": len(clique_sorted),
                    "referenced_by": clique_sorted,
                    "file_path": None,
                    "base_priority": len(clique_sorted),
                })

            return out
        except Exception as e:
            self._log_error("collect_thin_communities", e)
            return []

    def _collect_link_density_anomalies(self) -> List[Dict[str, Any]]:
        """Signal 5: notes with many out-links but zero in-links (sinks).

        A note that links out heavily but is linked back to by nobody is a
        dead-end; the curriculum suggests it should be re-linked into the
        graph. Lower priority than the other signals.
        """
        try:
            out: List[Dict[str, Any]] = []
            threshold = self.link_density_min_outlinks
            for name, node in self.graph.nodes.items():
                out_links = self.graph.edges.get(name, set())
                in_links = self.graph.backlinks.get(name, set())
                if len(out_links) >= threshold and not in_links:
                    out.append({
                        "kind": "link_density",
                        "topic": node.get("name", name),
                        "normalized_name": name,
                        "reference_count": len(out_links),
                        "referenced_by": [],  # by definition, nobody links in
                        "file_path": node.get("file_path"),
                        "base_priority": 1,  # deliberately low
                    })
            return out
        except Exception as e:
            self._log_error("collect_link_density_anomalies", e)
            return []

    # ------------------------------------------------------------------
    # Filtering + scoring
    # ------------------------------------------------------------------
    def _filter_candidates(
        self, candidates: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Drop completed topics and topics that failed very recently.

        A failed topic is suppressed for a cooldown window; the achievability
        bonus already crushes gaps that failed ≥3 times, so here we only
        hard-filter topics that failed in the *most recent* attempt to avoid
        an immediate retry loop.
        """
        completed: Set[str] = {
            t.lower() for t in (self.state.get("completed_topics") or [])
            if isinstance(t, str)
        }
        failed_recently: Set[str] = {
            fr.get("topic", "").lower()
            for fr in (self.state.get("failed_topics") or [])
            if isinstance(fr, dict) and fr.get("attempts", 0) >= 3
        }

        kept: List[Dict[str, Any]] = []
        for g in candidates:
            topic = (g.get("topic") or "").lower()
            norm = (g.get("normalized_name") or "").lower()
            if topic in completed or norm in completed:
                continue
            # thin_community normalized_name is a pipe-joined clique key; only
            # the topic string is meaningful for completion checks.
            if g.get("kind") != "thin_community" and (
                topic in failed_recently or norm in failed_recently
            ):
                continue
            # Quality gate: reject trivial / placeholder topics that would
            # produce dictionary-scraping junk notes. thin_community topics
            # ("MOC for: ...") are synthetic and always pass — the gate is
            # about the raw dangling-link / thin-note labels.
            if not _is_researchable_topic(g):
                continue
            kept.append(g)
        return kept

    def _score_gap(self, gap: Dict[str, Any]) -> Dict[str, Any]:
        """Apply the Voyager curriculum priority to a single gap.

            priority = base_priority * diversity_bonus * achievability_bonus * context_bonus

        Returns the gap dict augmented with ``priority`` and ``score_breakdown``.
        """
        base = float(gap.get("base_priority", 1) or 1)
        diversity = self._diversity_bonus(gap)
        achievability = self._achievability_bonus(gap)
        context = self._context_bonus(gap)
        priority = base * diversity * achievability * context

        breakdown: Dict[str, float] = {
            "base_priority": base,
            "diversity_bonus": diversity,
            "achievability_bonus": achievability,
            "context_bonus": context,
            "priority": priority,
        }
        gap = dict(gap)  # shallow copy so we don't mutate the candidate
        gap["priority"] = priority
        gap["score_breakdown"] = breakdown
        gap["reason"] = self._explain(gap, breakdown)
        return gap

    def _diversity_bonus(self, gap: Dict[str, Any]) -> float:
        """Penalize gaps too similar to recently-completed topics.

        A gap whose tokens heavily overlap the last ``diversity_window``
        completed topics gets ×0.3; moderate overlap → ×0.6; no overlap → ×1.0.
        """
        completed = (self.state.get("completed_topics") or [])[-self.diversity_window:]
        if not completed:
            return 1.0

        topic = gap.get("topic", "") or ""
        # For thin_community, also fold in the clique member names so diversity
        # considers the actual notes, not the synthetic "MOC for: ..." string.
        if gap.get("kind") == "thin_community":
            ref_by = gap.get("referenced_by") or []
            topic = topic + " " + " ".join(
                self.graph.nodes.get(n, {}).get("name", n) for n in ref_by
            )

        max_overlap = 0.0
        for done in completed:
            if not isinstance(done, str):
                continue
            ov = _token_overlap(topic, done)
            if ov > max_overlap:
                max_overlap = ov

        if max_overlap >= 0.5:
            return 0.3
        if max_overlap >= 0.2:
            return 0.6
        return 1.0

    def _achievability_bonus(self, gap: Dict[str, Any]) -> float:
        """Reward gaps that are cheap to close; crush repeatedly-failed ones.

        - thin_note (already exists, just needs expanding): ×1.5
        - dangling_link with 1 reference: ×1.0
        - dangling_link with many references (high value, harder): ×1.2
        - missing_entity: ×1.1 (already partially surfaced)
        - thin_community / link_density: ×1.0
        - any topic that failed ≥3 times: ×0.1
        """
        kind = gap.get("kind", "")
        ref_count = int(gap.get("reference_count", 0) or 0)

        if kind == "thin_note":
            bonus = 1.5
        elif kind == "dangling_link":
            bonus = 1.0 if ref_count <= 1 else 1.2
        elif kind == "missing_entity":
            bonus = 1.1
        elif kind == "thin_community":
            bonus = 1.0
        elif kind == "link_density":
            bonus = 1.0
        else:
            bonus = 1.0

        # Failure penalty: ≥3 attempts collapses the bonus.
        topic = (gap.get("topic") or "").lower()
        norm = (gap.get("normalized_name") or "").lower()
        for fr in self.state.get("failed_topics") or []:
            if not isinstance(fr, dict):
                continue
            ft = (fr.get("topic") or "").lower()
            fn = ""
            attempts = int(fr.get("attempts", 0) or 0)
            if ft == topic or (fn and fn == norm):
                if attempts >= 3:
                    return 0.1
                if attempts >= 1:
                    bonus *= 0.7
        return bonus

    def _context_bonus(self, gap: Dict[str, Any]) -> float:
        """Boost gaps whose referenced_by notes are well-connected (high degree).

        Filling a gap that wedges into a rich neighborhood (referencing notes
        have many neighbors) yields more graph rewiring per research effort.
        Returns ×1.3 when the average referencing-note degree is high, ×1.0
        otherwise.
        """
        ref_by = gap.get("referenced_by") or []
        if not ref_by:
            return 1.0
        degrees: List[int] = []
        for src in ref_by:
            if not isinstance(src, str):
                continue
            degree = len(self.graph.neighbors(src, direction="both"))
            degrees.append(degree)
        if not degrees:
            return 1.0
        avg_degree = sum(degrees) / len(degrees)
        # "High degree" = the referencing notes themselves have ≥4 neighbors
        # on average. Tunable; kept conservative so it nudges, not dominates.
        if avg_degree >= 4:
            return 1.3
        if avg_degree >= 2:
            return 1.1
        return 1.0

    def _explain(
        self, gap: Dict[str, Any], breakdown: Dict[str, float]
    ) -> str:
        """Human-readable reason string for why this gap was selected."""
        try:
            kind = gap.get("kind", "gap")
            topic = gap.get("topic", "")
            parts: List[str] = [f"{kind}='{topic}'"]
            parts.append(f"base={breakdown['base_priority']:.1f}")
            parts.append(f"div={breakdown['diversity_bonus']:.2f}")
            parts.append(f"ach={breakdown['achievability_bonus']:.2f}")
            parts.append(f"ctx={breakdown['context_bonus']:.2f}")
            parts.append(f"priority={breakdown['priority']:.2f}")
            return " | ".join(parts)
        except Exception:
            return "curriculum-selected gap"


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------
def _is_hub_name(name: str) -> bool:
    """True if a note name looks like a MOC/Index hub note."""
    if not name:
        return False
    n = name.lower()
    return "moc" in n or "index" in n