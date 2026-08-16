"""Tests for the ``provides`` frontmatter field — procedure composition
inheritance so the model sees what an orchestrator brings to the table
without reading each sub-procedure.

Covers:
  - procedure_surface_line renders ``composes:`` sub-procedure summaries
  - build_procedure_surface resolves provides via proc_index
  - build_procedure_tree walks the graph recursively with cycle detection
  - vault_indexer embedding surface includes provides names
  - procedure_validator validates the provides field
  - procedure_tracker.get_procedure_index parses provides lists correctly

Offline: no FAISS, no Ollama, no network.
"""

from __future__ import annotations

import sys
import types

# faiss ABI shim so vault_indexer imports without the real faiss package.
if "faiss" not in sys.modules:
    _faiss_stub = types.ModuleType("faiss")
    _faiss_stub.IndexFlatL2 = type("IndexFlatL2", (), {})
    _faiss_stub.read_index = lambda *a, **k: None
    _faiss_stub.write_index = lambda *a, **k: None
    sys.modules["faiss"] = _faiss_stub

from procedure_surface import (
    build_procedure_surface,
    build_procedure_tree,
    procedure_surface_line,
)
from vault_indexer import VaultIndexer

# ---------------------------------------------------------------------------
# procedure_surface_line — provides rendering
# ---------------------------------------------------------------------------


def test_surface_line_no_provides_when_field_absent():
    """No provides field → no composes section."""
    line = procedure_surface_line(
        "Standalone",
        {
            "type": "procedure",
            "description": "does one thing",
            "status": "verified",
        },
    )
    assert "composes" not in line


def test_surface_line_no_provides_without_proc_index():
    """provides present but no proc_index → can't resolve, omit composes."""
    line = procedure_surface_line(
        "Orchestrator",
        {
            "type": "procedure",
            "description": "orchestrates things",
            "status": "verified",
            "provides": ["Child-A", "Child-B"],
        },
    )
    assert "composes" not in line


def test_surface_line_resolves_provides_one_level():
    """provides + proc_index → composes line with child descriptions."""
    proc_index = {
        "Child-A": {
            "path": "x",
            "frontmatter": {
                "type": "procedure",
                "description": "scans for orphans",
                "status": "verified",
            },
        },
        "Child-B": {
            "path": "y",
            "frontmatter": {
                "type": "procedure",
                "description": "analyzes links",
                "status": "verified",
            },
        },
    }
    line = procedure_surface_line(
        "Dream-Pass",
        {
            "type": "procedure",
            "description": "orchestrator",
            "status": "verified",
            "provides": ["Child-A", "Child-B"],
        },
        proc_index,
    )
    assert "composes:" in line
    assert "Child-A — scans for orphans" in line
    assert "Child-B — analyzes links" in line


def test_surface_line_skips_missing_sub_procedures():
    """A provides name not in proc_index is silently skipped."""
    proc_index = {
        "Child-A": {
            "path": "x",
            "frontmatter": {
                "type": "procedure",
                "description": "scans for orphans",
                "status": "verified",
            },
        },
    }
    line = procedure_surface_line(
        "Orchestrator",
        {
            "type": "procedure",
            "description": "does stuff",
            "status": "verified",
            "provides": ["Child-A", "Nonexistent"],
        },
        proc_index,
    )
    assert "Child-A" in line
    assert "Nonexistent" not in line


def test_surface_line_skips_flagged_sub_procedures():
    """Flagged children are skipped — they can't run and would add noise."""
    proc_index = {
        "Child-A": {
            "path": "x",
            "frontmatter": {
                "type": "procedure",
                "description": "works",
                "status": "verified",
            },
        },
        "Child-B": {
            "path": "y",
            "frontmatter": {
                "type": "procedure",
                "description": "broken",
                "status": "flagged",
            },
        },
    }
    line = procedure_surface_line(
        "Orchestrator",
        {
            "type": "procedure",
            "description": "does stuff",
            "status": "verified",
            "provides": ["Child-A", "Child-B"],
        },
        proc_index,
    )
    assert "Child-A" in line
    assert "Child-B" not in line


def test_surface_line_provides_child_without_description():
    """A child with no description still shows its name."""
    proc_index = {
        "Child-A": {
            "path": "x",
            "frontmatter": {"type": "procedure", "status": "verified"},
        },
    }
    line = procedure_surface_line(
        "Orchestrator",
        {
            "type": "procedure",
            "description": "does stuff",
            "status": "verified",
            "provides": ["Child-A"],
        },
        proc_index,
    )
    assert "Child-A" in line


# ---------------------------------------------------------------------------
# build_procedure_surface — integration with proc_index
# ---------------------------------------------------------------------------


def test_build_surface_resolves_provides_via_index():
    """build_procedure_surface passes proc_index to surface_line so
    provides is resolved."""
    results = [{"file_path": "Procedures/Dream-Pass.md", "content": ""}]
    proc_index = {
        "Dream-Pass": {
            "path": "Procedures/Dream-Pass.md",
            "frontmatter": {
                "type": "procedure",
                "description": "orchestrator",
                "status": "verified",
                "provides": ["Dream-Scan", "Dream-Analyze"],
            },
        },
        "Dream-Scan": {
            "path": "Procedures/Dream-Scan.md",
            "frontmatter": {
                "type": "procedure",
                "description": "scans for orphans",
                "status": "verified",
            },
        },
        "Dream-Analyze": {
            "path": "Procedures/Dream-Analyze.md",
            "frontmatter": {
                "type": "procedure",
                "description": "analyzes links",
                "status": "experimental",
            },
        },
    }
    surface = build_procedure_surface(results, proc_index)
    assert "Dream-Pass" in surface
    assert "composes:" in surface
    assert "Dream-Scan — scans for orphans" in surface
    assert "Dream-Analyze — analyzes links" in surface


# ---------------------------------------------------------------------------
# build_procedure_tree — recursive graph walk
# ---------------------------------------------------------------------------


def test_tree_returns_none_for_missing_stem():
    assert build_procedure_tree("Nonexistent", {}) is None


def test_tree_flat_procedure_has_empty_provides():
    proc_index = {
        "Standalone": {
            "path": "x",
            "frontmatter": {
                "type": "procedure",
                "description": "solo",
                "status": "verified",
            },
        },
    }
    tree = build_procedure_tree("Standalone", proc_index)
    assert tree is not None
    assert tree["name"] == "Standalone"
    assert tree["provides"] == []


def test_tree_resolves_one_level():
    proc_index = {
        "Parent": {
            "path": "x",
            "frontmatter": {
                "type": "procedure",
                "description": "parent",
                "status": "verified",
                "provides": ["Child"],
            },
        },
        "Child": {
            "path": "y",
            "frontmatter": {
                "type": "procedure",
                "description": "child",
                "status": "verified",
            },
        },
    }
    tree = build_procedure_tree("Parent", proc_index)
    assert tree["name"] == "Parent"
    assert len(tree["provides"]) == 1
    assert tree["provides"][0]["name"] == "Child"
    assert tree["provides"][0]["description"] == "child"


def test_tree_resolves_recursively():
    """Grandparent → Parent → Child chain resolves to depth."""
    proc_index = {
        "Grandparent": {
            "path": "a",
            "frontmatter": {
                "type": "procedure",
                "description": "gp",
                "status": "verified",
                "provides": ["Parent"],
            },
        },
        "Parent": {
            "path": "b",
            "frontmatter": {
                "type": "procedure",
                "description": "p",
                "status": "verified",
                "provides": ["Child"],
            },
        },
        "Child": {
            "path": "c",
            "frontmatter": {
                "type": "procedure",
                "description": "c",
                "status": "verified",
            },
        },
    }
    tree = build_procedure_tree("Grandparent", proc_index, max_depth=3)
    assert tree["provides"][0]["name"] == "Parent"
    assert tree["provides"][0]["provides"][0]["name"] == "Child"


def test_tree_cycle_detection():
    """A → B → A cycle is detected and doesn't recurse infinitely."""
    proc_index = {
        "A": {
            "path": "a",
            "frontmatter": {
                "type": "procedure",
                "description": "a",
                "status": "verified",
                "provides": ["B"],
            },
        },
        "B": {
            "path": "b",
            "frontmatter": {
                "type": "procedure",
                "description": "b",
                "status": "verified",
                "provides": ["A"],
            },
        },
    }
    tree = build_procedure_tree("A", proc_index, max_depth=5)
    assert tree["name"] == "A"
    child = tree["provides"][0]
    assert child["name"] == "B"
    # B's child A should be marked as cycle, not recursed into.
    cycle_child = child["provides"][0]
    assert cycle_child["name"] == "A"
    assert cycle_child.get("cycle") is True


def test_tree_marks_missing_sub_procedures():
    """A provides name that doesn't exist is marked as missing."""
    proc_index = {
        "Parent": {
            "path": "x",
            "frontmatter": {
                "type": "procedure",
                "description": "p",
                "status": "verified",
                "provides": ["Ghost"],
            },
        },
    }
    tree = build_procedure_tree("Parent", proc_index)
    assert tree["provides"][0]["name"] == "Ghost"
    assert tree["provides"][0].get("missing") is True


def test_tree_respects_max_depth():
    """max_depth=1 resolves children but not grandchildren."""
    proc_index = {
        "GP": {
            "path": "a",
            "frontmatter": {
                "type": "procedure",
                "description": "gp",
                "status": "verified",
                "provides": ["P"],
            },
        },
        "P": {
            "path": "b",
            "frontmatter": {
                "type": "procedure",
                "description": "p",
                "status": "verified",
                "provides": ["C"],
            },
        },
        "C": {
            "path": "c",
            "frontmatter": {
                "type": "procedure",
                "description": "c",
                "status": "verified",
            },
        },
    }
    tree = build_procedure_tree("GP", proc_index, max_depth=1)
    assert tree["provides"][0]["name"] == "P"
    # P's provides should be empty — we stopped at depth 1.
    assert tree["provides"][0]["provides"] == []


# ---------------------------------------------------------------------------
# vault_indexer — embedding surface includes provides
# ---------------------------------------------------------------------------


class _StubOllama:
    def __init__(self):
        self.calls: list[str] = []

    def embeddings(self, text: str) -> list[float]:
        self.calls.append(text)
        h = abs(hash(text)) % 1000
        return [float((h >> i) & 1) for i in range(8)] or [0.0] * 8

    def batch_embeddings(self, texts, max_workers=8):
        return [self.embeddings(t) for t in texts]


def _make_indexer(tmp_path):
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


ORCHESTRATOR_NOTE = """---
type: procedure
status: verified
description: "Biomimetic dream pass orchestrator"
when: "when consolidating memories"
provides:
  - Dream-Scan
  - Dream-Analyze
allowed_tools:
  - run_procedure
---

## Steps

1. ```python
   run_procedure("Dream-Scan")
   ```
"""


def test_orchestrator_embedding_includes_provides_names(tmp_path):
    """The provides sub-procedure names are included in the embedding
    surface so the orchestrator is discoverable by its children's
    capabilities."""
    idx = _make_indexer(tmp_path)
    text = idx._embedding_text_for_note(tmp_path / "Dream-Pass.md", ORCHESTRATOR_NOTE)
    assert "Dream-Pass" in text
    assert "Biomimetic dream pass orchestrator" in text
    assert "Dream-Scan" in text
    assert "Dream-Analyze" in text
    assert "Composes:" in text
    # The code body must NOT be in the embedding text.
    assert "run_procedure" not in text.split("Composes:")[0]


def test_orchestrator_embedding_inline_provides(tmp_path):
    """Inline single-value provides (provides: Dream-Scan) also works."""
    idx = _make_indexer(tmp_path)
    note = (
        "---\ntype: procedure\nstatus: verified\n"
        'description: "orchestrator"\nprovides: Dream-Scan\n'
        "allowed_tools: []\n---\n## Steps\n1. do thing"
    )
    text = idx._embedding_text_for_note(tmp_path / "Orch.md", note)
    assert "Dream-Scan" in text
    assert "Composes:" in text


# ---------------------------------------------------------------------------
# procedure_validator — provides field checks
# ---------------------------------------------------------------------------

from procedure_validator import validate_procedure_text

_VALID_PROC_BODY = """
# Test-Proc

## Steps

1. ```python
   result = {"ok": True}
   ```
"""

_VALID_PROC_WITH_PROVIDES = (
    "---\n"
    "type: procedure\n"
    "status: experimental\n"
    'description: "test proc"\n'
    "when_to_use: testing\n"
    "falsifiable_if: it fails\n"
    "allowed_tools:\n"
    "  - code_read\n"
    "provides:\n"
    "  - Dream-Scan\n"
    "  - Dream-Analyze\n"
    "---\n" + _VALID_PROC_BODY
)


def test_validator_passes_with_valid_provides():
    result = validate_procedure_text(_VALID_PROC_WITH_PROVIDES)
    # provides should not cause any errors
    provides_errors = [e for e in result["errors"] if "provides" in e.lower()]
    assert provides_errors == [], provides_errors


def test_validator_warns_missing_sub_procedure():
    """When a proc_index is provided and a provides name isn't in it,
    the validator warns."""
    proc_index = {"Other-Proc": {"path": "x", "frontmatter": {}}}
    result = validate_procedure_text(_VALID_PROC_WITH_PROVIDES, proc_index)
    provides_warnings = [
        w for w in result["warnings"] if "provides" in w.lower() and "Dream-Scan" in w
    ]
    assert provides_warnings, "Expected warning about missing Dream-Scan"


def test_validator_no_warning_when_sub_procedure_exists():
    """When the proc_index contains the provides name, no warning."""
    proc_index = {
        "Dream-Scan": {"path": "x", "frontmatter": {}},
        "Dream-Analyze": {"path": "y", "frontmatter": {}},
    }
    result = validate_procedure_text(_VALID_PROC_WITH_PROVIDES, proc_index)
    provides_warnings = [
        w for w in result["warnings"] if "provides" in w.lower() and "Dream-Scan" in w
    ]
    assert provides_warnings == []


def test_validator_no_provides_no_warning():
    """A procedure without provides field generates no provides warnings."""
    note = (
        "---\ntype: procedure\nstatus: experimental\n"
        'description: "test"\nwhen_to_use: testing\n'
        "falsifiable_if: fail\nallowed_tools:\n  - code_read\n---\n" + _VALID_PROC_BODY
    )
    result = validate_procedure_text(note)
    provides_warnings = [w for w in result["warnings"] if "provides" in w.lower()]
    assert provides_warnings == []


# ---------------------------------------------------------------------------
# procedure_tracker — provides list parsing in get_procedure_index
# ---------------------------------------------------------------------------

from procedure_tracker import ProcedureTracker


def test_tracker_index_parses_provides_list(tmp_path):
    """The procedure index correctly parses a provides YAML list into a
    Python list — regression test for the empty-value-list bug where
    provides items were mis-attached to the previous scalar key."""
    proc = tmp_path / "Dream-Pass.md"
    proc.write_text(
        "---\n"
        "type: procedure\n"
        "status: verified\n"
        'description: "orchestrator"\n'
        "allowed_tools:\n"
        "  - run_procedure\n"
        "provides:\n"
        "  - Dream-Scan\n"
        "  - Dream-Analyze\n"
        "---\n"
        "# Dream-Pass\n## Steps\n1. do thing\n",
        encoding="utf-8",
    )
    tracker = ProcedureTracker(
        log_path=str(tmp_path / "log.json"),
        vault_path=str(tmp_path),
    )
    index = tracker.get_procedure_index(str(tmp_path))
    assert "Dream-Pass" in index
    fm = index["Dream-Pass"]["frontmatter"]
    assert fm.get("provides") == ["Dream-Scan", "Dream-Analyze"], (
        f"provides was mis-parsed as {fm.get('provides')!r}"
    )
    # The previous scalar key should NOT have swallowed the list items.
    assert fm.get("allowed_tools") == ["run_procedure"]
    assert fm.get("description") == "orchestrator"
