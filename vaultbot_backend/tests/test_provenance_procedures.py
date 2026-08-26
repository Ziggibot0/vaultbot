"""Regression tests for provenance-safe procedure routing."""

from pathlib import Path

import pytest
from procedure_compiler import compile_procedure

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TRIAGE_PATH = (
    _REPO_ROOT
    / "myvault"
    / "vaultbot-stuff"
    / "System"
    / "Procedures"
    / "Triage-GitHub-Issues.md"
)


def test_triage_is_read_only_and_does_not_claim_to_measure_ease():
    text = _TRIAGE_PATH.read_text(encoding="utf-8")
    procedure = compile_procedure(str(_TRIAGE_PATH))

    assert procedure is not None
    assert len(procedure.steps) == 1
    assert 'run_procedure("Solve-GitHub-Issue"' not in text
    assert "This ranking does not establish which issue is easiest" in text
