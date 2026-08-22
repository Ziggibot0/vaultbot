#!/usr/bin/env python3
"""Debt ratchet: fail CI if soft-gate debt grows past the committed baseline.

The two remaining soft gates (pyright full, pytest integration) run with
``continue-on-error: true`` so they surface pre-existing debt without
blocking CI. That means they show green even when they have failures, and
nothing forces them to become hard gates.

This script is the enforcement mechanism (issue #21): it re-runs each soft
gate, counts the current violations, and compares against the committed
baseline in ``.ci-baseline.json``. If the count *increases*, CI fails —
new debt is blocked while the existing baseline stays green and can be
lowered incrementally as debt is paid down.

Exit 0 = within baseline. Exit 1 = debt grew (or a tool failed to run).

Stdlib only — no dependencies beyond Python 3.11+.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

# The script lives at vaultbot_backend/scripts/, so the repo root (where
# .ci-baseline.json lives) is two levels up.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BASELINE_PATH = _REPO_ROOT / ".ci-baseline.json"
_BACKEND_DIR = _REPO_ROOT / "vaultbot_backend"

_PYRIGHT_SUMMARY_RE = re.compile(r"(\d+) errors?, (\d+) warnings?, (\d+) informations?")
_PYRIGHT_VERSION_RE = re.compile(r"pyright (\d+\.\d+\.\d+)")
_PYTEST_SUMMARY_RE = re.compile(r"(\d+) (?:failed|passed)")


def _load_baseline() -> dict:
    if not _BASELINE_PATH.exists():
        print(
            f"debt-ratchet: baseline file not found: {_BASELINE_PATH}", file=sys.stderr
        )
        return {}
    return json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))


def _run_pyright() -> tuple[int, int, str | None]:
    """Return (errors, warnings, version) from `pyright --level warning`.

    The version is parsed from `pyright --version` so the ratchet can detect
    when the installed pyright differs from the version the baseline was
    measured against. A version bump changes pyright's type-inference counts
    even with zero code changes, so a stale baseline would either false-fail
    ("debt grew") or silently hide real debt. See the version-drift check in
    ``main()``.
    """
    result = subprocess.run(
        ["uv", "run", "pyright", "--level", "warning", "vaultbot_backend/"],
        cwd=_BACKEND_DIR.parent,
        capture_output=True,
        text=True,
        timeout=600,
    )
    # pyright exits 1 when there are errors; that's expected. We only care
    # about the summary line, which is on stdout.
    output = result.stdout + result.stderr
    m = _PYRIGHT_SUMMARY_RE.search(output)
    if not m:
        print("debt-ratchet: could not parse pyright summary", file=sys.stderr)
        print(output[-2000:], file=sys.stderr)
        return (-1, -1, None)
    version = _pyright_version()
    return int(m.group(1)), int(m.group(2)), version


def _pyright_version() -> str | None:
    """Return the installed pyright version string (e.g. "1.1.411"), or None.

    ``python -m pyright --version`` prints a single line like
    "pyright 1.1.411". (The ``pyright`` console script's ``--version`` does
    not emit output on Windows, so we invoke the module form, which is
    cross-platform.) If the version can't be determined (tool missing,
    unexpected output), return None so the caller can decide whether that's
    fatal.
    """
    result = subprocess.run(
        ["uv", "run", "python", "-m", "pyright", "--version"],
        cwd=_BACKEND_DIR.parent,
        capture_output=True,
        text=True,
        timeout=120,
    )
    m = _PYRIGHT_VERSION_RE.search(result.stdout + result.stderr)
    return m.group(1) if m else None


def _run_pytest_integration() -> tuple[int, int]:
    """Return (failures, total) from `pytest -m integration`."""
    import os

    env = dict(os.environ)
    env["VAULTBOT_SKIP_LOCK"] = "1"
    env["VAULTBOT_SKIP_WATCHER"] = "1"
    env["VAULT_PATH"] = str(_REPO_ROOT)
    result = subprocess.run(
        ["uv", "run", "pytest", "-m", "integration", "--tb=no"],
        cwd=_BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=600,
        env=env,
    )
    output = result.stdout + result.stderr
    # Parse the summary line like "25 passed, 647 deselected" or
    # "1 failed, 24 passed, 647 deselected". The summary is the last line
    # containing a "N passed"/"N failed" token.
    summary = [
        ln for ln in output.splitlines() if re.search(r"\d+ (failed|passed)", ln)
    ]
    if not summary:
        print("debt-ratchet: could not parse pytest summary", file=sys.stderr)
        print(output[-2000:], file=sys.stderr)
        return (-1, -1)
    line = summary[-1]
    failed = 0
    total = 0
    for m in re.finditer(r"(\d+) (failed|passed)", line):
        n = int(m.group(1))
        total += n
        if m.group(2) == "failed":
            failed = n
    return failed, total


def main() -> int:
    baseline = _load_baseline()
    if not baseline:
        return 1

    failures: list[str] = []

    # Pyright ratchet. The baseline is keyed by Python version because
    # pyright's type-inference count differs slightly between 3.11 and 3.12
    # stdlib stubs (e.g. 454 vs 456 errors).
    py_errors, py_warnings, py_version = _run_pyright()
    py_base = baseline.get("pyright", {})

    # Version-drift guard: the baseline's counts are only meaningful for the
    # pyright version they were measured against. A pyright bump (via uv.lock
    # re-resolution or a manual version change) shifts the counts even with
    # zero code changes, so a stale baseline would either false-fail ("debt
    # grew") or silently hide real debt. Fail loudly and tell the author to
    # re-measure and refresh the baseline in the same PR.
    recorded_version = baseline.get("pyright_version")
    if recorded_version is None:
        failures.append(
            "pyright: baseline is missing 'pyright_version' — add the version "
            "from `pyright --version` to .ci-baseline.json"
        )
    elif py_version is None:
        failures.append(
            "pyright: could not determine installed version — is pyright "
            "installed in the dev environment?"
        )
    elif py_version != recorded_version:
        failures.append(
            f"pyright version drift: installed {py_version} != baseline "
            f"{recorded_version}. Re-measure the counts and update "
            f"'pyright_version' (and the per-version counts) in "
            f".ci-baseline.json in the same PR."
        )

    if py_errors < 0:
        failures.append("pyright: could not determine count")
    else:
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
        ver_base = py_base.get(py_ver)
        if ver_base is None:
            # Fall back to a flat {errors, warnings} shape for backward compat.
            ver_base = py_base if "errors" in py_base else {}
        base_errors = ver_base.get("errors", 0)
        base_warnings = ver_base.get("warnings", 0)
        if py_errors > base_errors:
            failures.append(
                f"pyright errors grew: {py_errors} > baseline {base_errors}"
            )
        if py_warnings > base_warnings:
            failures.append(
                f"pyright warnings grew: {py_warnings} > baseline {base_warnings}"
            )
        print(
            f"debt-ratchet: pyright {py_errors} errors / {py_warnings} warnings "
            f"(baseline {base_errors} / {base_warnings}, py{py_ver}, "
            f"pyright {py_version})"
        )

    # Pytest integration ratchet.
    it_failures, it_total = _run_pytest_integration()
    it_base = baseline.get("pytest_integration", {})
    if it_failures < 0:
        failures.append("pytest integration: could not determine count")
    else:
        base_failures = it_base.get("failures", 0)
        if it_failures > base_failures:
            failures.append(
                f"integration test failures grew: {it_failures} > "
                f"baseline {base_failures}"
            )
        print(
            f"debt-ratchet: pytest integration {it_failures} failures / "
            f"{it_total} tests (baseline {base_failures} failures)"
        )

    if failures:
        print("\nDEBT RATCHET FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        print(
            "\nTo pay down debt, fix the violations and lower the baseline in "
            ".ci-baseline.json. To accept new debt (discouraged), raise the "
            "baseline in the same PR.",
            file=sys.stderr,
        )
        return 1

    print("debt-ratchet: within baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
