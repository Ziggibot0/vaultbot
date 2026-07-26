"""
Curated graph-operation vocabulary for the plan executor.

This module defines the small, fixed set of graph operations that the
autonomous researcher's plan executor calls. Per Anthropic's
Agent-Computer Interaction (ACI) guidance — "more tools don't always
lead to better outcomes" and "if a human engineer can't definitively
say which tool should be used, an AI agent can't be expected to do
better" — this is a deliberately curated, non-overlapping set of ~7 ops
with crisp descriptions.

Design principles (every op here satisfies all four):

  1. Small & non-overlapping — a human can pick the right op unambiguously.
  2. Idempotent — safe to retry; calling twice produces the same vault state.
  3. Verifiable — each op returns a dict a deterministic verifier can check.
  4. Never raises — every op wraps in try/except and returns {"error": ...}
     on failure, so a plan never crashes the executor.

The ops wrap the existing building blocks (vault_graph, vault_indexer,
note_creator, research_engine, ollama_client) without modifying them.
"""

import re
from pathlib import Path
from typing import Any, Optional

from note_creator import NoteCreator
from research_engine import ResearchEngine
from vault_graph import VaultGraph
from vault_indexer import VaultIndexer

try:
    from ollama_client import OllamaClient
except Exception:  # pragma: no cover - ollama_client is optional at import time
    OllamaClient = None  # type: ignore


# --- Regex extractors for `extract` (pure stdlib, no LLM required) --------
_QUOTED_PHRASE_RE = re.compile(r'\"([^"]{2,80})\"')
_TITLECASE_RE = re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b')
# A "key fact" heuristic: sentences containing a digit or a definition cue.
_FACT_CUE_RE = re.compile(
    r'([A-Z][^.!?\n]{15,220}?(?:\b(?:is|are|was|were|means|defined as)\b'
    r'|\d+)[^.!?\n]{0,180}[.!?])'
)

SKILLS_DIR = Path(__file__).resolve().parent / "skills"


class GraphOpRegistry:
    """
    Registry of the curated graph operations.

    Holds an op-name -> callable mapping (``self.ops``) and the Ollama-format
    ``SCHEMAS`` list so the same ops can be exposed to the LLM for direct
    tool-calling. Each op is a bound method returning a dict and never
    raising.
    """

    def __init__(
        self,
        vault_graph: VaultGraph,
        vault_indexer: VaultIndexer,
        note_creator: NoteCreator,
        research_engine: ResearchEngine,
        ollama_client: Optional["OllamaClient"] = None,
        session_logger: Any = None,
    ) -> None:
        self.vault_graph = vault_graph
        self.vault_indexer = vault_indexer
        self.note_creator = note_creator
        self.research_engine = research_engine
        self.ollama_client = ollama_client
        self.session_logger = session_logger

        # op-name -> bound method
        self.ops: dict[str, Any] = {
            "search": self.search,
            "extract": self.extract,
            "link": self.link,
            "synthesize": self.synthesize,
            "create_note": self.create_note,
            "learn_skill": self.learn_skill,
            "verify": self.verify,
        }

    # --- helpers ---------------------------------------------------------
    def _log(
        self,
        method: str,
        inputs: dict[str, Any] | None = None,
        outputs: Any = None,
        error: str | None = None,
    ) -> None:
        if self.session_logger is None:
            return
        try:
            self.session_logger.log_tool_call(
                tool="graph_ops",
                method=method,
                inputs=inputs,
                outputs=outputs,
                error=error,
            )
        except Exception:
            pass

    @staticmethod
    def _slugify(title: str) -> str:
        """Turn a title into a filesystem-safe slug matching vault convention."""
        slug = re.sub(r"[^\w\s-]", "", title).strip().lower()
        slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
        return slug or "untitled"

    def _resolve_note(self, name: str) -> Path | None:
        """Resolve a note name/path to an actual .md file in the vault."""
        try:
            p = Path(name)
            if p.is_absolute() and p.exists():
                return p
            # Search by stem (case-insensitive) via the graph's vault path.
            vault_root = getattr(self.vault_graph, "vault_path", None)
            if vault_root is None:
                return None
            norm = name.strip().lower()
            for cand in Path(vault_root).rglob("*.md"):
                if cand.stem.lower() == norm:
                    return cand
            for cand in Path(vault_root).rglob("*.md"):
                if norm in cand.stem.lower():
                    return cand
        except Exception:
            return None
        return None

    @staticmethod
    def _make_snippet(content: str, max_len: int = 240) -> str:
        text = re.sub(r"\s+", " ", content).strip()
        if len(text) <= max_len:
            return text
        return text[:max_len].rsplit(" ", 1)[0] + "…"

    # --- op: search ------------------------------------------------------
    def search(self, args: dict[str, Any]) -> dict[str, Any]:
        """
        Semantic search over the vault's embedding index.

        Read-only and idempotent. Use this to find existing notes relevant
        to a query BEFORE deciding to research or create a note. Distinct
        from `synthesize` (which hits the web) and `extract` (which reads
        a single note).
        """
        try:
            query = args.get("query")
            if not query or not isinstance(query, str):
                return {"error": "missing or invalid 'query'"}
            k = int(args.get("k", 5))
            raw = self.vault_indexer.search(query, k=k)
            results: list[dict[str, Any]] = []
            for r in raw:
                fp = r.get("file_path", "")
                content = r.get("content", "")
                results.append({
                    "title": Path(fp).stem if fp else "",
                    "path": fp,
                    "snippet": self._make_snippet(content),
                    "score": r.get("score", 0.0),
                })
            out = {"results": results, "count": len(results)}
            self._log("search", {"query": query, "k": k}, out)
            return out
        except Exception as e:
            self._log("search", args, error=str(e))
            return {"error": str(e)}

    # --- op: extract -----------------------------------------------------
    def extract(self, args: dict[str, Any]) -> dict[str, Any]:
        """
        Extract entities + key facts from a note or raw text.

        Read-only and idempotent. Use this to understand what a single note
        contains (the 'who' and 'what'). Distinct from `search` (retrieves
        notes by similarity) and `synthesize` (researches the web).
        Accepts ``{"text": str}`` or ``{"note_path": str}``.
        """
        try:
            text = args.get("text")
            if text is None:
                note_path = args.get("note_path")
                if not note_path:
                    return {"error": "provide 'text' or 'note_path'"}
                resolved = self._resolve_note(note_path)
                if resolved is None:
                    return {"error": f"note not found: {note_path}"}
                text = resolved.read_text(encoding="utf-8", errors="replace")
            if not isinstance(text, str):
                return {"error": "'text' must be a string"}

            entities_set = set()
            for m in _QUOTED_PHRASE_RE.findall(text):
                entities_set.add(m.strip())
            for m in _TITLECASE_RE.findall(text):
                entities_set.add(m.strip())
            # Filter common single-word false positives.
            stop_single = {"The", "This", "That", "These", "Those", "It",
                           "They", "We", "You", "He", "She", "There", "Here"}
            entities = sorted(
                e for e in entities_set
                if e not in stop_single and len(e) >= 2
            )

            key_facts: list[str] = []
            for m in _FACT_CUE_RE.findall(text):
                fact = re.sub(r"\s+", " ", m).strip()
                if 20 <= len(fact) <= 240 and fact not in key_facts:
                    key_facts.append(fact)

            # Optionally enrich with an LLM pass if a client is wired in.
            if self.ollama_client is not None:
                try:
                    prompt = (
                        "Extract the most important entities (proper nouns, "
                        "named concepts) and 3-5 key facts from the text "
                        "below. Respond as JSON: "
                        '{"entities": [...], "key_facts": [...]}.\n\n' + text[:6000]
                    )
                    resp = self.ollama_client.generate(
                        prompt, temperature=0.2, max_tokens=600
                    )
                    if isinstance(resp, dict):
                        raw_llm = resp.get("response", "")
                        # Best-effort JSON pull.
                        start = raw_llm.find("{")
                        end = raw_llm.rfind("}")
                        if start != -1 and end != -1 and end > start:
                            import json
                            parsed = json.loads(raw_llm[start:end + 1])
                            llm_ents = parsed.get("entities", [])
                            llm_facts = parsed.get("key_facts", [])
                            if isinstance(llm_ents, list):
                                for e in llm_ents:
                                    if isinstance(e, str) and e not in entities:
                                        entities.append(e)
                            if isinstance(llm_facts, list):
                                for f in llm_facts:
                                    if isinstance(f, str) and f not in key_facts:
                                        key_facts.append(f)
                except Exception as e:
                    self._log("extract_llm", {"error": str(e)})

            out = {
                "entities": entities,
                "key_facts": key_facts,
                "word_count": len(text.split()),
            }
            self._log("extract", args, out)
            return out
        except Exception as e:
            self._log("extract", args, error=str(e))
            return {"error": str(e)}

    # --- op: link --------------------------------------------------------
    def link(self, args: dict[str, Any]) -> dict[str, Any]:
        """
        Insert a wikilink from one note to another (MERGE semantics).

        Idempotent: if the link already exists, this is a no-op. Distinct
        from `create_note` (which writes note bodies). Use this to wire up
        the graph AFTER notes exist.
        """
        from vault_guard import VaultWriteForbidden, assert_writable
        try:
            source = args.get("source_note")
            target = args.get("target_note")
            if not source or not target:
                return {"error": "require 'source_note' and 'target_note'"}

            src_path = self._resolve_note(source)
            if src_path is None:
                return {"linked": False, "reason": "source does not exist",
                        "source_path": source}
            # Sacred/locked guard: never let the LLM rewrite a date-only
            # journal file or a LOCKED note (link appends to the source).
            try:
                assert_writable(src_path)
            except VaultWriteForbidden as e:
                return {"linked": False, "reason": "write blocked: " + e.reason,
                        "source_path": str(src_path)}
            tgt_path = self._resolve_note(target)
            if tgt_path is None:
                return {"linked": False, "reason": "target does not exist",
                        "source_path": str(src_path),
                        "target_exists": False}

            target_stem = tgt_path.stem
            content = src_path.read_text(encoding="utf-8", errors="replace")
            link_token = f"[[{target_stem}]]"
            # Idempotent: any existing link to the target stem means no-op.
            if re.search(rf"\[\[{re.escape(target_stem)}(?:\|[^\]]+)?\]\]",
                         content):
                return {"linked": False, "reason": "already linked",
                        "source_path": str(src_path),
                        "target_exists": True}

            new_content = content.rstrip() + f"\n\n- {link_token}\n"
            src_path.write_text(new_content, encoding="utf-8")
            # Refresh graph awareness so subsequent ops see the new edge.
            try:
                self.vault_graph.refresh()
            except Exception:
                pass
            out = {"linked": True, "source_path": str(src_path),
                   "target_exists": True}
            self._log("link", args, out)
            return out
        except Exception as e:
            self._log("link", args, error=str(e))
            return {"error": str(e)}

    # --- op: synthesize --------------------------------------------------
    def synthesize(self, args: dict[str, Any]) -> dict[str, Any]:
        """
        Run the research engine on a topic and return the synthesis.

        Read-only with respect to the vault: this does NOT write a note.
        Use this to gather fresh web-grounded facts; pair with
        `create_note` to persist the result. Distinct from `search` (which
        only looks inside the vault).
        """
        try:
            topic = args.get("topic")
            if not topic or not isinstance(topic, str):
                return {"error": "missing or invalid 'topic'"}
            min_sources = int(args.get("min_sources", 3))

            report = self.research_engine.research(topic)
            source_count = report.get("source_count", 0)
            if source_count < min_sources:
                return {
                    "synthesis": report.get("synthesis", ""),
                    "sources": report.get("sources", []),
                    "source_count": source_count,
                    "facts": report.get("synthesis_facts", 0),
                    "warning": f"only {source_count} sources "
                               f"(min_sources={min_sources})",
                }
            out = {
                "synthesis": report.get("synthesis", ""),
                "sources": report.get("sources", []),
                "source_count": source_count,
                "facts": report.get("synthesis_facts", 0),
            }
            self._log("synthesize", {"topic": topic}, out)
            return out
        except Exception as e:
            self._log("synthesize", args, error=str(e))
            return {"error": str(e)}

    # --- op: create_note -------------------------------------------------
    def create_note(self, args: dict[str, Any]) -> dict[str, Any]:
        """
        UPSERT a note by title/slug.

        Idempotent: if a note with the slug exists, APPEND a section only
        if an identical section isn't already present (so calling twice
        with the same body is a no-op the second time). Distinct from
        `link` (which wires edges between existing notes) and
        `synthesize` (which gathers facts without writing).
        """
        try:
            title = args.get("title")
            body = args.get("body")
            if not title or not isinstance(title, str):
                return {"error": "missing or invalid 'title'"}
            if body is None:
                return {"error": "missing 'body'"}
            folder = args.get("folder", "vaultbot/research")
            summary = args.get("summary", "") or ""

            slug = self._slugify(title)
            vault_root = getattr(self.vault_graph, "vault_path", None)
            if vault_root is None:
                return {"error": "vault_path unavailable"}
            note_dir = Path(vault_root) / folder
            note_dir.mkdir(parents=True, exist_ok=True)
            note_path = note_dir / f"{slug}.md"

            # Sacred/locked guard: never let the LLM create or append to a
            # date-only journal file or a LOCKED note.
            from vault_guard import VaultWriteForbidden, assert_writable
            try:
                assert_writable(note_path)
            except VaultWriteForbidden as e:
                return {"error": "write blocked: " + e.reason,
                        "note_path": str(note_path)}

            section_header = f"## {title}"
            section_body = body.strip()
            if summary:
                section_body = f"{summary.strip()}\n\n{section_body}"
            new_section = f"{section_header}\n\n{section_body}\n"

            created = False
            appended = False

            if not note_path.exists():
                content = f"# {title}\n\n{new_section}"
                note_path.write_text(content, encoding="utf-8")
                created = True
            else:
                existing = note_path.read_text(encoding="utf-8", errors="replace")
                # Idempotency: skip if an identical section already present.
                if new_section.strip() in existing:
                    out = {"note_path": str(note_path),
                           "created": False, "appended": False,
                           "reason": "identical section already present"}
                    self._log("create_note", args, out)
                    return out
                updated = existing.rstrip() + "\n\n" + new_section
                note_path.write_text(updated, encoding="utf-8")
                appended = True

            # Keep the index + graph aware of the new/changed note.
            try:
                self.vault_indexer._add_file_to_index(note_path)
            except Exception:
                pass
            try:
                self.vault_graph.refresh()
            except Exception:
                pass

            out = {"note_path": str(note_path), "created": created,
                   "appended": appended}
            self._log("create_note", args, out)
            return out
        except Exception as e:
            self._log("create_note", args, error=str(e))
            return {"error": str(e)}

    # --- op: learn_skill -------------------------------------------------
    def learn_skill(self, args: dict[str, Any]) -> dict[str, Any]:
        """
        Register a reusable procedure as a skill (markdown file under
        ``vaultbot_backend/skills/``).

        Idempotent: same name + description is a no-op. Placeholder op
        for the self-improvement loop; a future executor can re-invoke
        learned skills by name. Distinct from `create_note` (vault notes)
        — skills are backend procedures, not vault knowledge.
        """
        try:
            name = args.get("name")
            description = args.get("description")
            procedure = args.get("procedure")
            if not name or not isinstance(name, str):
                return {"error": "missing or invalid 'name'"}
            if not description or not procedure:
                return {"error": "require 'description' and 'procedure'"}

            SKILLS_DIR.mkdir(parents=True, exist_ok=True)
            slug = self._slugify(name)
            skill_path = SKILLS_DIR / f"{slug}.md"

            header = f"# Skill: {name}\n\n"
            desc_line = f"**Description:** {description}\n\n"
            proc_line = f"**Procedure:**\n\n{procedure.strip()}\n"
            new_content = header + desc_line + proc_line

            if skill_path.exists():
                existing = skill_path.read_text(encoding="utf-8",
                                                 errors="replace")
                # Idempotent on name + description match.
                if (f"**Description:** {description}" in existing
                        and f"# Skill: {name}" in existing):
                    out = {"skill_path": str(skill_path), "created": False,
                           "reason": "skill already registered"}
                    self._log("learn_skill", args, out)
                    return out
                # Update procedure body if description matches but procedure
                # differs (still safe — last write wins, deterministic).
                skill_path.write_text(new_content, encoding="utf-8")
                out = {"skill_path": str(skill_path), "created": False,
                       "reason": "updated existing skill"}
                self._log("learn_skill", args, out)
                return out

            skill_path.write_text(new_content, encoding="utf-8")
            out = {"skill_path": str(skill_path), "created": True}
            self._log("learn_skill", args, out)
            return out
        except Exception as e:
            self._log("learn_skill", args, error=str(e))
            return {"error": str(e)}

    # --- op: verify ------------------------------------------------------
    def verify(self, args: dict[str, Any]) -> dict[str, Any]:
        """
        Deterministic gate: check a condition against the vault.

        Read-only and idempotent. The plan executor uses this to confirm a
        prior op succeeded before proceeding. ``check`` is one of:
        ``exists``, ``min_words:N``, ``has_links``, ``has_sources``.
        Distinct from all other ops — this only READS and reports pass/fail.
        """
        try:
            note_path = args.get("note_path")
            check = args.get("check")
            if not note_path or not check:
                return {"error": "require 'note_path' and 'check'"}

            resolved = self._resolve_note(note_path)
            if resolved is None:
                return {"passed": False, "check": check,
                        "details": f"note not found: {note_path}"}

            content = resolved.read_text(encoding="utf-8", errors="replace")
            word_count = len(content.split())
            wikilinks = re.findall(r"\[\[([^\]|]+)", content)
            source_markers = re.findall(r"\[sources?:", content, re.I)

            passed = False
            details = ""

            if check == "exists":
                passed = resolved.exists()
                details = f"exists={passed}"
            elif check.startswith("min_words:"):
                try:
                    n = int(check.split(":", 1)[1])
                except ValueError:
                    return {"passed": False, "check": check,
                            "details": "invalid min_words value"}
                passed = word_count >= n
                details = f"word_count={word_count} (min {n})"
            elif check == "has_links":
                passed = len(wikilinks) > 0
                details = f"link_count={len(wikilinks)}"
            elif check == "has_sources":
                passed = len(source_markers) > 0
                details = f"source_count={len(source_markers)}"
            else:
                return {"passed": False, "check": check,
                        "details": f"unknown check: {check}"}

            out = {"passed": passed, "check": check, "details": details}
            self._log("verify", args, out)
            return out
        except Exception as e:
            self._log("verify", args, error=str(e))
            return {"error": str(e)}


# --- Ollama-format tool schemas (shared by the LLM tool-caller) ---------
SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": (
                "Semantic search over the vault's existing notes. Use this "
                "to find what the vault already knows about a topic BEFORE "
                "researching or creating a note. Read-only. Do NOT use this "
                "to fetch web facts (use `synthesize`) or to read a single "
                "note in depth (use `extract`)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query.",
                    },
                    "k": {
                        "type": "integer",
                        "description": "Number of results to return (default 5).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract",
            "description": (
                "Extract entities (proper nouns, quoted phrases) and key "
                "facts from a single note or raw text. Read-only. Use this "
                "to understand one note's contents. Do NOT use this to "
                "find notes by similarity (use `search`) or to gather web "
                "facts (use `synthesize`)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Raw text to extract from.",
                    },
                    "note_path": {
                        "type": "string",
                        "description": (
                            "Path or stem of a vault note to read and "
                            "extract from. Use this OR `text`."
                        ),
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "link",
            "description": (
                "Insert a wikilink from one existing note to another "
                "(idempotent merge). Use this to wire up the graph AFTER "
                "both notes exist. Do NOT use this to create notes (use "
                "`create_note`) or to research content (use `synthesize`)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source_note": {
                        "type": "string",
                        "description": "Path or stem of the note adding the link.",
                    },
                    "target_note": {
                        "type": "string",
                        "description": "Path or stem of the note being linked to.",
                    },
                },
                "required": ["source_note", "target_note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "synthesize",
            "description": (
                "Run the web research engine on a topic and return a sourced "
                "synthesis. Does NOT write a note. Use this to gather fresh, "
                "web-grounded facts; pair with `create_note` to persist. Do "
                "NOT use this to search the vault (use `search`)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "The topic to research on the web.",
                    },
                    "min_sources": {
                        "type": "integer",
                        "description": (
                            "Minimum source count for a trustworthy result "
                            "(default 3)."
                        ),
                    },
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_note",
            "description": (
                "UPSERT a vault note by title/slug. If the note exists, "
                "appends a section (idempotent on identical content). Use "
                "this to persist research or synthesis results. Do NOT use "
                "this to add links between notes (use `link`)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Note title (used to derive the slug).",
                    },
                    "body": {
                        "type": "string",
                        "description": "Markdown body / section content.",
                    },
                    "folder": {
                        "type": "string",
                        "description": "Vault subfolder (default 'vaultbot/research').",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Optional one-line summary prepended to the body.",
                    },
                },
                "required": ["title", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "learn_skill",
            "description": (
                "Register a reusable procedure as a skill (markdown file "
                "under the backend skills dir). Idempotent on name + "
                "description. Use this for the self-improvement loop to "
                "record a procedure worth re-running. Distinct from "
                "`create_note` (which writes vault knowledge, not backend "
                "procedures)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Short, unique skill name.",
                    },
                    "description": {
                        "type": "string",
                        "description": "What the skill does and when to use it.",
                    },
                    "procedure": {
                        "type": "string",
                        "description": "The step-by-step procedure to record.",
                    },
                },
                "required": ["name", "description", "procedure"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify",
            "description": (
                "Deterministic gate: check a condition against a note. Use "
                "this to confirm a prior op succeeded before continuing. "
                "Read-only. `check` is one of: 'exists', 'min_words:N', "
                "'has_links', 'has_sources'. Never use this to mutate the "
                "vault."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "note_path": {
                        "type": "string",
                        "description": "Path or stem of the note to check.",
                    },
                    "check": {
                        "type": "string",
                        "description": (
                            "Condition: 'exists', 'min_words:N', 'has_links', "
                            "or 'has_sources'."
                        ),
                    },
                },
                "required": ["note_path", "check"],
            },
        },
    },
]
