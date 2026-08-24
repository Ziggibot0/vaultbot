#!/usr/bin/env python3
"""Debt ratchet: fail CI if soft-gate debt grows past the committed baseline.

The two remaining soft gates (pyright full, pytest integration) run with
``continue-on-error: true`` so they surface pre-existing debt without
blocking CI. This script enforces the baseline by comparing the current
counts against the committed values in ``.ci-baseline.json``.

The script prefers machine-readable reports (``pyright --outputjson`` and
``pytest --junitxml``) whenever they are available, because they avoid regex
parsing of tool output. When no artifact path is supplied, the script falls
back to invoking the tools directly so it still works locally.

Exit 0 = within baseline. Exit 1 = debt grew (or a tool failed to run).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

# The script lives at vaultbot_backend/scripts/, so the repo root (where
# .ci-baseline.json lives) is two levels up.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BASELINE_PATH = _REPO_ROOT / ".ci-baseline.json"
_BACKEND_DIR = _REPO_ROOT / "vaultbot_backend"


def _load_baseline(path: Path | None = None) -> dict:
    baseline_path = path or _BASELINE_PATH
    if not baseline_path.exists():
        print(
            f"debt-ratchet: baseline file not found: {baseline_path}", file=sys.stderr
        )
        return {}
    return json.loads(baseline_path.read_text(encoding="utf-8"))


def _pyright_version() -> str | None:
    """Return the installed pyright version string or None."""
    result = subprocess.run(
        [sys.executable, "-m", "pyright", "--version"],
        cwd=_BACKEND_DIR.parent,
        capture_output=True,
        text=True,
        timeout=120,
    )
    text = (result.stdout + result.stderr).strip()
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith("pyright "):
            version = line.split()[1].strip()
            return version
    return None


def _parse_pyright_report(report: dict) -> tuple[int, int]:
    summary = report.get("summary", {})
    errors = int(summary.get("errorCount", 0) or 0)
    warnings = int(summary.get("warningCount", 0) or 0)
    return errors, warnings


def _read_pyright_report(path: Path) -> tuple[int, int, str | None]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print(
            f"debt-ratchet: could not read pyright JSON report: {path}",
            file=sys.stderr,
        )
        return (-1, -1, None)
    version = _pyright_version()
    errors, warnings = _parse_pyright_report(report)
    return errors, warnings, version


def _run_pyright() -> tuple[int, int, str | None]:
    """Return (errors, warnings, version) from `pyright --level warning`."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyright",
            "--level",
            "warning",
            "--outputjson",
            "vaultbot_backend/",
        ],
        cwd=_BACKEND_DIR.parent,
        capture_output=True,
        text=True,
        timeout=600,
    )
    output = result.stdout.strip()
    if not output:
        print("debt-ratchet: could not parse pyright JSON", file=sys.stderr)
        print((result.stderr or result.stdout)[-2000:], file=sys.stderr)
        return (-1, -1, None)
    try:
        report = json.loads(output)
    except json.JSONDecodeError:
        print("debt-ratchet: could not parse pyright JSON", file=sys.stderr)
        print(output[-2000:], file=sys.stderr)
        return (-1, -1, None)
    version = _pyright_version()
    return (*_parse_pyright_report(report), version)


def _parse_pytest_junit(path: Path) -> tuple[int, int]:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        print(
            f"debt-ratchet: could not read/parse pytest JUnit XML: {path}",
            file=sys.stderr,
        )
        return (-1, -1)

    # The JUnit root can be either a <testsuite ...> or a <testsuites> wrapper.
    # Some outputs contain multiple <testsuite> nodes, so sum them all.
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        return (0, 0)
    tests = sum(int(s.attrib.get("tests", 0) or 0) for s in suites)
    failures = sum(int(s.attrib.get("failures", 0) or 0) for s in suites)
    return failures, tests


def _run_pytest_integration() -> tuple[int, int]:
    """Return (failures, total) from `pytest -m integration`."""
    env = dict(os.environ)
    env["VAULTBOT_SKIP_LOCK"] = "1"
    env["VAULTBOT_SKIP_WATCHER"] = "1"
    env["VAULT_PATH"] = str(_REPO_ROOT)
    with tempfile.TemporaryDirectory(prefix="vaultbot-ratchet-") as tmp_dir:
        junit_path = Path(tmp_dir) / "pytest-integration.xml"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-m",
                "integration",
                "--tb=no",
                "--junitxml",
                str(junit_path),
            ],
            cwd=_BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=600,
            env=env,
            check=False,
        )
        if not junit_path.exists():
            print("debt-ratchet: could not produce pytest JUnit XML", file=sys.stderr)
            return (-1, -1)
        return _parse_pytest_junit(junit_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=_BASELINE_PATH)
    parser.add_argument(
        "--pyright-json",
        type=Path,
        help="path to pyright --outputjson report",
    )
    parser.add_argument(
        "--pytest-junitxml",
        type=Path,
        help="path to pytest --junitxml report",
    )
    args = parser.parse_args()

    baseline = _load_baseline(args.baseline)
    if not baseline:
        return 1

    failures: list[str] = []

    if args.pyright_json is not None:
        py_errors, py_warnings, py_version = _read_pyright_report(args.pyright_json)
    else:
        py_errors, py_warnings, py_version = _run_pyright()
    py_base = baseline.get("pyright", {})

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

    if args.pytest_junitxml is not None:
        it_failures, it_total = _parse_pytest_junit(args.pytest_junitxml)
    else:
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
