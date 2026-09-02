#!/usr/bin/env python3
"""Fail CI when changed Python files contain dangerous execution patterns.

This check is a hard gate for issue #330. It scans only changed .py files in
a git diff range and blocks high-risk primitives unless the file is explicitly
allowlisted.

Blocked calls:
- eval(...)
- exec(...)
- __import__(...)
- os.system(...)
- pickle.load(...), pickle.loads(...)
- marshal.load(...), marshal.loads(...)
- shutil.rmtree(...)
- subprocess.call/run/Popen(..., shell=True)
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

# Existing hot-path files with legacy subprocess patterns are allowlisted so
# this gate blocks NEW introductions without breaking known baseline debt.
_ALLOWLIST: set[str] = {
    "vaultbot_backend/vault_indexer.py",
    "vaultbot_backend/chat_handler.py",
    "vaultbot_backend/fused_retrieval.py",
}


class DangerousPatternVisitor(ast.NodeVisitor):
    """Collect dangerous call sites from a Python AST."""

    def __init__(self) -> None:
        self.findings: list[tuple[int, str, str]] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func_name = self._func_name(node.func)

        if func_name in {"eval", "exec", "__import__"}:
            self.findings.append((node.lineno, func_name, "dynamic code execution"))
        elif func_name == "os.system":
            self.findings.append((node.lineno, func_name, "shell command execution"))
        elif func_name in {
            "pickle.load",
            "pickle.loads",
            "marshal.load",
            "marshal.loads",
        }:
            self.findings.append((node.lineno, func_name, "unsafe deserialization"))
        elif func_name == "shutil.rmtree":
            self.findings.append((node.lineno, func_name, "recursive delete primitive"))
        elif func_name in {
            "subprocess.call",
            "subprocess.run",
            "subprocess.Popen",
        } and self._has_shell_true(node):
            self.findings.append(
                (
                    node.lineno,
                    func_name,
                    "subprocess shell=True (command injection risk)",
                )
            )

        self.generic_visit(node)

    @staticmethod
    def _func_name(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parts: list[str] = []
            cur: ast.AST | None = node
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
            return ".".join(reversed(parts))
        return ""

    @staticmethod
    def _has_shell_true(node: ast.Call) -> bool:
        for kw in node.keywords:
            if kw.arg != "shell":
                continue
            value = kw.value
            if isinstance(value, ast.Constant) and value.value is True:
                return True
            if isinstance(value, ast.NameConstant) and value.value is True:
                return True
        return False


def _changed_python_files(base: str, head: str) -> list[str]:
    cmd = ["git", "diff", "--name-only", "--diff-filter=ACMR", base, head]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"git diff failed: {result.stderr.strip()}")

    files = [line.strip().replace("\\", "/") for line in result.stdout.splitlines()]
    return [f for f in files if f.endswith(".py")]


def _scan_file(repo_root: Path, rel_path: str) -> list[tuple[int, str, str]]:
    file_path = repo_root / rel_path
    if not file_path.exists():
        return []

    source = file_path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        return [(exc.lineno or 1, "syntax_error", "cannot parse file for safety scan")]

    visitor = DangerousPatternVisitor()
    visitor.visit(tree)
    return visitor.findings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check changed Python files for dangerous patterns."
    )
    parser.add_argument("--base", required=True, help="base git SHA for diff")
    parser.add_argument("--head", required=True, help="head git SHA for diff")
    args = parser.parse_args()

    repo_root = Path.cwd()
    try:
        changed_py = _changed_python_files(args.base, args.head)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"dangerous-pattern check: {exc}", file=sys.stderr)
        return 1

    if not changed_py:
        print("dangerous-pattern check: no changed Python files")
        return 0

    violations: list[str] = []
    for rel_path in changed_py:
        if rel_path in _ALLOWLIST:
            continue
        findings = _scan_file(repo_root, rel_path)
        for lineno, pattern, reason in findings:
            violations.append(f"{rel_path}:{lineno}: {pattern} ({reason})")

    if violations:
        print("DANGEROUS-PATTERN CHECK FAILED:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        print(
            "\nRemove these calls or move them behind reviewed, allowlisted modules.",
            file=sys.stderr,
        )
        return 1

    print(
        "dangerous-pattern check: scanned "
        f"{len(changed_py)} changed Python file(s), no blocked patterns found"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
