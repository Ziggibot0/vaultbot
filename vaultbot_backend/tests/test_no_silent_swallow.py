"""Regression test: no new silent-swallow except blocks.

AST-scans the backend for ``except Exception`` blocks that return a literal
empty value (``[]``, ``{}``, `""``, ``None``, ``False``) without calling
``notify_problem``, ``log_exception``, ``log(...)``, ``raise``, or printing.
These are "silent swallows" — the masking fallback pattern this project
eradicated. The test fails on any new violation so the pattern can't
creep back in.

This is the automated enforcement of the project's fail-loud law:
  > All code must fail loudly. No fallbacks, no silent degradation.
  > Checking multiple sources is fine, but trying different mechanisms in
  > a row is lazy. If it breaks, it breaks visibly.

Run: pytest tests/test_no_silent_swallow.py -v
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# The backend directory to scan.
_BACKEND_DIR = Path(__file__).resolve().parent.parent

# Files/dirs to exclude from the scan.
_EXCLUDE_DIRS = {
    "__pycache__",
    "vaultbot_index",
    "sessions",
    "partials",
    "checkpoints",
    "trash",
    "tests",  # test files can use broad except for isolation
}

# Strings that indicate the except block IS surfacing the error
# (not silently swallowing). If any of these appear in the except body,
# the block is allowed.
_SURFACING_PATTERNS = {
    "notify_problem",
    "notify_info",
    "log_exception",
    "log(",
    "logger.",
    "print(",
    "raise",
    "_log(",
    "_log_error",
    "_log_tool",
    "_progress",
    "session_logger",
    "# noqa: BLE001",  # explicitly justified
}

# Literal empty returns that indicate silent swallowing.
_EMPTY_RETURNS = {
    "[]",
    "{}",
    '""',
    "None",
    "False",
    "return []",
    "return {}",
    'return ""',
    "return None",
    "return False",
}


def _except_body_is_silent_swallower(handler_body: list[ast.stmt]) -> bool:
    """Check if an except handler body is a silent swallower.

    Returns True when the body returns a literal empty value AND doesn't
    call any surfacing function (notify_problem, log, raise, print), or
    when the only logging is a ``logger.debug`` call (invisible in
    production — issue #245). ``logger.error/warning/exception`` count as
    surfacing.
    """
    # Convert the body to source to check for surfacing patterns.
    body_source = ast.unparse(ast.Module(body=handler_body, type_ignores=[]))

    # A body whose only logging is logger.debug is a silent swallow: debug
    # is invisible in production, so the failure is masked. Require a
    # visible log level (error/warning/exception) or a noqa justification.
    if "logger.debug" in body_source and "noqa" not in body_source:
        # Still allow if it ALSO surfaces via another mechanism.
        _other = body_source.replace("logger.debug", "")
        if not any(p in _other for p in _SURFACING_PATTERNS if p != "logger."):
            return True

    # If any surfacing pattern is present, it's not silent.
    for pattern in _SURFACING_PATTERNS:
        if pattern in body_source:
            return False

    # Check for bare `pass` — this is silent but may be justified with noqa.
    if body_source.strip() == "pass" and "noqa" not in body_source:
        return True

    # Check for return of literal empty values.
    for stmt in handler_body:
        if isinstance(stmt, ast.Return) and stmt.value is not None:
            try:
                ret_src = ast.unparse(stmt.value).strip()
                if ret_src in _EMPTY_RETURNS:
                    return True
            except Exception:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                pass

    return False


def _scan_file(filepath: Path) -> list[tuple[int, str]]:
    """Scan a single .py file for silent-swallow except blocks.

    Returns a list of (line_number, description) tuples for violations.
    """
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []  # skip files with syntax errors

    violations: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            # Only check `except Exception` (broad catches).
            if node.type is None:
                continue  # bare except — different lint rule
            try:
                exc_type = ast.unparse(node.type).strip()
            except Exception:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                continue
            if exc_type != "Exception" and not exc_type.startswith("Exception "):
                continue

            # Check if this handler has a noqa comment in the source.
            # We need to check the source line, not the AST.
            source_lines = source.splitlines()
            if node.lineno <= len(source_lines):
                except_line = source_lines[node.lineno - 1]
                if "noqa" in except_line:
                    continue  # explicitly justified

            if _except_body_is_silent_swallower(node.body):
                violations.append(
                    (
                        node.lineno,
                        "silent swallow: except Exception returns empty "
                        "without logging/raising",
                    )
                )

    return violations


def _iter_backend_files() -> list[Path]:
    """Yield all .py files in the backend directory, excluding tests/cache."""
    files: list[Path] = []
    for root, dirs, filenames in os.walk(_BACKEND_DIR):
        # Prune excluded dirs in-place.
        dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS]
        for fname in filenames:
            if fname.endswith(".py"):
                files.append(Path(root) / fname)
    return files


# ─────────────────────────────────────────────────────────────────────────
# Test
# ─────────────────────────────────────────────────────────────────────────


class TestNoSilentSwallow:
    """Ensure no new silent-swallow except blocks have been introduced.

    This test AST-scans every .py file in the backend (excluding tests)
    for `except Exception` blocks that return a literal empty value
    (``[]``, ``{}``, ``""``, ``None``, ``False``) or bare ``pass``
    without calling any surfacing function (notify_problem, log, raise,
    print) or having a ``# noqa: BLE001`` justification.

    If this test fails, you've introduced a masking fallback. Either:
      1. Surface the error (call notify_problem / log_exception / raise)
      2. Narrow the except to a specific exception type
      3. If genuinely best-effort, add `# noqa: BLE001 — <reason>`
    """

    def test_no_silent_swallow_patterns(self):
        """No except Exception blocks that silently return empty."""
        all_violations: list[str] = []

        for filepath in _iter_backend_files():
            violations = _scan_file(filepath)
            for lineno, desc in violations:
                rel = filepath.relative_to(_BACKEND_DIR)
                all_violations.append(f"  {rel}:{lineno}: {desc}")

        if all_violations:
            pytest.fail(
                f"Found {len(all_violations)} silent-swallow except block(s):\n"
                + "\n".join(all_violations)
                + "\n\nFix: surface the error (notify_problem/log/raise), "
                "narrow the except, or add # noqa: BLE001 — <reason>"
            )

    def test_scanner_runs_without_crashing(self):
        """The scanner itself must not crash on any backend file."""
        for filepath in _iter_backend_files():
            # Should never raise.
            _scan_file(filepath)
