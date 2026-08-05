"""Tests that procedures are embedded by their description/when-to-use
surface, not their full body content.

This is the intent-based discovery fix: a procedure should be retrieved
when the query matches *when to use it*, not when it lexically overlaps
the procedure's implementation (code blocks, tool names, step prose).
"""

from vault_indexer import VaultIndexer


class _StubOllama:
    """Returns a deterministic vector per unique text so tests can assert
    WHICH text was embedded without a real Ollama call."""

    def __init__(self):
        self.calls: list[str] = []
        self._dim = 8

    def embeddings(self, text: str) -> list[float]:
        self.calls.append(text)
        # Deterministic vector from text hash so different texts -> different
        # vectors, same text -> same vector.
        h = abs(hash(text)) % 1000
        return [float((h >> i) & 1) for i in range(self._dim)] or [0.0] * self._dim

    def batch_embeddings(self, texts, max_workers=8):
        return [self.embeddings(t) for t in texts]


def _make_indexer(tmp_path, monkeypatch):
    idx = VaultIndexer.__new__(VaultIndexer)
    idx.vault_path = tmp_path
    idx.index_path = tmp_path / "idx"
    idx.index_path.mkdir(exist_ok=True)
    idx.index_file = idx.index_path / "index.faiss"
    idx.metadata_file = idx.index_path / "metadata.pkl"
    idx.timestamp_file = idx.index_path / "timestamps.json"
    idx.ollama_client = _StubOllama()
    idx.dimension = None
    idx.index = None
    idx._metadata = {}
    idx._path_to_id = {}
    idx._next_id = 0
    idx.timestamps = {}
    idx.preview_chars = 2000
    idx._needs_full_rebuild = False
    idx.session_logger = None
    return idx


PROCEDURE_NOTE = """---
type: procedure
status: experimental
description: "Verify Python file syntax before restarting backend."
when: "Before restarting the backend after editing Python files"
allowed_tools: []
---

## Steps

1. ```python
   import py_compile
   py_compile.compile("chat_handler.py", doraise=True)
   ```

2. [llm: report whether the file compiled cleanly]
"""

PLAIN_NOTE = """---
type: note
tags: [python]
---

# Some Note

This note talks about py_compile and chat_handler.py syntax errors.
"""


def test_procedure_embeds_description_surface_not_body(tmp_path, monkeypatch):
    """A procedure's embedding text should contain the description and
    when-to-use, NOT the code body or tool names."""
    idx = _make_indexer(tmp_path, monkeypatch)
    proc = tmp_path / "Verify-Syntax.md"
    proc.write_text(PROCEDURE_NOTE, encoding="utf-8")

    text = idx._embedding_text_for_note(proc, PROCEDURE_NOTE)
    assert "Verify Python file syntax before restarting backend." in text
    assert "Before restarting the backend" in text
    # The code body must NOT be in the embedding text.
    assert "py_compile" not in text
    assert "chat_handler.py" not in text
    assert "import py_compile" not in text


def test_plain_note_embeds_full_content(tmp_path, monkeypatch):
    """Non-procedure notes embed their full content (unchanged behaviour)."""
    idx = _make_indexer(tmp_path, monkeypatch)
    note = tmp_path / "Plain.md"
    note.write_text(PLAIN_NOTE, encoding="utf-8")

    text = idx._embedding_text_for_note(note, PLAIN_NOTE)
    assert "py_compile" in text  # full content
    assert "chat_handler.py" in text


def test_procedure_without_description_falls_back_to_content(tmp_path, monkeypatch):
    """A procedure missing both description and when_to_use can't do
    intent-based retrieval — fall back to full content (validator flags it)."""
    idx = _make_indexer(tmp_path, monkeypatch)
    body = "---\ntype: procedure\nstatus: experimental\n---\n## Steps\n1. do thing"
    proc = tmp_path / "NoDesc.md"
    proc.write_text(body, encoding="utf-8")

    text = idx._embedding_text_for_note(proc, body)
    assert "do thing" in text  # full content fallback


def test_procedure_with_only_when(tmp_path, monkeypatch):
    """A procedure with `when` but no `description` still embeds the surface."""
    idx = _make_indexer(tmp_path, monkeypatch)
    body = (
        "---\ntype: procedure\nwhen: \"when auditing capabilities\"\n"
        "allowed_tools: []\n---\n## Steps\n1. do thing"
    )
    proc = tmp_path / "Cap.md"
    proc.write_text(body, encoding="utf-8")

    text = idx._embedding_text_for_note(proc, body)
    assert "when auditing capabilities" in text
    assert "do thing" not in text


def test_add_file_uses_surface_for_procedure(tmp_path, monkeypatch):
    """End-to-end: _add_file_to_index embeds the surface, not the body,
    but still caches the full content as the preview."""
    idx = _make_indexer(tmp_path, monkeypatch)
    proc = tmp_path / "Verify-Syntax.md"
    proc.write_text(PROCEDURE_NOTE, encoding="utf-8")

    idx._add_file_to_index(proc)
    # The stub recorded exactly one embedding call, and it was the surface.
    assert len(idx.ollama_client.calls) == 1
    embedded = idx.ollama_client.calls[0]
    assert "Verify Python file syntax" in embedded
    assert "py_compile" not in embedded
    # Full content is cached as the preview.
    meta = next(iter(idx._metadata.values()))
    assert "py_compile" in meta["content_preview"]


def test_batch_add_uses_surface_for_procedure(tmp_path, monkeypatch):
    """batch_add_files also embeds the surface for procedures and full
    content for plain notes."""
    idx = _make_indexer(tmp_path, monkeypatch)
    proc = tmp_path / "Verify-Syntax.md"
    proc.write_text(PROCEDURE_NOTE, encoding="utf-8")
    plain = tmp_path / "Plain.md"
    plain.write_text(PLAIN_NOTE, encoding="utf-8")

    idx.batch_add_files([str(proc), str(plain)])
    calls = idx.ollama_client.calls
    # The procedure call should NOT contain the code body.
    proc_call = [c for c in calls if "Verify Python file syntax" in c]
    assert proc_call, "procedure surface was not embedded"
    assert "py_compile" not in proc_call[0]
    # The plain note call should contain its body.
    plain_call = [c for c in calls if "py_compile" in c]
    assert plain_call, "plain note full content was not embedded"