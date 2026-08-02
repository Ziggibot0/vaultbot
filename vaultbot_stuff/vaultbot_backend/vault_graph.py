import re
import threading
from collections import deque
from pathlib import Path
from typing import Any

WIKILINK_RE = re.compile(r"\[\[([^\][\|\r\n]+)(?:\|[^\]\r\n]+)?\]\]")
# Directories the graph should skip when scanning the vault. Mirrors the
# indexer's IGNORED_DIRS so the graph and the FAISS index see the same files;
# without this the graph was ingesting plugin READMEs, venv files, partial
# crash-recovery notes, etc. as graph nodes.
_IGNORED_DIRS = {
    ".venv",
    "vaultbot_venv",  # legacy name; superseded by .venv
    "vaultbot_index",
    "sessions",
    "partials",
    ".git",
    ".obsidian",
}


def _is_ignored_path(path: Path) -> bool:
    for part in path.parts:
        if part in _IGNORED_DIRS:
            return True
    return False

class VaultGraph:
    """
    Treats the Obsidian vault as a directed graph where notes are nodes and
    wikilinks are edges.  The bot "thinks" by walking this graph, not by
    trusting an LLM's weights.
    """

    def __init__(self, vault_path: str, session_logger=None, max_note_size: int = 12_000):
        self.vault_path = Path(vault_path).resolve()
        self.session_logger = session_logger
        self.max_note_size = max_note_size
        self.nodes: dict[str, dict[str, Any]] = {}  # normalized name -> metadata
        self.edges: dict[str, set[str]] = {}        # normalized name -> set of normalized target names
        self.backlinks: dict[str, set[str]] = {}    # normalized name -> set of normalized source names
        # Per-node file mtime (epoch seconds) for incremental refresh.
        self._mtimes: dict[str, float] = {}
        # Max mtime seen at the last refresh; cheap stat-only fast path checks
        # against this to skip all work when nothing has changed on disk.
        self._last_refresh_mtime: float = 0.0
        # Concurrency guard: the watchdog / autonomous researcher thread
        # calls refresh() while the chat loop reads nodes/edges/backlinks.
        # Without a lock this raises "RuntimeError: dictionary changed size
        # during iteration" on the read paths.  RLock so refresh can call
        # internal helpers that also acquire the lock.
        self._lock = threading.RLock()
        self._build_graph()

    def _log_tool(self, method: str, inputs: dict[str, Any] | None = None,
                  outputs: Any = None, error: str | None = None):
        if self.session_logger is None:
            return
        self.session_logger.log_tool_call(
            tool="vault_graph", method=method, inputs=inputs,
            outputs=outputs, error=error, duration_ms=None
        )

    def _normalize_name(self, name: str) -> str:
        """Wikilinks are case-insensitive and tolerate leading/trailing spaces."""
        return name.strip().lower().replace("\\", "/")

    def _resolve_note_path(self, name: str) -> Path | None:
        """Find an actual markdown file matching a wikilink target."""
        norm = self._normalize_name(name)
        # Exact match first
        for p in self.vault_path.rglob("*.md"):
            if self._normalize_name(p.stem) == norm:
                return p
        # Then partial
        for p in self.vault_path.rglob("*.md"):
            if norm in self._normalize_name(p.stem):
                return p
        return None

    def _read_note(self, path: Path) -> str:
        try:
            text = path.read_text(encoding="utf-8")
            if len(text) > self.max_note_size:
                return text[:self.max_note_size] + "\n\n[truncated]"
            return text
        except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
            self._log_tool("read_note", {"file_path": str(path)}, error=str(e))
            return ""

    def _extract_wikilinks(self, text: str) -> set[str]:
        return {self._normalize_name(match) for match in WIKILINK_RE.findall(text)}

    def _collect_md_files(self) -> list[Path]:
        """Scan the vault for markdown files, pruning ignored directories
        in-place during the walk.

        ``rglob`` can't skip a subtree once it has entered it, so a venv or
        ``.git`` directory full of non-vault files still gets fully traversed
        and then filtered out afterward (slow on this vault — ~670ms for the
        raw rglob vs ~180ms for a pruned os.walk). Pruning ``dirs[:]`` in
        place during ``os.walk`` tells the walker to never descend into the
        ignored subtrees at all, which is what makes the mtime-gated refresh
        actually cheap on the warm path.
        """
        import os as _os
        out: list[Path] = []
        for root, dirs, files in _os.walk(self.vault_path):
            # Prune ignored dirs in-place so os.walk doesn't descend into them.
            dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
            for f in files:
                if f.endswith(".md"):
                    p = Path(root) / f
                    if not _is_ignored_path(p):
                        out.append(p)
        return out

    def _add_or_update_node(self, path: Path) -> str:
        """Insert/update a single node from disk and return its normalized name.

        Re-reading happens only for files the caller has determined changed;
        this routine reads the file, rebuilds that node's edges/backlinks, and
        preserves edge reciprocity. It does NOT touch other nodes except to
        fix up their backlink sets (edges are global: a changed file's links
        affect the backlinks of its targets).
        """
        name = self._normalize_name(path.stem)
        # Drop any existing edges for this node before re-adding, so stale
        # links (targets that no longer exist or were unlinked) are cleared.
        self._remove_edges_for(name)
        content = self._read_note(path)
        self.nodes[name] = {
            "file_path": str(path),
            "name": path.stem,
            "content": content,
            "links": set(),
        }
        self.edges[name] = set()
        self.backlinks.setdefault(name, set())
        try:
            self._mtimes[name] = path.stat().st_mtime
        except OSError:
            self._mtimes[name] = 0.0
        # Re-wire this node's outgoing edges and the reciprocal backlinks.
        for target in self._extract_wikilinks(content):
            if target in self.nodes:
                self.edges[name].add(target)
                self.backlinks[target].add(name)
                self.nodes[name]["links"].add(target)
        return name

    def _remove_edges_for(self, name: str) -> None:
        """Remove a node's outgoing edges and the reciprocal backlinks."""
        for target in list(self.edges.get(name, set())):
            self.backlinks.get(target, set()).discard(name)
        self.edges.get(name, set()).clear()
        # Also drop this node as a backlink source from its old targets.
        for src in list(self.backlinks.get(name, set())):
            self.edges.get(src, set()).discard(name)
            src_node = self.nodes.get(src)
            if src_node:
                src_node.get("links", set()).discard(name)

    def _remove_node(self, name: str) -> None:
        """Fully evict a node (deleted file): edges, backlinks, metadata."""
        self._remove_edges_for(name)
        self.edges.pop(name, None)
        self.backlinks.pop(name, None)
        self.nodes.pop(name, None)
        self._mtimes.pop(name, None)

    def _build_graph(self):
        """Scan the vault once and build node/edge/backlink maps."""
        md_files = self._collect_md_files()
        max_mtime = 0.0
        for path in md_files:
            name = self._normalize_name(path.stem)
            if name in self.nodes:
                continue
            self._add_or_update_node(path)
            max_mtime = max(max_mtime, self._mtimes.get(name, 0.0))

        for name, node in self.nodes.items():
            targets = self._extract_wikilinks(node["content"])
            for target in targets:
                # Only add edges to notes that exist in the vault
                if target in self.nodes:
                    self.edges[name].add(target)
                    self.backlinks[target].add(name)
                    self.nodes[name]["links"].add(target)

        self._last_refresh_mtime = max_mtime
        self._log_tool("build_graph", {
            "vault_path": str(self.vault_path),
            "note_count": len(self.nodes),
            "edge_count": sum(len(v) for v in self.edges.values()),
        })

    def _detect_changes(self) -> tuple[list[Path], list[str]]:
        """Stat-only scan for changed/new/deleted files since the last refresh.

        Returns (changed_or_new_paths, deleted_normalized_names). No file
        contents are read here — only stat(). This is the fast path that lets
        `refresh()` be nearly free when the vault hasn't been edited.
        """
        md_files = self._collect_md_files()
        seen_paths = set()
        changed_or_new: list[Path] = []
        for path in md_files:
            seen_paths.add(str(path))
            name = self._normalize_name(path.stem)
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime > self._last_refresh_mtime or name not in self.nodes:
                changed_or_new.append(path)
        # Deleted: previously-known paths no longer present on disk.
        deleted = [name for name, node in self.nodes.items()
                   if node.get("file_path") and node["file_path"] not in seen_paths]
        return changed_or_new, deleted

    def refresh_if_changed(self) -> bool:
        """Refresh the graph only if files changed on disk since last refresh.

        Returns True if any change was applied, False if the graph was already
        up to date (no disk reads beyond stat()). This is the cheap path that
        `handle_chat` and `propose_next_gaps` rely on so consecutive messages
        in a warm session don't pay for a full vault rescan.
        """
        with self._lock:
            changed, deleted = self._detect_changes()
            if not changed and not deleted:
                return False
            # Apply only the delta. Edges are global, so each changed/removed
            # node's edges are rewired individually.
            max_mtime = self._last_refresh_mtime
            for path in changed:
                name = self._add_or_update_node(path)
                max_mtime = max(max_mtime, self._mtimes.get(name, 0.0))
            for name in deleted:
                self._remove_node(name)
            self._last_refresh_mtime = max_mtime
            self._log_tool("incremental_refresh", {
                "changed_count": len(changed),
                "deleted_count": len(deleted),
                "note_count": len(self.nodes),
                "edge_count": sum(len(v) for v in self.edges.values()),
            })
            return True

    def refresh(self):
        """Refresh the graph from disk, incrementally when possible.

        Previously this rebuilt the entire graph from scratch on every call
        (a full rglob + read of every .md file). It now stats files and only
        re-reads the ones that changed, which makes the common no-edit case
        nearly free — important because `handle_chat` and the knowledge
        curriculum both call this per message. Falls back to a full rebuild
        if incremental tracking has no state yet.
        """
        with self._lock:
            if not self.nodes and self._last_refresh_mtime == 0.0:
                # First-ever build: do the full scan once.
                self._build_graph()
                return
            self.refresh_if_changed()

    def neighbors(self, name: str, direction: str = "both") -> list[str]:
        """Return linked notes and/or backlinked notes."""
        with self._lock:
            norm = self._normalize_name(name)
            result = set()
            if direction in ("out", "both"):
                result.update(self.edges.get(norm, set()))
            if direction in ("in", "both"):
                result.update(self.backlinks.get(norm, set()))
            return sorted(result)

    def walk(self, start_names: list[str], depth: int = 2,
             min_backlinks: int = 1) -> dict[str, Any]:
        """
        Breadth-first walk starting from a set of seed notes.

        Returns a subgraph description: {nodes: [...], edges: [...], stats: {...}}
        min_backlinks: only include a note if it has at least this many backlinks
        OR is a direct neighbor of a seed (keeps context from getting too thin).
        """
        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque()
        for name in start_names:
            norm = self._normalize_name(name)
            if norm in self.nodes:
                queue.append((norm, 0))
                visited.add(norm)

        while queue:
            current, d = queue.popleft()
            if d >= depth:
                continue
            for neighbor in self.neighbors(current, direction="both"):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, d + 1))

        # Filter by backlink threshold unless it is a seed or direct neighbor of seed
        seeds = {self._normalize_name(n) for n in start_names}
        direct_neighbors: set[str] = set()
        for seed in seeds:
            direct_neighbors.update(self.neighbors(seed, direction="both"))

        selected = []
        for name in visited:
            node = self.nodes.get(name)
            if not node:
                continue
            if name in seeds or name in direct_neighbors:
                selected.append(name)
                continue
            if len(self.backlinks.get(name, set())) >= min_backlinks:
                selected.append(name)

        nodes_out = []
        edges_out = []
        for name in selected:
            node = self.nodes[name]
            nodes_out.append({
                "name": node["name"],
                "file_path": node["file_path"],
                "content": node["content"],
                "backlinks": sorted(self.backlinks.get(name, set())),
                "outgoing_links": sorted(self.edges.get(name, set())),
            })
            for target in self.edges.get(name, set()):
                if target in selected:
                    edges_out.append({"from": name, "to": target})

        self._log_tool("graph_walk", {
            "seeds": start_names,
            "depth": depth,
            "visited": len(visited),
            "selected": len(selected),
        })

        return {
            "nodes": nodes_out,
            "edges": edges_out,
            "stats": {
                "seeds": len(seeds),
                "visited": len(visited),
                "selected": len(selected),
                "depth": depth,
            },
        }

    def note_exists(self, name: str) -> bool:
        with self._lock:
            return self._normalize_name(name) in self.nodes

    def get_note(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            return self.nodes.get(self._normalize_name(name))

    def dangling_links(self, min_references: int = 1) -> list[dict[str, Any]]:
        """Return wikilink targets that do NOT resolve to any note.

        These are the vault's own declarations of what it wants to know.
        Each entry is sorted by reference count (most-wanted first) so the
        autonomous researcher can prioritize the deepest gaps.

        Args:
            min_references: only report a gap if at least this many notes link
                to the missing target. Default 1 = every red link is a gap.
        """
        with self._lock:
            # Count how many distinct notes link to each unresolved target.
            # `edges` only contains resolved targets, so we must re-scan raw
            # content for *unresolved* wikilinks.
            ref_counts: dict[str, int] = {}
            ref_sources: dict[str, set[str]] = {}
            # Re-scan every note's raw content for ALL wikilinks (resolved or not).
            for name, node in self.nodes.items():
                raw_links = WIKILINK_RE.findall(node["content"])
                for link in raw_links:
                    norm = self._normalize_name(link)
                    if norm not in self.nodes:  # dangling
                        ref_counts[norm] = ref_counts.get(norm, 0) + 1
                        ref_sources.setdefault(norm, set()).add(name)

            gaps = []
            for norm, count in ref_counts.items():
                if count < min_references:
                    continue
                # Recover a human-readable display name from the first source's
                # raw link text; fall back to the normalized form.
                display = norm
                for src in ref_sources.get(norm, set()):
                    node = self.nodes.get(src)
                    if not node:
                        continue
                    for m in WIKILINK_RE.findall(node["content"]):
                        if self._normalize_name(m) == norm:
                            # Strip stray leading '[' from malformed nested links
                            # like [[[[Note]]|Note]] which the regex can capture.
                            display = m.strip().lstrip("[")
                            break
                    if display != norm:
                        break
                gaps.append({
                    "name": display,
                    "normalized_name": norm,
                    "reference_count": count,
                    "referenced_by": sorted(ref_sources.get(norm, set())),
                })
            gaps.sort(key=lambda g: g["reference_count"], reverse=True)
            return gaps

    def thin_notes(self, min_content_length: int = 200) -> list[dict[str, Any]]:
        """Return notes whose body is shorter than min_content_length.

        These are notes that exist but don't yet say enough — another kind of
        knowledge gap the autonomous researcher can fill.
        """
        with self._lock:
            thin = []
            for name, node in self.nodes.items():
                content = node.get("content", "") or ""
                # Strip frontmatter and wikilinks for a truer body length.
                body = re.sub(r"^\s*---.*?---\s*", "", content, count=1, flags=re.DOTALL)
                body = WIKILINK_RE.sub("", body)
                body = re.sub(r"\s+", " ", body).strip()
                if len(body) < min_content_length:
                    thin.append({
                        "name": node["name"],
                        "normalized_name": name,
                        "file_path": node["file_path"],
                        "content_length": len(body),
                    })
            thin.sort(key=lambda n: n["content_length"])
            return thin


def build_graph_context(graph: VaultGraph, search_results: list[dict[str, Any]],
                        query: str, k: int = 5, depth: int = 2,
                        max_notes: int = 25, per_note_cap: int = 900,
                        total_cap: int = 20000) -> str:
    """
    Given flat search results, turn them into a rich graph context prompt.

    BOUNDED (the "context flood" fix): the legacy dump appended
    node["content"][:2000] for EVERY walked node. At 5 seeds × depth 2 that is
    20-40+ notes — a 40-50K-char blob that got pinned as the sacred head of
    the conversation and re-sent verbatim every agentic round, inflating the
    remote model's TTFT into the 13-32s "read-loop wall" where the agent
    spins without converging. Now: cap the number of notes, shrink each
    note's snippet, and hard-cap the whole context. The full notes are always
    reachable via vault_search / the L1 card `> source` link if the model
    needs more — this is an orientation map, not the whole vault.
    """
    seed_names = []
    for res in search_results[:k]:
        path = Path(res.get("file_path", ""))
        if path.exists():
            seed_names.append(path.stem)

    if not seed_names:
        return "VAULT CONTEXT: (no relevant notes found in the vault graph)"

    subgraph = graph.walk(seed_names, depth=depth)

    lines = [
        "VAULT CONTEXT — relevant sub-vault graph",
        f"Query: {query}",
        f"Graph stats: {subgraph['stats']['selected']} connected notes "
        f"from {subgraph['stats']['seeds']} seed(s), depth {subgraph['stats']['depth']}.",
        "",
        "--- CONNECTED NOTES ---",
    ]

    # Bound the note dump: highest-value nodes first (the walk already orders
    # them by relevance/graph distance from the seeds), capped count + size.
    nodes = subgraph["nodes"][:max_notes]
    for node in nodes:
        lines.append(f"\n### [[{node['name']}]]")
        if node["outgoing_links"]:
            lines.append("Links out: " + ", ".join(f"[[{n}]]" for n in node["outgoing_links"]))
        if node["backlinks"]:
            lines.append("Linked from: " + ", ".join(f"[[{n}]]" for n in node["backlinks"]))
        lines.append("")
        snippet = node["content"][:per_note_cap]
        if len(node["content"]) > per_note_cap:
            snippet += "\n*[... full note via vault_search / card > source ...]*"
        lines.append(snippet)
    if subgraph["stats"]["selected"] > len(nodes):
        lines.append(
            f"\n*[... {subgraph['stats']['selected'] - len(nodes)} more connected "
            "notes not shown — vault_search for specifics ...]*")

    if subgraph["edges"]:
        lines.append("\n--- GRAPH EDGES ---")
        for edge in subgraph["edges"]:
            lines.append(f"[[{edge['from']}]] -> [[{edge['to']}]]")

    out = "\n".join(lines)
    if len(out) > total_cap:
        out = out[:total_cap] + (
            "\n\n*[... context truncated to stay within budget; vault_search "
            "for full note content ...]*")
    return out
