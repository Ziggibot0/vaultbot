"""Regression tests for the CI debt ratchet parsing helpers."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_debt_ratchet.py"
_SPEC = importlib.util.spec_from_file_location("check_debt_ratchet", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_CHECK_DEBT_RATCHET = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_CHECK_DEBT_RATCHET)


def test_parse_pyright_report_counts_errors_and_warnings() -> None:
    report = {
        "summary": {
            "errorCount": 4,
            "warningCount": 7,
            "informationCount": 1,
        }
    }
    assert _CHECK_DEBT_RATCHET._parse_pyright_report(report) == (4, 7)


def test_parse_pytest_junit_counts_failures_and_tests(tmp_path: Path) -> None:
    xml_path = tmp_path / "integration.xml"
    xml_path.write_text(
        """<testsuites>
        <testsuite name='integration' tests='12' failures='2' errors='0' skipped='1' />
        </testsuites>""",
        encoding="utf-8",
    )
    assert _CHECK_DEBT_RATCHET._parse_pytest_junit(xml_path) == (2, 12)


def test_ci_python_matrix_matches_pyright_baseline_keys() -> None:
    workflow = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    baseline = json.loads(
        (_REPO_ROOT / ".ci-baseline.json").read_text(encoding="utf-8")
    )
    matrix_match = re.search(r"^\s*python-version:\s*(\[[^\n]+\])\s*$", workflow, re.M)
    assert matrix_match is not None, "CI Python matrix not found"

    matrix_versions = set(json.loads(matrix_match.group(1)))
    baseline_versions = set(baseline["pyright"])
    missing = sorted(matrix_versions - baseline_versions)
    stale = sorted(baseline_versions - matrix_versions)

    assert matrix_versions == baseline_versions, (
        "CI Python matrix and Pyright baseline keys differ: "
        f"missing baselines={missing}, stale baselines={stale}"
    )
