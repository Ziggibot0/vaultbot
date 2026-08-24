"""Regression test: no new hardcoded lexical-heuristic routers.

AST-scans the backend for if/elif chains that decide BETWEEN MULTIPLE
DIFFERENT ACTIONS (calling different functions, or returning different tool/
procedure identifiers) purely by regex or literal keyword matching against
free-text (e.g. ``re.search(r"...", user_message)`` or
``"keyword" in msg.lower()``). This is the "bespoke heuristic for behavior
policy" anti-pattern this project rejects in favor of deterministic contracts
and scored retrieval/ranking (see ``chat_preflight.deterministic_procedure_hint``
for the sanctioned alternative: rank by an already-computed FUSED score
instead of literal string matching).

Rule of thumb this enforces:
  > No bespoke heuristics for behavior policy. Assume language drift and
  > paraphrase diversity; prefer deterministic contracts, scored
  > retrieval/rubrics, and tests over keyword hacks.

A single regex used for parsing/extraction (JSON cleanup, filename patterns,
frontmatter, etc.) is FINE and not what this test looks for. What it flags
is an if/elif chain of 3+ branches, each gated on a regex/keyword test
against the same free-text variable, that dispatch to 2+ distinct actions
(different function calls or different literal return values). That shape is
a hand-rolled keyword router — the kind of thing that quietly breaks on any
paraphrase the author didn't think of.

Justify an intentional exception with ``# noqa: LEXICAL-ROUTE — <reason>`` on
the ``if`` line.

Run: pytest tests/test_no_lexical_routing_heuristics.py -v
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_BACKEND_DIR = Path(__file__).resolve().parent.parent

_EXCLUDE_DIRS = {
    "__pycache__",
    "vaultbot_index",
    "sessions",
    "partials",
    "checkpoints",
    "trash",
    "tests",
}

# Minimum chain length (if + elifs) to be considered a "router" rather than
# a one-off special case.
_MIN_CHAIN_LEN = 3

_REGEX_MATCH_FUNCS = {"search", "match", "fullmatch", "findall"}


def _is_lexical_call(node: ast.expr) -> bool:
    """True if node is a call to re.search/match/fullmatch/findall."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    # re.search(...) or compiled_pattern.search(...)
    return isinstance(func, ast.Attribute) and func.attr in _REGEX_MATCH_FUNCS


def _is_lexical_membership(node: ast.expr) -> bool:
    """True if node is `"literal" in text` / `text in ("a", "b")` style."""
    if not isinstance(node, ast.Compare):
        return False
    ops = node.ops
    if not all(isinstance(op, (ast.In, ast.NotIn)) for op in ops):
        return False
    # At least one side must be a string/collection-of-strings literal —
    # otherwise this is generic membership testing (e.g. `x in known_ids`,
    # a legitimate deterministic-contract check against an explicit set).
    candidates = [node.left, *node.comparators]
    for c in candidates:
        if isinstance(c, ast.Constant) and isinstance(c.value, str):
            return True
        if isinstance(c, (ast.List, ast.Tuple, ast.Set)) and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in c.elts
        ):
            return True
    return False


def _is_lexical_condition(test: ast.expr) -> bool:
    """True if `test` is (or is built from) a regex/keyword-literal check."""
    if isinstance(test, ast.BoolOp):
        return any(_is_lexical_condition(v) for v in test.values)
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return _is_lexical_condition(test.operand)
    return _is_lexical_call(test) or _is_lexical_membership(test)


def _collect_chain(node: ast.If) -> list[ast.If]:
    """Follow an if/elif chain (NOT plain `else:` blocks) and return all links."""
    chain = [node]
    orelse = node.orelse
    while len(orelse) == 1 and isinstance(orelse[0], ast.If):
        chain.append(orelse[0])
        orelse = orelse[0].orelse
    return chain


def _branch_action_signature(body: list[ast.stmt]) -> str | None:
    """A rough fingerprint of "what action this branch takes".

    Returns the function name of the first call made, or the literal value
    of the first return, or None if neither is present (so we can't compare
    it against other branches).
    """
    for stmt in body:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Call):
                fn = sub.func
                if isinstance(fn, ast.Name):
                    return f"call:{fn.id}"
                if isinstance(fn, ast.Attribute):
                    return f"call:{fn.attr}"
            if isinstance(sub, ast.Return) and sub.value is not None:
                try:
                    return f"return:{ast.unparse(sub.value)}"
                except Exception:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                    return None
    return None


def _scan_file(filepath: Path) -> list[tuple[int, str]]:
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []

    source_lines = source.splitlines()
    violations: list[tuple[int, str]] = []
    seen_lines: set[int] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or node.lineno in seen_lines:
            continue

        chain = _collect_chain(node)
        for link in chain:
            seen_lines.add(link.lineno)

        if len(chain) < _MIN_CHAIN_LEN:
            continue

        lexical_links = [link for link in chain if _is_lexical_condition(link.test)]
        if len(lexical_links) < _MIN_CHAIN_LEN:
            continue  # not predominantly a keyword/regex-gated chain

        # Chain must dispatch to at least 2 distinct actions — otherwise
        # it's just repeated validation, not a router.
        signatures = {
            sig
            for link in chain
            if (sig := _branch_action_signature(link.body)) is not None
        }
        if len(signatures) < 2:
            continue

        if node.lineno <= len(source_lines) and "noqa" in source_lines[node.lineno - 1]:
            continue

        violations.append(
            (
                node.lineno,
                f"hardcoded lexical-heuristic router: {len(chain)}-branch "
                f"if/elif chain gated on regex/keyword literals dispatches "
                f"to {len(signatures)} distinct actions",
            )
        )

    return violations


def _iter_backend_files() -> list[Path]:
    files: list[Path] = []
    for root, dirs, filenames in os.walk(_BACKEND_DIR):
        dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS]
        for fname in filenames:
            if fname.endswith(".py"):
                files.append(Path(root) / fname)
    return files


class TestNoLexicalRoutingHeuristics:
    """Ensure no new hardcoded keyword/regex routers have been introduced.

    If this test fails, replace the if/elif keyword chain with a deterministic
    contract (explicit tool/procedure IDs, enums) or scored retrieval/ranking
    (embeddings, FUSED score) — see ``chat_preflight.deterministic_procedure_hint``
    for the pattern this project already uses. If the branching is genuinely
    just keyword-gated and safe (e.g. all branches are equivalent no-ops),
    add ``# noqa: LEXICAL-ROUTE — <reason>`` on the ``if`` line.
    """

    def test_no_lexical_routing_patterns(self):
        all_violations: list[str] = []
        for filepath in _iter_backend_files():
            for lineno, desc in _scan_file(filepath):
                rel = filepath.relative_to(_BACKEND_DIR)
                all_violations.append(f"  {rel}:{lineno}: {desc}")

        if all_violations:
            pytest.fail(
                f"Found {len(all_violations)} hardcoded lexical-routing "
                "chain(s):\n"
                + "\n".join(all_violations)
                + "\n\nFix: replace with a deterministic contract or scored "
                "retrieval/ranking, or justify with "
                "# noqa: LEXICAL-ROUTE — <reason>"
            )

    def test_scanner_runs_without_crashing(self):
        for filepath in _iter_backend_files():
            _scan_file(filepath)
