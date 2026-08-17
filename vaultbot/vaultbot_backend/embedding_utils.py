"""Embedding helpers for the vault index.

Extracted from ``vault_indexer.py`` to keep the indexer focused on FAISS
storage and search.  This module owns the pure / nearly-pure embedding-text
and chunking logic:

* ``embedding_text_for_note(file_path, content)`` — returns the text that
  should be *embedded* for a note.  Procedures embed only their
  description / when-to-use / trigger / provides surface (intent-based
  discovery); other notes embed full content.
* ``split_into_chunks(text, chunk_size, overlap)`` — splits long text on
  paragraph boundaries into overlapping chunks.
* ``get_chunked_embedding(text, ollama_client, chunk_size, overlap)`` —
  embeds long text by chunking + averaging via the Ollama client.

``VaultIndexer`` keeps thin delegating ``@staticmethod`` / instance methods
with the original names so existing callers (tests, A-MEM, batch_add_files)
are unaffected.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

# Chunking defaults — kept as module constants so callers don't have to
# pass them unless they want to override.
CHUNK_SIZE = 3000
CHUNK_OVERLAP = 300


def get_chunked_embedding(
    text: str,
    ollama_client,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> np.ndarray:
    """Embed long text by chunking + averaging.

    Splits on paragraph boundaries (\\n\\n) into ~3K-char chunks with
    ~300-char overlap, embeds all chunks in parallel via
    batch_embeddings, and averages the vectors.  Returns a single
    float32 array of the same dimensionality as a single embedding.
    Falls back to a single truncated embedding if anything fails.
    """
    chunks = split_into_chunks(text, chunk_size, overlap)
    if not chunks:
        embedding = ollama_client.embeddings(text[:4000])
        return np.array(embedding, dtype=np.float32)
    if len(chunks) == 1:
        embedding = ollama_client.embeddings(chunks[0][:4000])
        return np.array(embedding, dtype=np.float32)
    # Parallel embed all chunks.
    embs = ollama_client.batch_embeddings(chunks)
    valid = [
        np.array(e, dtype=np.float32) for e in embs if e is not None and len(e) > 0
    ]
    if not valid:
        # All chunks failed — fall back to first 4K.
        embedding = ollama_client.embeddings(text[:4000])
        return np.array(embedding, dtype=np.float32)
    # Average into one vector.
    stacked = np.stack(valid)
    return np.mean(stacked, axis=0).astype(np.float32)


def split_into_chunks(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split text into overlapping chunks on paragraph boundaries.

    Tries to break on \\n\\n (paragraph) or \\n (line) boundaries near the
    target chunk size, so chunks don't split mid-sentence.  Each chunk
    overlaps the previous by `overlap` chars so context isn't lost at
    the seam.
    """
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:])
            break
        # Try to break at a paragraph boundary near `end`.
        boundary = text.rfind("\n\n", end - overlap, end)
        if boundary == -1 or boundary <= start:
            boundary = text.rfind("\n", end - overlap, end)
        if boundary == -1 or boundary <= start:
            boundary = end  # hard cut — no nice boundary nearby
        chunks.append(text[start:boundary])
        start = boundary + 1  # +1 to skip the newline
    # Filter out empty / tiny chunks.
    return [c for c in chunks if len(c.strip()) > 50]


def embedding_text_for_note(file_path: Path, content: str) -> str:
    """Return the text that should be EMBEDDED for a note.

    For ordinary notes this is the full content (current behaviour): a
    note is retrieved when the query semantically matches what the note
    *says*.

    For PROCEDURE notes (``type: procedure``) this returns only the
    *discovery surface* — the title, ``description``, and
    ``when_to_use``/``when`` frontmatter fields — NOT the step body or
    code. The operator's insight: a procedure should be discovered when
    the user's need matches *when to use it*, not when the query happens
    to lexically overlap the procedure's implementation (code blocks,
    tool names, step prose). Embedding the full body meant a procedure
    about "verify Python syntax" would surface for any Python-syntax
    question even when the user didn't want a procedure; and a procedure
    whose steps mention "FAISS" would surface for FAISS questions
    unrelated to the procedure's purpose.

    Embedding the description surface instead means the procedure's
    FAISS vector represents its *capability*, so retrieval matches on
    intent ("I need to check syntax before restart" → Verify-Syntax)
    rather than on incidental content overlap. The full body is only
    loaded at execution time via ``execute_procedure``.

    Falls back to full content if the note has no description
    (procedures without a description can't be retrieved by intent
    anyway, and the validator flags the missing field).
    """
    if not content.startswith("---"):
        return content
    end = content.find("\n---", 3)
    if end == -1:
        return content
    fm_block = content[3:end]
    # Only special-case procedures; everything else embeds full content.
    if "type: procedure" not in fm_block:
        return content
    # Parse the few scalar keys that form the discovery surface.
    # Mirrors the lightweight frontmatter parse in procedure_surface.py
    # / procedure_tracker.py (no YAML dep, no nested mappings).
    description = ""
    when = ""
    provides: list[str] = []
    triggers: list[str] = []
    current_key: str | None = None
    for line in fm_block.split("\n"):
        line = line.rstrip()
        if not line:
            continue
        # List item: "  - value" — collect provides sub-procedure names
        # and trigger phrases (both are list-valued frontmatter fields).
        if line.startswith("  - ") and current_key in ("provides", "trigger"):
            val = line[4:].strip().strip('"').strip("'")
            if val:
                if current_key == "provides":
                    provides.append(val)
                else:
                    triggers.append(val)
            continue
        if line.startswith("  "):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not value:
            current_key = key
            continue
        current_key = key
        if key == "description":
            description = value
        elif key in ("when_to_use", "when"):
            when = value
        elif key == "trigger":
            # Inline single value: ``trigger: some phrase``
            triggers.append(value)
        elif key == "provides":
            # Inline single value: ``provides: Dream-Scan``
            provides.append(value)
    # No description → can't do intent-based retrieval; embed full
    # content as a degraded fallback (validator will have flagged it).
    if not description and not when and not triggers:
        return content
    title = file_path.stem
    surface = f"{title}"
    if description:
        surface += f"\n{description}"
    if triggers:
        # Trigger phrases are individual use-cases (feedback-tuned).
        # One "Use when:" line per phrase so each gets its own embedding
        # line — same rationale as the when_to_use split below.
        for phrase in triggers:
            phrase = phrase.strip().strip('"').strip("'")
            if not phrase:
                continue
            surface += f"\nUse when: {phrase}"
    elif when:
        # Fallback: split when_to_use into individual use-cases so each
        # one gets its own embedding line. Without this, the embedding
        # model averages all use-cases into one vector, diluting each
        # specific use-case. With individual lines, a query matching any
        # single use-case gets high similarity instead of being averaged
        # away.  Pattern: "when X, when Y, when Z, or when W"
        clauses = re.split(r",\s*(?:or\s+)?when\s+", when)
        for clause in clauses:
            clause = clause.strip().rstrip(",").strip('"').strip("'")
            if not clause:
                continue
            surface += f"\nUse when: {clause}"
    # Include sub-procedure names so an orchestrator is discoverable by
    # the capabilities it composes — a query about "scan orphans" should
    # surface Dream-Pass (which composes Dream-Scan), not just Dream-Scan.
    # Only the names are added (not descriptions) to stay compact.
    if provides:
        surface += "\nComposes: " + ", ".join(provides)
    return surface
