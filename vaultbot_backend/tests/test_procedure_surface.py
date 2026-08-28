"""Tests for the Procedure Discovery Service (procedure_surface.py) and the
status-aware retrieval boost / execution gate.

Offline: no FAISS, no Ollama, no network. The surface is built from stub
FUSED results; the boost is checked via the FusedRetriever rerank constants.

Leaf-module imports only — `import main` is hard-fenced by conftest.py.
"""

from __future__ import annotations

import sys
import types

import pytest

# faiss ABI shim (same as test_fused_retrieval.py) so fused_retrieval imports.
if "faiss" not in sys.modules:

    class _StubIndexFlatL2:
        def __init__(self, dim: int = 4, *args, **kwargs):
            self.d = dim
            self.ntotal = 0

    _faiss_stub = types.ModuleType("faiss")
    _faiss_stub.IndexFlatL2 = _StubIndexFlatL2
    _faiss_stub.read_index = lambda *a, **k: None
    _faiss_stub.write_index = lambda *a, **k: None
    sys.modules["faiss"] = _faiss_stub

from procedure_surface import (
    build_procedure_surface,
    procedure_surface_line,
    status_allows_execution,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# status_allows_execution — the extra-safe gate
# ---------------------------------------------------------------------------
def test_gate_allows_verified_clean():
    allowed, reason = status_allows_execution("verified")
    assert allowed is True
    assert reason == "verified"


def test_gate_allows_experimental_with_caution():
    allowed, reason = status_allows_execution("experimental")
    assert allowed is True
    assert reason == "experimental"


def test_gate_treats_unknown_status_as_experimental():
    allowed, reason = status_allows_execution("")
    assert allowed is True
    assert reason == "experimental"


def test_gate_blocks_flagged():
    allowed, reason = status_allows_execution("flagged")
    assert allowed is False
    assert "FLAGGED" in reason


# ---------------------------------------------------------------------------
# procedure_surface_line — compact one-line rendering
# ---------------------------------------------------------------------------
def test_surface_line_includes_description_and_status():
    line = procedure_surface_line(
        "Verify-Claims",
        {
            "type": "procedure",
            "description": "check a note's claims against its sources",
            "status": "verified",
        },
    )
    assert "Verify-Claims" in line
    assert "check a note's claims" in line
    assert "[verified]" in line


def test_surface_line_marks_experimental():
    line = procedure_surface_line(
        "Dream-Pass",
        {
            "type": "procedure",
            "description": "consolidate logs",
            "status": "experimental",
        },
    )
    assert "⚠ experimental" in line


def test_surface_line_marks_flagged_do_not_use():
    line = procedure_surface_line(
        "Stale-Proc",
        {
            "type": "procedure",
            "description": "old",
            "status": "flagged",
        },
    )
    assert "⛔ FLAGGED" in line
    assert "do not use" in line


def test_surface_line_includes_when_to_use():
    line = procedure_surface_line(
        "Ingest",
        {
            "type": "procedure",
            "description": "ingest a PDF",
            "when_to_use": "a new textbook is dropped in learningMaterial",
            "status": "verified",
        },
    )
    assert "use when:" in line


# ---------------------------------------------------------------------------
# build_procedure_surface — scan FUSED results, emit compact block
# ---------------------------------------------------------------------------
def _proc_result(path, text):
    return {"file_path": path, "content": text, "score": 0.9}


def test_surface_empty_when_no_procedures():
    results = [
        {
            "file_path": "07-Research/some-note.md",
            "content": "# Just a note\nNo frontmatter.",
        }
    ]
    assert build_procedure_surface(results) == ""


def test_surface_built_from_result_content():
    proc_text = (
        "---\n"
        "type: procedure\n"
        "description: verify claims in a research note\n"
        "status: verified\n"
        "---\n"
        "# Verify-Claims\n\n## Steps\n1. do stuff\n"
    )
    results = [_proc_result("02-Procedures/Verify-Claims.md", proc_text)]
    surface = build_procedure_surface(results)
    assert "RELEVANT PROCEDURES" in surface
    assert "Verify-Claims" in surface
    assert "execute_procedure" in surface
    # The full body must NOT be in the surface — only the one-line description.
    assert "## Steps" not in surface
    assert "do stuff" not in surface


def test_surface_uses_proc_index_frontmatter_when_provided():
    # When the tracker index is provided, its frontmatter wins (richer).
    results = [{"file_path": "02-Procedures/Dream-Pass.md", "content": ""}]
    proc_index = {
        "Dream-Pass": {
            "path": "02-Procedures/Dream-Pass.md",
            "frontmatter": {
                "type": "procedure",
                "description": "consolidate episodic logs into semantic knowledge",
                "status": "experimental",
            },
        }
    }
    surface = build_procedure_surface(results, proc_index)
    assert "Dream-Pass" in surface
    assert "semantic knowledge" in surface
    assert "⚠ experimental" in surface


def test_surface_dedups_repeat_procedures():
    proc_text = "---\ntype: procedure\ndescription: x\nstatus: verified\n---\n# P\n"
    results = [
        _proc_result("02-Procedures/P.md", proc_text),
        _proc_result("02-Procedures/P.md", proc_text),
    ]
    surface = build_procedure_surface(results)
    assert surface.count("- P ") == 1
