"""
A-MEM note evolution (arXiv:2502.12110, NeurIPS 2025).

Implements the knowledge-accumulation half of the self-growth loop:
when a new note is created, find semantically similar neighbors and
evolve their context/tags/links so the vault "learns by refining."

A-MEM (Agentic Memory) is a Zettelkasten-style memory system where new
memories rewrite existing neighbors — each new note triggers an
opportunistic refinement of nearby notes: their tags get enriched,
their context broadened, and bidirectional links woven in. This module
mirrors that behavior over the local Obsidian-style vault.

Reference:
    S. Xu et al., "A-MEM: Agentic Memory for LLM Agents", arXiv:2502.12110.

Pure stdlib + existing project imports. No new dependencies.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path

import numpy as np
from vault_graph import VaultGraph
from vault_indexer import VaultIndexer

log = logging.getLogger(__name__)


class AMemeEvolution:
    """Evolves neighboring notes when a new note is created (A-MEM style)."""

    DEFAULT_K = 5
    CONTENT_PREVIEW_CHARS = 500
    MAX_NEIGHBOR_CHARS_FOR_LLM = 4000  # cap neighbor context sent to LLM

    def __init__(
        self,
        vault_path: str,
        vault_graph: VaultGraph,
        vault_indexer: VaultIndexer,
        ollama_client=None,
        session_logger=None,
    ) -> None:
        self.vault_path = Path(vault_path).resolve()
        self.vault_graph = vault_graph
        self.vault_indexer = vault_indexer
        self.ollama_client = ollama_client
        self.session_logger = session_logger

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def evolve_on_create(self, note_path: str, note_content: str,
                         heuristic_only: bool = False,
                         query_embedding: list | None = None,
                         skip_refresh: bool = False) -> dict:
        """
        Main entry point. Called after a new note is created.

        Finds semantically similar neighbors and evolves their tags/links
        so the vault accumulates knowledge by refining existing notes.

        Parameters
        ----------
        heuristic_only:
            When True, skip the per-neighbor LLM tag-suggestion call and use
            the token-overlap heuristic only. Used by batch weaves (e.g.
            textbook ingest) where N notes x K neighbors would otherwise
            produce hundreds of LLM calls — most of them low-value because
            textbook section titles are unambiguous. The single-note path
            (vault_research / writing_note) keeps the LLM path.
        query_embedding:
            Optional pre-computed embedding for the new note. If supplied,
            the neighbor search reuses it instead of re-embedding the note
            text via Ollama — saves one embedding call per evolve.
        skip_refresh:
            When True, skip the graph/index refresh. A batch weave that
            just indexed the notes and rebuilt the graph once at the top of
            the pass can pass this for every subsequent note in the same
            weave — the graph doesn't change between consecutive notes.

        Returns a summary dict; never raises.
        """
        result: dict = {
            "evolved_count": 0,
            "neighbors": [],
            "links_added": 0,
            "tags_updated": 0,
        }
        try:
            note_p = Path(note_path)
            title = note_p.stem
            if not title:
                self._log_event("evolve_skip_empty_title", {"note_path": note_path})
                return result

            # Ensure indexer/graph see latest state if they support refresh.
            if not skip_refresh:
                self._refresh_indices()

            if query_embedding is not None:
                try:
                    hits = self.vault_indexer.search_by_vector(
                        np.asarray(query_embedding, dtype=np.float32),
                        k=self.DEFAULT_K)
                except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                    self._log_error("indexer_search_failed", e)
                    hits = []
            else:
                query = (title + " " + note_content[: self.CONTENT_PREVIEW_CHARS]).strip()
                try:
                    hits = self.vault_indexer.search(query, k=self.DEFAULT_K)
                except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                    self._log_error("indexer_search_failed", e)
                    hits = []

            # Resolve new note's absolute path for exclusion.
            try:
                new_note_abs = str(note_p.resolve())
            except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                new_note_abs = str(note_p)

            neighbors: list[dict] = []
            for hit in hits:
                fp = hit.get("file_path") or hit.get("path") or ""
                if not fp:
                    continue
                try:
                    if str(Path(fp).resolve()) == new_note_abs:
                        continue
                except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                    if os.path.normpath(fp) == os.path.normpath(str(note_p)):
                        continue
                neighbors.append(hit)

            result["neighbors"] = [h.get("file_path", "") for h in neighbors]

            for hit in neighbors:
                npath = hit.get("file_path", "")
                # ALWAYS read the neighbor's full content from disk here, even
                # if the search hit carries a content_preview. _evolve_neighbor
                # mutates this content (tag + wikilink insertion) and writes it
                # back to the note file via _atomic_write — a capped preview
                # would silently truncate the stored note. The search-result
                # content field is a snippet for display/retrieval only.
                try:
                    ncontent = Path(npath).read_text(encoding="utf-8")
                except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                    self._log_error("read_neighbor_failed", e, {"path": npath})
                    continue

                ev = self._evolve_neighbor(npath, ncontent, title, note_content,
                                            heuristic_only=heuristic_only)
                if ev.get("changed"):
                    result["evolved_count"] += 1
                result["links_added"] += ev.get("links_added", 0)
                result["tags_updated"] += ev.get("tags_updated", 0)

            self._log_event(
                "evolve_on_create",
                {
                    "note_path": note_path,
                    "title": title,
                    "neighbor_count": len(neighbors),
                    "evolved_count": result["evolved_count"],
                    "links_added": result["links_added"],
                    "tags_updated": result["tags_updated"],
                },
            )
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            self._log_error("evolve_on_create_failed", e)
        return result

    # ------------------------------------------------------------------ #
    # Per-neighbor evolution
    # ------------------------------------------------------------------ #
    def _evolve_neighbor(
        self,
        neighbor_path: str,
        neighbor_content: str,
        new_note_title: str,
        new_note_content: str,
        heuristic_only: bool = False,
    ) -> dict:
        """
        Evolve a single neighbor note.

        - Asks the LLM (if available AND heuristic_only is False) for suggested
          tags, else token-overlap heuristic.
        - Appends suggested tags to neighbor frontmatter (atomic write).
        - Opportunistically inserts a [[new_note_title]] wikilink if the title
          appears as plain text in the neighbor body.

        Returns {"changed": bool, "links_added": int, "tags_updated": int}.
        Never raises.
        """
        out: dict = {"changed": False, "links_added": 0, "tags_updated": 0}
        try:
            content = neighbor_content

            # --- Determine new tags ---
            suggested_tags: list[str] = []
            # Heuristic first: if the new note's title appears as plain text in
            # the neighbor, tagging it with the title is a high-precision
            # signal — the neighbor literally mentions the concept. In that
            # case skip the LLM tag-suggestion call entirely (saves a
            # generative LLM call per matching neighbor on the single-note
            # research path). The LLM only runs when the heuristic misses,
            # i.e. the relation is semantic but not lexical.
            heuristic_match = (
                bool(new_note_title)
                and new_note_title.lower() in content.lower()
            )
            if heuristic_match:
                suggested_tags = [new_note_title]
            elif not heuristic_only:
                # Tier 2: embedding-based tag suggestion (local Ollama embedding,
                # NOT the cloud LLM). Extracts noun phrases from the highest-
                # similarity sentence fragments. Zero generative LLM calls.
                try:
                    suggested_tags = self._embedding_suggest_tags(
                        new_note_title, new_note_content, content)
                except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                    self._log_error("embedding_suggest_tags_failed", e)
                    suggested_tags = []
                # Tier 3 (last resort): LLM tag suggestion.
                if not suggested_tags and self.ollama_client is not None:
                    try:
                        suggested_tags = self._llm_suggest_tags(
                            new_note_title, new_note_content, content
                        )
                    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                        self._log_error("llm_suggest_tags_failed", e)
                        suggested_tags = []

            if not suggested_tags:
                # Fallback heuristic: if the new note's title token appears in the
                # neighbor content (plain text), add it as a tag.
                if new_note_title and new_note_title.lower() in content.lower():
                    suggested_tags = [new_note_title]

            # --- Apply tags ---
            tags_updated = 0
            if suggested_tags:
                new_content = self._add_tags_to_frontmatter(content, suggested_tags)
                if new_content != content:
                    content = new_content
                    tags_updated += 1

            # --- Opportunistic wikilink ---
            links_added = 0
            linked_content = self._insert_wikilink(content, new_note_title)
            if linked_content != content:
                content = linked_content
                links_added += 1

            # --- Atomic write if anything changed ---
            if content != neighbor_content:
                if self._atomic_write(neighbor_path, content):
                    out["changed"] = True
                    out["links_added"] = links_added
                    out["tags_updated"] = tags_updated
                    self._log_event(
                        "neighbor_evolved",
                        {
                            "neighbor_path": neighbor_path,
                            "new_note_title": new_note_title,
                            "tags_updated": tags_updated,
                            "links_added": links_added,
                        },
                    )
                else:
                    self._log_event(
                        "neighbor_write_failed", {"neighbor_path": neighbor_path}
                    )
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            self._log_error("evolve_neighbor_failed", e, {"path": neighbor_path})
        return out

    # ------------------------------------------------------------------ #
    # Embedding-based tag suggestion (Tier 2: local Ollama, no cloud LLM)
    # ------------------------------------------------------------------ #
    def _embedding_suggest_tags(
        self,
        new_title: str,
        new_content: str,
        neighbor_content: str,
    ) -> list[str]:
        """Suggest tags via embedding similarity — zero generative LLM calls.

        Embeds the new note title+preview and the neighbor content, then
        extracts noun phrases from the neighbor as tag candidates. Only
        suggests tags if there's meaningful semantic overlap (cosine
        similarity > 0.3). Uses the local Ollama embedding model
        (nomic-embed-text), NOT the cloud LLM key.
        """
        if self.vault_indexer is None:
            return []
        try:
            import numpy as _np
            import re as _re
            # Embed the new note: title + first 500 chars.
            new_text = (new_title + " " + new_content[:500]).strip()
            new_emb = self.vault_indexer._get_embedding(new_text)
            if new_emb is None:
                return []
            neighbor_emb = self.vault_indexer._get_embedding(
                neighbor_content[:4000])
            if neighbor_emb is None:
                return []
            new_v = _np.asarray(new_emb, dtype=_np.float32)
            neigh_v = _np.asarray(neighbor_emb, dtype=_np.float32)
            cos_sim = float(_np.dot(new_v, neigh_v) / (
                _np.linalg.norm(new_v) * _np.linalg.norm(neigh_v) + 1e-8))
            if cos_sim < 0.3:
                return []
            # Extract noun phrases from the neighbor (capitalized phrases).
            phrases = _re.findall(
                r"\b([A-Z][a-zA-Z0-9_]+(?:\s+[A-Z][a-zA-Z0-9_]+){0,2})\b",
                neighbor_content)
            stop = {"The", "This", "That", "These", "Those", "It", "They",
                    "We", "You", "He", "She", "There", "Here", "Note", "Notes",
                    "Section", "Chapter", "Figure", "Table", "Example"}
            candidates = [p for p in phrases if p not in stop and len(p) >= 3]
            seen: set[str] = set()
            tags: list[str] = []
            for c in candidates:
                cl = c.lower()
                if cl not in seen:
                    seen.add(cl)
                    tags.append(c)
                if len(tags) >= 3:
                    break
            return tags
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            return []

    # ------------------------------------------------------------------ #
    # LLM tag suggestion
    # ------------------------------------------------------------------ #
    def _llm_suggest_tags(
        self,
        new_title: str,
        new_content: str,
        neighbor_content: str,
    ) -> list[str]:
        """Ask the LLM for 1-3 new tags for the neighbor. Returns [] on failure.

        Uses the SMALL model cartridge when available — tag suggestion is a
        simple structured task (return a JSON array of 1-3 strings) that
        doesn't need the big model's reasoning power. Saves cloud tokens.
        """
        if self.ollama_client is None:
            return []
        neighbor_preview = neighbor_content[: self.MAX_NEIGHBOR_CHARS_FOR_LLM]
        new_preview = new_content[: self.MAX_NEIGHBOR_CHARS_FOR_LLM]
        prompt = (
            f"Given a new note titled '{new_title}' and an existing neighbor note, "
            "suggest 1-3 new tags/keywords to add to the neighbor that capture how "
            "the new note relates to it. Return ONLY a JSON array of strings, no prose.\n\n"
            f"New note title: {new_title}\n"
            f"New note content (excerpt):\n{new_preview}\n\n"
            f"Neighbor note content (excerpt):\n{neighbor_preview}\n"
        )
        messages = [{"role": "user", "content": prompt}]
        # Use the small model for tag suggestion (simple structured task).
        from llm_client import get_small_client_or_big
        _tag_client = get_small_client_or_big()
        resp = _tag_client.chat(messages, temperature=0.3, stream=False)
        text = ""
        try:
            text = resp["message"]["content"]
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            text = str(resp) if isinstance(resp, str) else ""
        if not text:
            return []
        return self._parse_json_tags(text)

    @staticmethod
    def _parse_json_tags(text: str) -> list[str]:
        """Robustly extract a JSON array of strings from an LLM response."""
        # Try direct parse first.
        try:
            val = json.loads(text)
            if isinstance(val, list):
                return [str(x).strip() for x in val if str(x).strip()]
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            pass
        # Find the first JSON array in the text.
        m = re.search(r"\[[\s\S]*?\]", text)
        if m:
            try:
                val = json.loads(m.group(0))
                if isinstance(val, list):
                    return [str(x).strip() for x in val if str(x).strip()]
            except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                pass
        return []

    # ------------------------------------------------------------------ #
    # Frontmatter / tags parsing
    # ------------------------------------------------------------------ #
    def _extract_tags(self, content: str) -> list:
        """
        Parse YAML frontmatter `tags:` field.

        Handles:
            tags: [a, b, c]
            tags:
              - a
              - b
        Returns [] if no tags or no frontmatter.
        """
        try:
            fm = self._get_frontmatter_block(content)
            if fm is None:
                return []
            # inline array form
            m = re.search(r"^tags\s*:\s*\[(.*)\]\s*$", fm, re.MULTILINE)
            if m:
                inner = m.group(1).strip()
                if not inner:
                    return []
                tags = [t.strip().strip("\"'") for t in inner.split(",")]
                return [t for t in tags if t]
            # block list form
            m = re.search(r"^tags\s*:\s*\n((?:\s*-\s+.+\n?)+)", fm, re.MULTILINE)
            if m:
                block = m.group(1)
                tags = re.findall(r"^\s*-\s+(.+?)\s*$", block, re.MULTILINE)
                tags = [t.strip().strip("\"'") for t in tags]
                return [t for t in tags if t]
            # scalar form: tags: a
            m = re.search(r"^tags\s*:\s+(\S+)\s*$", fm, re.MULTILINE)
            if m:
                return [m.group(1).strip().strip("\"'")]
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            self._log_error("extract_tags_failed", e)
        return []

    @staticmethod
    def _get_frontmatter_block(content: str) -> str | None:
        """Return the raw frontmatter text (between the --- fences) or None."""
        if not content.startswith("---"):
            return None
        # find the closing ---
        rest = content[3:]
        # skip a leading newline
        if rest.startswith("\r\n"):
            rest = rest[2:]
        elif rest.startswith("\n"):
            rest = rest[1:]
        # find closing fence
        idx = rest.find("\n---")
        if idx == -1:
            # maybe content is just "---\n... with closing at very end w/o newline
            if rest.rstrip().endswith("---"):
                return rest[: -3].rstrip()
            return None
        return rest[:idx]

    def _add_tags_to_frontmatter(self, content: str, new_tags: list) -> str:
        """
        Merge new tags into existing frontmatter (dedup, case-insensitive).
        If no frontmatter, create one at the top. Preserves the body exactly.
        """
        if not new_tags:
            return content
        new_tags = [t for t in new_tags if t and t.strip()]
        if not new_tags:
            return content

        fm_block = self._get_frontmatter_block(content)
        existing_tags_lower: set[str] = set()
        existing_tags: list[str] = []
        if fm_block is not None:
            existing_tags = self._extract_tags(content)
            existing_tags_lower = {t.lower() for t in existing_tags}

        to_add: list[str] = []
        for t in new_tags:
            if t.lower() not in existing_tags_lower:
                existing_tags_lower.add(t.lower())
                to_add.append(t)
        if not to_add:
            return content

        merged = existing_tags + to_add

        if fm_block is None:
            # create new frontmatter at the very top
            fm_new = self._format_tags_line(merged)
            return f"---\n{fm_new}\n---\n{content}"

        # Has existing frontmatter — replace or insert tags field.
        return self._set_tags_in_frontmatter(content, merged)

    @staticmethod
    def _format_tags_line(tags: list) -> str:
        """Format tags as an inline YAML array line."""
        if not tags:
            return "tags: []"
        # quote if contains spaces or special chars
        parts = []
        for t in tags:
            t = t.strip()
            if re.search(r"[\s,\[\]{}:#\"']", t):
                parts.append(json.dumps(t))
            else:
                parts.append(t)
        return "tags: [" + ", ".join(parts) + "]"

    def _set_tags_in_frontmatter(self, content: str, tags: list) -> str:
        """Set the tags field inside an existing frontmatter block (atomic to body)."""
        # Split into frontmatter / body
        if not content.startswith("---"):
            return content
        rest = content[3:]
        if rest.startswith("\r\n"):
            nl = "\r\n"
            rest = rest[2:]
        elif rest.startswith("\n"):
            nl = "\n"
            rest = rest[1:]
        else:
            nl = "\n"
        idx = rest.find("\n---")
        if idx == -1:
            return content
        fm_text = rest[:idx]
        after = rest[idx + len("\n---") :]
        # after may start with newline(s) — preserve body
        body = after[1:] if after.startswith("\n") else after
        if after.startswith("\r\n"):
            body = after[2:]

        tags_line = self._format_tags_line(tags)

        # Remove existing tags lines (inline or block) then insert new line.
        lines = fm_text.split("\n")
        out_lines: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if re.match(r"^tags\s*:\s*\[.*\]\s*$", line) or re.match(r"^tags\s*:\s+\S+", line):
                # inline/scalar tags — drop it, we'll re-add
                i += 1
                continue
            if re.match(r"^tags\s*:\s*$", line):
                # block list form — skip this line and following list items
                i += 1
                while i < len(lines) and re.match(r"^\s*-\s+", lines[i]):
                    i += 1
                continue
            out_lines.append(line)
            i += 1

        # Append tags line at the end of the frontmatter.
        out_lines.append(tags_line)
        new_fm = "\n".join(out_lines).rstrip()
        return f"---{nl}{new_fm}{nl}---{nl}{body}"

    # ------------------------------------------------------------------ #
    # Wikilink insertion
    # ------------------------------------------------------------------ #
    def _insert_wikilink(self, content: str, target_title: str) -> str:
        """
        If target_title appears as plain text (not already inside [[...]]),
        wrap the first occurrence in [[target_title]]. Don't touch existing
        wikilinks. Case-insensitive match on the plain title.
        """
        if not target_title:
            return content
        target = target_title.strip()
        if not target:
            return content

        # Tokenize-ish: find target as a whole-word-ish run not preceded/followed
        # by '[' or ']' (i.e. not inside a wikilink), not part of a URL fragment.
        # Use a regex with lookarounds.
        # Escape regex special chars in the title.
        pat = re.compile(
            r"(?<![\[/\w])" + re.escape(target) + r"(?![\]/\w])",
            re.IGNORECASE,
        )
        # We must avoid replacing occurrences that sit inside an existing [[...]].
        # Strategy: split content into wikilink spans and non-wikilink spans,
        # only operate on non-wikilink spans.
        return self._replace_outside_wikilinks(content, pat, f"[[{target}]]", max_count=1)

    @staticmethod
    def _replace_outside_wikilinks(
        content: str, pattern: re.Pattern, replacement: str, max_count: int = 1
    ) -> str:
        """Replace pattern matches only in text segments outside [[...]] spans."""
        result_parts: list[str] = []
        i = 0
        count = 0
        while i < len(content):
            # find next wikilink start
            open_idx = content.find("[[", i)
            if open_idx == -1:
                segment = content[i:]
                if count < max_count:
                    new_seg, n = pattern.subn(replacement, segment, count=max_count - count)
                    count += n
                    result_parts.append(new_seg)
                else:
                    result_parts.append(segment)
                break
            # segment before the wikilink
            segment = content[i:open_idx]
            if count < max_count:
                new_seg, n = pattern.subn(replacement, segment, count=max_count - count)
                count += n
                result_parts.append(new_seg)
            else:
                result_parts.append(segment)
            # find wikilink close
            close_idx = content.find("]]", open_idx + 2)
            if close_idx == -1:
                # unterminated — treat rest as literal
                result_parts.append(content[open_idx:])
                break
            result_parts.append(content[open_idx : close_idx + 2])
            i = close_idx + 2
        return "".join(result_parts)

    # ------------------------------------------------------------------ #
    # I/O helpers
    # ------------------------------------------------------------------ #
    def _atomic_write(self, path: str, content: str) -> bool:
        """Write content atomically (temp + os.replace). Returns True on success.

        On Windows, os.replace can fail with PermissionError (WinError 32)
        when another process — typically the vault indexer's file watcher —
        has the target file open for reading. This is a brief race that
        resolves in milliseconds, so we retry a few times before giving up.
        """
        import time as _time
        max_retries = 5
        retry_delay = 0.05  # 50ms — the watcher releases handles fast
        for attempt in range(max_retries):
            try:
                p = Path(path)
                if not p.parent.exists():
                    p.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp_path = tempfile.mkstemp(
                    prefix=p.name + ".tmp_", dir=str(p.parent), suffix=".md", text=True
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                        f.write(content)
                    os.replace(tmp_path, str(p))
                except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                    try:
                        os.unlink(tmp_path)
                    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                        pass
                    raise
                return True
            except PermissionError as e:
                # WinError 32: file in use by another process (the watcher).
                # Retry after a brief sleep; the handle is released quickly.
                if attempt < max_retries - 1:
                    _time.sleep(retry_delay)
                    continue
                self._log_error("atomic_write_failed", e, {"path": path, "retries": max_retries})
                return False
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                self._log_error("atomic_write_failed", e, {"path": path})
                return False
        return False

    def _refresh_indices(self) -> None:
        """Best-effort refresh of graph/indexer before searching."""
        try:
            if hasattr(self.vault_graph, "refresh"):
                self.vault_graph.refresh()
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            self._log_error("graph_refresh_failed", e)
        try:
            if hasattr(self.vault_indexer, "refresh"):
                self.vault_indexer.refresh()
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            self._log_error("indexer_refresh_failed", e)

    # ------------------------------------------------------------------ #
    # Logging helpers
    # ------------------------------------------------------------------ #
    def _log_event(self, event: str, data: dict) -> None:
        try:
            if self.session_logger is not None and hasattr(self.session_logger, "log"):
                self.session_logger.log(event, data)
            else:
                log.info("amem:%s %s", event, data)
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            log.info("amem:%s %s", event, data)

    def _log_error(self, event: str, err: Exception, extra: dict | None = None) -> None:
        try:
            data = {"error": f"{type(err).__name__}: {err}"}
            if extra:
                data.update(extra)
            if self.session_logger is not None and hasattr(self.session_logger, "log"):
                self.session_logger.log(f"error:{event}", data)
            else:
                log.warning("amem:%s %s %s", event, data, err, exc_info=True)
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            log.warning("amem:%s %s", event, err, exc_info=True)


if __name__ == "__main__":
    # Quick smoke test: instantiate with stubs to verify import + tag helpers.
    import sys

    ev = AMemeEvolution(".", None, None, None, None)
    # _extract_tags
    c1 = "---\ntags: [a, b, c]\n---\nbody\n"
    assert ev._extract_tags(c1) == ["a", "b", "c"], ev._extract_tags(c1)
    c2 = "---\ntags:\n  - x\n  - y\n---\nbody\n"
    assert ev._extract_tags(c2) == ["x", "y"], ev._extract_tags(c2)
    c3 = "no fm here"
    assert ev._extract_tags(c3) == []
    # _add_tags_to_frontmatter
    c4 = ev._add_tags_to_frontmatter(c1, ["d", "A"])  # dedup A case-insensitive
    assert "d" in ev._extract_tags(c4)
    assert ev._extract_tags(c4).count("a") == 1
    c5 = ev._add_tags_to_frontmatter(c3, ["newtag"])
    assert ev._extract_tags(c5) == ["newtag"]
    # _insert_wikilink
    c6 = "Some text about Voyager and more Voyager here."
    r6 = ev._insert_wikilink(c6, "Voyager")
    assert "[[Voyager]]" in r6 and r6.count("[[Voyager]]") == 1, r6
    # don't touch existing wikilinks
    c7 = "See [[Voyager]] for details. Also Voyager is cool."
    r7 = ev._insert_wikilink(c7, "Voyager")
    assert r7.count("[[Voyager]]") == 2, r7  # one existing + one new
    print("smoke test OK")
    sys.exit(0)
