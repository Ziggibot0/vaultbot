import json
import re
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from vault_graph import WIKILINK_RE, VaultGraph


class VaultMaintenance:
    """
    Continuous self-cleaning for a vaultbot-managed vault.

    Rules:
    1. Generated notes live under vaultbot/chat/ and vaultbot/research/.
    2. Chat notes on the same topic are merged into one running log.
    3. Orphan generated notes (no wikilinks in, no wikilinks out, empty body) are removed.
    4. Near-duplicate generated notes are merged.
    """

    def __init__(self, vault_path: str, session_logger=None,
                 similarity_threshold: float = 0.85):
        self.vault_path = Path(vault_path).resolve()
        self.session_logger = session_logger
        self.similarity_threshold = similarity_threshold
        self.bot_dir = self.vault_path / "vaultbot"
        self.chat_dir = self.bot_dir / "chat"
        self.research_dir = self.bot_dir / "research"
        self.log_file = self.bot_dir / "maintenance.log"
        self._ensure_dirs()

    def _ensure_dirs(self):
        self.bot_dir.mkdir(exist_ok=True)
        self.chat_dir.mkdir(exist_ok=True)
        self.research_dir.mkdir(exist_ok=True)

    def _log(self, action: str, details: dict[str, Any]):
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "action": action,
            "details": details,
        }
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
        except Exception:
            pass
        if self.session_logger is not None:
            self.session_logger.log_tool_call(
                tool="vault_maintenance", method=action,
                inputs=details.get("inputs"), outputs=details.get("outputs"),
                error=details.get("error"),
            )

    def _is_generated(self, path: Path) -> bool:
        try:
            return self.bot_dir in path.resolve().parents
        except Exception:
            return False

    def _body_text(self, content: str) -> str:
        """Strip headings and wikilinks to compare semantic payload."""
        text = re.sub(r"#+\s*", "", content)
        text = WIKILINK_RE.sub(lambda m: m.group(1).split("|")[0].strip(), text)
        text = re.sub(r"\s+", " ", text).strip().lower()
        return text

    def _similarity(self, a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a, b).ratio()

    def merge_chat_note(self, topic: str, new_entry: str) -> Path:
        """
        Append a new chat exchange to an existing chat note for this topic,
        or create one if it doesn't exist.
        """
        from vault_guard import VaultWriteForbidden, assert_writable
        safe_topic = self._safe_filename(topic)
        note_path = self.chat_dir / f"{safe_topic}.md"
        # Sacred/locked guard: never let the LLM touch a date-only journal
        # file or a LOCKED note. These writes are LLM-driven (chat logging),
        # so the guard applies.
        try:
            assert_writable(note_path)
        except VaultWriteForbidden as e:
            self._log("chat_write_blocked", {
                "file_path": str(note_path), "reason": e.reason})
            raise

        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        section = f"\n\n## {now}\n\n{new_entry}"

        if note_path.exists():
            try:
                existing = note_path.read_text(encoding="utf-8")
                content = existing.rstrip() + section
                self._log("chat_append", {
                    "file_path": str(note_path),
                    "inputs": {"topic": topic},
                })
            except Exception as e:
                self._log("chat_append", {
                    "file_path": str(note_path),
                    "error": str(e),
                })
                content = f"# {topic}{section}"
        else:
            content = f"# {topic}{section}"
            self._log("chat_create", {
                "file_path": str(note_path),
                "inputs": {"topic": topic},
            })

        note_path.write_text(content, encoding="utf-8")
        return note_path

    def create_research_note(self, topic: str, summary: str, research_content: str,
                             links: list[str]) -> Path:
        """Create a research note, replacing an older near-duplicate if found."""
        from vault_guard import VaultWriteForbidden, assert_writable
        safe_topic = self._safe_filename(topic)
        note_path = self.research_dir / f"{safe_topic}.md"
        # Sacred/locked guard: the autonomous researcher + research_tool both
        # flow through here; never let them touch a date-only journal file or
        # a LOCKED note.
        try:
            assert_writable(note_path)
        except VaultWriteForbidden as e:
            self._log("research_write_blocked", {
                "file_path": str(note_path), "reason": e.reason})
            raise

        # If a very similar research note exists, replace it with fresher info
        new_payload = self._body_text(f"{topic} {summary} {research_content}")
        for existing in self.research_dir.glob("*.md"):
            old = self._body_text(existing.read_text(encoding="utf-8"))
            if self._similarity(new_payload, old) >= self.similarity_threshold:
                self._log("research_replace", {
                    "old_path": str(existing),
                    "new_path": str(note_path),
                    "similarity": self._similarity(new_payload, old),
                })
                if existing.resolve() != note_path.resolve():
                    try:
                        existing.unlink()
                    except Exception as e:
                        self._log("research_replace", {"error": str(e)})
                break

        content_lines = [
            f"# {topic}",
            "",
            "## Summary",
            summary,
            "",
            "## Research Notes",
            research_content,
            "",
            "## Related Notes",
        ]
        if links:
            content_lines.extend([f"- {link}" for link in links])
        else:
            content_lines.append("*No related notes found.*")

        content = "\n".join(content_lines)
        note_path.write_text(content, encoding="utf-8")
        self._log("research_create", {"file_path": str(note_path), "topic": topic})
        return note_path

    def run_cleanup(self, graph: VaultGraph) -> dict[str, Any]:
        """
        Remove orphan generated notes and merge near-duplicate generated notes.
        Returns a report.
        """
        removed = []
        merged = []

        # 1. Remove orphans under vaultbot/
        for path in list(self.bot_dir.rglob("*.md")):
            content = path.read_text(encoding="utf-8")
            links = WIKILINK_RE.findall(content)
            body = self._body_text(content)
            name = path.stem
            norm = name.lower()

            has_backlinks = norm in graph.backlinks and len(graph.backlinks[norm]) > 0
            has_outgoing = len(links) > 0
            is_empty = len(body) < 80

            if is_empty and not has_backlinks:
                try:
                    path.unlink()
                    removed.append(str(path))
                    self._log("remove_orphan", {"file_path": str(path)})
                except Exception as e:
                    self._log("remove_orphan", {"file_path": str(path), "error": str(e)})

        # 2. Merge near-duplicate generated notes (within same folder)
        for folder in (self.chat_dir, self.research_dir):
            files = sorted(folder.glob("*.md"))
            skip: set[Path] = set()
            for i, a in enumerate(files):
                if a in skip:
                    continue
                a_text = a.read_text(encoding="utf-8")
                a_body = self._body_text(a_text)
                duplicates = [a]
                for b in files[i + 1:]:
                    if b in skip:
                        continue
                    b_body = self._body_text(b.read_text(encoding="utf-8"))
                    if self._similarity(a_body, b_body) >= self.similarity_threshold:
                        duplicates.append(b)
                        skip.add(b)
                if len(duplicates) > 1:
                    merged_text = self._merge_note_contents(duplicates)
                    keeper = duplicates[0]
                    keeper.write_text(merged_text, encoding="utf-8")
                    for dup in duplicates[1:]:
                        try:
                            dup.unlink()
                            merged.append(str(dup))
                        except Exception as e:
                            self._log("merge_duplicates", {"file_path": str(dup), "error": str(e)})
                    self._log("merge_duplicates", {
                        "kept": str(keeper),
                        "removed": [str(d) for d in duplicates[1:]],
                    })

        return {"removed": removed, "merged": merged}

    def _merge_note_contents(self, paths: list[Path]) -> str:
        """Combine duplicate notes, preserving oldest date sections when present."""
        blocks = []
        seen = set()
        for p in paths:
            text = p.read_text(encoding="utf-8")
            if text not in seen:
                seen.add(text)
                blocks.append(text)
        return "\n\n---\n\n".join(blocks)

    @staticmethod
    def _safe_filename(topic: str) -> str:
        safe = re.sub(r"[^\w\s-]", "", topic).strip()
        safe = re.sub(r"[-\s]+", "-", safe)
        return safe[:80] or "note"
