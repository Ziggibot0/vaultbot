import os
import re
import json
from pathlib import Path
from typing import Dict, List, Set, Any, Optional, Tuple
from datetime import datetime, timezone
from collections import deque

WIKILINK_RE = re.compile(r"\[\[([^\][\|\r\n]+)(?:\|[^\]\r\n]+)?\]\]")


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
        self.nodes: Dict[str, Dict[str, Any]] = {}  # normalized name -> metadata
        self.edges: Dict[str, Set[str]] = {}        # normalized name -> set of normalized target names
        self.backlinks: Dict[str, Set[str]] = {}    # normalized name -> set of normalized source names
        self._build_graph()

    def _log_tool(self, method: str, inputs: Optional[Dict[str, Any]] = None,
                  outputs: Any = None, error: Optional[str] = None):
        if self.session_logger is None:
            return
        self.session_logger.log_tool_call(
            tool="vault_graph", method=method, inputs=inputs,
            outputs=outputs, error=error, duration_ms=None
        )

    def _normalize_name(self, name: str) -> str:
        """Wikilinks are case-insensitive and tolerate leading/trailing spaces."""
        return name.strip().lower().replace("\\", "/")

    def _resolve_note_path(self, name: str) -> Optional[Path]:
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
        except Exception as e:
            self._log_tool("read_note", {"file_path": str(path)}, error=str(e))
            return ""

    def _extract_wikilinks(self, text: str) -> Set[str]:
        return {self._normalize_name(match) for match in WIKILINK_RE.findall(text)}

    def _build_graph(self):
        """Scan the vault once and build node/edge/backlink maps."""
        md_files = [p for p in self.vault_path.rglob("*.md")]
        for path in md_files:
            name = self._normalize_name(path.stem)
            if name in self.nodes:
                continue
            content = self._read_note(path)
            self.nodes[name] = {
                "file_path": str(path),
                "name": path.stem,
                "content": content,
                "links": set(),
            }
            self.edges[name] = set()
            self.backlinks[name] = set()

        for name, node in self.nodes.items():
            targets = self._extract_wikilinks(node["content"])
            for target in targets:
                # Only add edges to notes that exist in the vault
                if target in self.nodes:
                    self.edges[name].add(target)
                    self.backlinks[target].add(name)
                    self.nodes[name]["links"].add(target)

        self._log_tool("build_graph", {
            "vault_path": str(self.vault_path),
            "note_count": len(self.nodes),
            "edge_count": sum(len(v) for v in self.edges.values()),
        })

    def refresh(self):
        """Rebuild the graph from disk."""
        self.nodes.clear()
        self.edges.clear()
        self.backlinks.clear()
        self._build_graph()

    def neighbors(self, name: str, direction: str = "both") -> List[str]:
        """Return linked notes and/or backlinked notes."""
        norm = self._normalize_name(name)
        result = set()
        if direction in ("out", "both"):
            result.update(self.edges.get(norm, set()))
        if direction in ("in", "both"):
            result.update(self.backlinks.get(norm, set()))
        return sorted(result)

    def walk(self, start_names: List[str], depth: int = 2,
             min_backlinks: int = 1) -> Dict[str, Any]:
        """
        Breadth-first walk starting from a set of seed notes.

        Returns a subgraph description: {nodes: [...], edges: [...], stats: {...}}
        min_backlinks: only include a note if it has at least this many backlinks
        OR is a direct neighbor of a seed (keeps context from getting too thin).
        """
        visited: Set[str] = set()
        queue: deque[Tuple[str, int]] = deque()
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
        direct_neighbors: Set[str] = set()
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
        return self._normalize_name(name) in self.nodes

    def get_note(self, name: str) -> Optional[Dict[str, Any]]:
        return self.nodes.get(self._normalize_name(name))

    def dangling_links(self, min_references: int = 1) -> List[Dict[str, Any]]:
        """Return wikilink targets that do NOT resolve to any note.

        These are the vault's own declarations of what it wants to know.
        Each entry is sorted by reference count (most-wanted first) so the
        autonomous researcher can prioritize the deepest gaps.

        Args:
            min_references: only report a gap if at least this many notes link
                to the missing target. Default 1 = every red link is a gap.
        """
        # Count how many distinct notes link to each unresolved target.
        ref_counts: Dict[str, int] = {}
        ref_sources: Dict[str, Set[str]] = {}
        for source_name, targets in self.edges.items():
            for target in targets:
                # `edges` only contains resolved targets, so we must re-scan
                # raw content for *unresolved* wikilinks. Build a full set of
                # referenced names (resolved or not) per note.
                pass
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

    def thin_notes(self, min_content_length: int = 200) -> List[Dict[str, Any]]:
        """Return notes whose body is shorter than min_content_length.

        These are notes that exist but don't yet say enough â€” another kind of
        knowledge gap the autonomous researcher can fill.
        """
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


def build_graph_context(graph: VaultGraph, search_results: List[Dict[str, Any]],
                        query: str, k: int = 5, depth: int = 2) -> str:
    """
    Given flat search results, turn them into a rich graph context prompt.
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
        "VAULT CONTEXT â€” relevant sub-vault graph",
        f"Query: {query}",
        f"Graph stats: {subgraph['stats']['selected']} connected notes "
        f"from {subgraph['stats']['seeds']} seed(s), depth {subgraph['stats']['depth']}.",
        "",
        "--- CONNECTED NOTES ---",
    ]

    for node in subgraph["nodes"]:
        lines.append(f"\n### [[{node['name']}]]")
        if node["outgoing_links"]:
            lines.append(f"Links out: " + ", ".join(f"[[{n}]]" for n in node["outgoing_links"]))
        if node["backlinks"]:
            lines.append(f"Linked from: " + ", ".join(f"[[{n}]]" for n in node["backlinks"]))
        lines.append("")
        lines.append(node["content"][:2_000])

    if subgraph["edges"]:
        lines.append("\n--- GRAPH EDGES ---")
        for edge in subgraph["edges"]:
            lines.append(f"[[{edge['from']}]] -> [[{edge['to']}]]")

    return "\n".join(lines)
