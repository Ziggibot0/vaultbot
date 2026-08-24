"""Unit tests for the pre-flight CI gate in submit_contribution.

Verifies that the gate-failure decision logic correctly identifies which
CI hard gates failed, so submit_contribution refuses to push a PR that
would fail CI. Pure logic — no subprocess, no network.
"""

import pytest

pytestmark = pytest.mark.unit

from custom_tools import submit_contribution as sc


def test_failed_gates_returns_only_fail_and_error():
    gates = {
        "ruff_check": {"status": "pass", "output": ""},
        "ruff_format": {"status": "fail", "output": "Would reformat x.py"},
        "pytest": {"status": "error", "output": "boom"},
        "pyright": {"status": "skipped", "output": ""},
    }
    failed = sc._failed_gates(gates)
    assert set(failed.keys()) == {"ruff_format", "pytest"}


def test_failed_gates_empty_when_all_pass():
    gates = {
        "ruff_check": {"status": "pass", "output": ""},
        "ruff_format": {"status": "pass", "output": ""},
        "pytest": {"status": "pass", "output": ""},
    }
    assert sc._failed_gates(gates) == {}


def test_failed_gates_ignores_skipped():
    gates = {
        "ruff_check": {"status": "skipped", "output": "ruff not found"},
        "pytest": {"status": "pass", "output": ""},
    }
    assert sc._failed_gates(gates) == {}


def test_auto_merge_enabled_true(monkeypatch):
    monkeypatch.setenv("VAULTBOT_AUTO_MERGE", "true")
    assert sc._auto_merge_enabled() is True


def test_auto_merge_enabled_case_insensitive(monkeypatch):
    monkeypatch.setenv("VAULTBOT_AUTO_MERGE", "TRUE")
    assert sc._auto_merge_enabled() is True


def test_auto_merge_disabled_unset(monkeypatch):
    monkeypatch.delenv("VAULTBOT_AUTO_MERGE", raising=False)
    assert sc._auto_merge_enabled() is False


def test_auto_merge_disabled_false(monkeypatch):
    monkeypatch.setenv("VAULTBOT_AUTO_MERGE", "false")
    assert sc._auto_merge_enabled() is False
