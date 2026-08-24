"""Regression tests for the CI debt ratchet parsing helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

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
