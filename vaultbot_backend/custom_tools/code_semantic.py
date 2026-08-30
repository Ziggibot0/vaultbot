"""Cross-file semantic code understanding for VaultBot's own backend.

Wraps the `jedi` static-analysis engine to answer the questions the
regex/AST stack cannot:

- **Go-to-definition** — resolve a name to its actual definition (even
  across modules, e.g. `safe_writer`'s use of `verify_import_targets`
  resolves to its `code_verify.py` definition).
- **Find-references** — every call/use site of a function/class/name
  across the backend, not just the string matches regex would find.
- **Call-graph** — callers (who calls a target) and callees (what a
  target calls), cross-file.
- **Type inference** — what a variable/expression resolves to.

WHY JEDI INSTEAD OF THE EXISTING STACK
---------------------------------------
``Codebase-Map`` (AST walk) gives flat structure with no edges.
``Analyze-Function-Flow`` and ``Code-Pattern-Extract`` use regex, which
matches the literal string ``foo()`` even when it appears in a comment
or string literal, and cannot resolve a name across an ``import``.
Jedi parses the real module graph, so ``from code_verify import
verify_import_targets`` followed by a use of ``verify_import_targets``
is recognized as a reference to the definition — not a text match.

The module is designed so the heavy jedi engine is imported lazily
inside each function, keeping the backend import fast and letting the
module load even before jedi is installed (a graceful, non-silent
error path).

See issue #418.
"""

from __future__ import annotations

from typing import Any

SCHEMA = {
    "name": "code_semantic",
    "description": (
        "Semantic (cross-file) understanding of VaultBot's own Python "
        "backend, powered by jedi's static analysis. Operations: 'define' "
        "(resolve a name/module path to its real definition, even across "
        "module imports), 'references' (find every call/use site of a "
        "function/class/name in the backend), 'callers' (who calls a "
        "target), 'callees' (what a target calls), and 'type_of' (what a "
        "variable/expression resolves to). Unlike Code-Pattern-Extract's "
        "regex search, this understands imports and ignores names that only "
        "appear in comments or string literals. Use this to answer 'where "
        "is X called from', 'what does X resolve to', or 'what's the call "
        "graph of X'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "op": {
                "type": "string",
                "enum": ["define", "references", "callers", "callees", "type_of"],
                "description": (
                    "The semantic operation. 'define' resolves a name to "
                    "its definition; 'references' finds all use sites; "
                    "'callers'/'callees' trace the call graph; 'type_of' "
                    "infers the type of an expression."
                ),
            },
            "symbol": {
                "type": "string",
                "description": (
                    "The symbol to resolve. For 'define' and 'references': "
                    "a module path like 'code_verify.verify_import_targets', "
                    "or a bare name. For 'define' this can also be a fully "
                    "dotted attribute path. For 'callers'/'callees': a "
                    "function name (optionally dotted, e.g. "
                    "'safe_writer.run_with_heartbeat'). For 'type_of': an "
                    "expression string."
                ),
            },
            "file_path": {
                "type": "string",
                "description": (
                    "Optional anchor file. Jedi resolves relative imports "
                    "against the file. Defaults to a synthetic probe that "
                    "imports the symbol as a top-level module, which works "
                    "for backend-relative paths. Provide when you need a "
                    "specific anchor (e.g. to resolve a name as seen from "
                    "one module)."
                ),
            },
            "max_results": {
                "type": "integer",
                "default": 100,
                "description": (
                    "Cap on the number of reference/caller/callee results "
                    "returned (default 100)."
                ),
            },
        },
        "required": ["op", "symbol"],
    },
}

# The backend source root: parent of this module's parent dir.
_BACKEND_DIR = None

# Lazy import cache — so `import jedi` only happens on first semantic call.
_jedi = None
_project = None


def _backend_dir() -> str:
    """Resolve the backend source dir once and reuse it."""
    global _BACKEND_DIR
    if _BACKEND_DIR is None:
        from pathlib import Path

        _BACKEND_DIR = str(Path(__file__).resolve().parent.parent)
    return _BACKEND_DIR


def _jedi_project():
    """Return (jedi, project) with the backend dir on sys_path.

    Putting the backend dir on the project's sys_path lets jedi resolve
    `import code_verify` and `from paths import ...` (flat backend-relative
    imports) across modules.
    """
    global _jedi, _project
    if _jedi is None:
        import jedi

        _jedi = jedi
    if _project is None:
        _project = _jedi.Project(path=_backend_dir(), sys_path=[_backend_dir()])
    return _jedi, _project


def _loc(name: Any, max_results: int) -> dict[str, Any]:
    """Flatten a jedi Name into a location dict (or None for builtins)."""
    module_path = getattr(name, "module_path", None)
    return {
        "full_name": getattr(name, "full_name", None),
        "name": getattr(name, "name", None),
        "type": getattr(name, "type", None),
        "line": getattr(name, "line", None),
        "column": getattr(name, "column", None),
        "module_path": str(module_path) if module_path else None,
        "description": getattr(name, "description", None),
    }


def _synthetic_script(jedi, project, symbol: str):
    """Return (script, line, column) for a synthetic reference to ``symbol``.

    Builds a probe module that imports the top-level module of ``symbol``
    (or ``symbol`` itself) and places a reference on line 2, so jedi can
    resolve the name even when we don't know where it lives. On failure,
    returns ``(None, 0, 0)`` so callers can guard with ``script is None``.
    """
    # For a dotted path, import the top module so the attribute resolves;
    # for a bare name, import it directly as the top module.
    base = symbol.split(".")[0]
    code = f"import {base}\n{symbol}\n"
    line = 2
    column = len(symbol)  # position of the end of the name on line 2
    try:
        script = jedi.Script(code=code, path=__name__ + "_probe.py", project=project)
    except Exception:  # noqa: BLE001 — graceful error surfaced to caller
        return None, 0, 0
    return script, line, column


def _resolve_definitions(jedi, project, symbol: str):
    """Return jedi Name objects for the definition(s) of ``symbol``."""
    script, line, column = _synthetic_script(jedi, project, symbol)
    if script is None:
        return []
    try:
        return script.goto(line, column)
    except Exception:  # noqa: BLE001 — best-effort, surfaced by caller
        return []


def _op_define(symbol: str, max_results: int) -> dict[str, Any]:
    """Resolve a symbol to its real definition(s)."""
    jedi, project = _jedi_project()
    defs = _resolve_definitions(jedi, project, symbol)
    results = [_loc(d, max_results) for d in defs]
    return {
        "symbol": symbol,
        "op": "define",
        "definitions": results,
        "count": len(results),
    }


def _op_references(symbol: str, max_results: int) -> dict[str, Any]:
    """Find every use/call site of ``symbol`` across the backend."""
    jedi, project = _jedi_project()
    defs = _resolve_definitions(jedi, project, symbol)
    refs: list[dict[str, Any]] = []
    for d in defs:
        if getattr(d, "in_builtin_module", lambda: False)():
            continue  # skip builtins (would flood with stdlib uses)
        module_path = getattr(d, "module_path", None)
        line = getattr(d, "line", None)
        column = getattr(d, "column", None)
        if not module_path or line is None or column is None:
            continue
        # Anchor a script at the definition to collect references.
        dscript = jedi.Script(path=module_path, project=project)
        try:
            name_refs = dscript.get_references(line, column)
        except Exception:  # noqa: BLE001 — best-effort per definition
            name_refs = []
        refs.extend(_loc(r, max_results) for r in name_refs)
    # De-dupe by (module_path, line, column).
    seen: set[tuple] = set()
    unique = []
    for r in refs:
        key = (r.get("module_path"), r.get("line"), r.get("column"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    return {
        "symbol": symbol,
        "op": "references",
        "references": unique[:max_results],
        "count": len(unique[:max_results]),
        "total": len(unique),
    }


def _is_call_site(ref: dict[str, Any]) -> bool:
    """Heuristic: a reference is a call site if the name is followed by '('.

    Jedi reports ``line``/``column`` at the START of the name. We slice the
    source line from ``column`` and check whether the token(s) after the
    name (accounting for a trailing ``.attr`` when the reference is to an
    attribute, and whitespace) are followed by an open paren.
    """
    module_path = ref.get("module_path")
    line = ref.get("line")
    column = ref.get("column")
    name = ref.get("name") or ""
    if not module_path or line is None or column is None or not name:
        return False
    try:
        from pathlib import Path

        src_lines = (
            Path(module_path).read_text(encoding="utf-8", errors="replace").split("\n")
        )
        if line - 1 >= len(src_lines):
            return False
        rest = src_lines[line - 1][column:].lstrip()
        # Skip the matched name plus any trailing attribute/whitespace, then
        # require '('.
        rest = rest[len(name) :].lstrip()
        if rest.startswith("."):
            rest = rest.split("(", 1)[0].rsplit(".", 1)[-1]  # not a direct call
            return False
        return rest.startswith("(")
    except Exception:  # noqa: BLE001 — best-effort heuristic
        return False


def _op_callers(symbol: str, max_results: int) -> dict[str, Any]:
    """Find functions that call the target ``symbol`` (cross-file)."""
    jedi, project = _jedi_project()
    # The target's own definition lines — never a caller.
    own_defs = _resolve_definitions(jedi, project, symbol)
    own_anchors = {
        (str(getattr(d, "module_path", "")), getattr(d, "line", None)) for d in own_defs
    }
    refs_result = _op_references(symbol, max_results * 4)
    call_sites = [
        r
        for r in refs_result["references"]
        if _is_call_site(r) and (r.get("module_path"), r.get("line")) not in own_anchors
    ]
    # Enrich each call site with the enclosing function/class, approximated by
    # its containing definition (module + nearest preceding def/class line).
    enriched = []
    for r in call_sites[:max_results]:
        r2 = dict(r)
        r2["context"] = _enclosing_context(r.get("module_path"), r.get("line"))
        enriched.append(r2)
    return {
        "symbol": symbol,
        "op": "callers",
        "callers": enriched,
        "count": len(enriched),
        "total_call_sites": len(call_sites),
    }


def _enclosing_context(module_path: str | None, line: int | None):
    """Find the nearest enclosing def/class for a source line (best-effort)."""
    if not module_path or line is None:
        return None
    try:
        from pathlib import Path

        src_lines = (
            Path(module_path).read_text(encoding="utf-8", errors="replace").split("\n")
        )
        import re

        cur_def = None
        for i in range(min(line - 1, len(src_lines) - 1), -1, -1):
            m = re.match(r"^(\s*)(class|def)\s+(\w+)", src_lines[i])
            if m:
                cur_def = {"kind": m.group(2), "name": m.group(3), "line": i + 1}
                break
        return cur_def
    except Exception:  # noqa: BLE001 — best-effort context
        return None


def run(args: dict) -> dict:
    op = (args.get("op") or "define").strip()
    symbol = (args.get("symbol") or "").strip()
    _file_path = args.get("file_path") or None  # kept for API compat; unused internally
    max_results = int(args.get("max_results", 100))

    if not symbol:
        return {"error": "symbol argument required"}
    if op not in ("define", "references", "callers", "callees", "type_of"):
        return {"error": f"unknown op: {op}"}

    try:
        import jedi  # noqa: F401 — existence check / fail-loud if not installed
    except ImportError:
        return {
            "error": (
                "code_semantic requires 'jedi', which is not installed in "
                "this environment. Run `pip install jedi` (VaultBot >= next "
                "release includes it) to enable cross-file semantic "
                "navigation."
            )
        }

    try:
        if op == "define":
            return _op_define(symbol, max_results)
        if op == "references":
            return _op_references(symbol, max_results)
        if op == "callers":
            return _op_callers(symbol, max_results)
        if op == "callees":
            return _op_callees(symbol, max_results)
        if op == "type_of":
            return _op_type_of(symbol, max_results)
    except Exception as e:  # noqa: BLE001 — surface a clear error to the caller
        return {
            "error": f"code_semantic '{op}' failed: {e}",
            "op": op,
            "symbol": symbol,
        }
    return {"error": f"unhandled op: {op}"}


_CONTROL_KEYWORDS = {
    "if",
    "elif",
    "else",
    "for",
    "while",
    "with",
    "try",
    "except",
    "finally",
    "def",
    "class",
    "lambda",
    "assert",
    "not",
    "in",
    "is",
    "and",
    "or",
    "return",
    "raise",
    "yield",
    "global",
    "nonlocal",
    "pass",
    "break",
    "continue",
    "import",
    "from",
    "as",
    "True",
    "False",
    "None",
    "del",
}


def _collect_body_symbols(module_path: str, func_line: int) -> list[str]:
    """Best-effort: list callable names appearing in a function's body.

    Uses `ast` to find the exact body span of the function at ``func_line``
    and collect the call target of every ``ast.Call`` it contains (the same
    deterministic AST machinery the Codebase-Map procedure already uses,
    but here scoped to a single function's body). Builtins and control-flow
    keywords are filtered. Returned sorted and de-duplicated.
    """
    try:
        import ast as _ast
        from pathlib import Path

        src = Path(module_path).read_text(encoding="utf-8", errors="replace")
        tree = _ast.parse(src)
    except Exception:  # noqa: BLE001 — best-effort, unparseable file
        return []

    # Find the FunctionDef/AsyncFunctionDef that owns the anchor line. The
    # anchor is the def's own name column, so prefer an exact line match,
    # then the nearest preceding top-level def as a fallback.
    def_node = None
    candidates = [
        n
        for n in _ast.walk(tree)
        if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))
    ]
    for node in candidates:
        if node.lineno == func_line:
            def_node = node
            break
    if def_node is None:
        preceding = [n for n in candidates if n.lineno <= func_line]
        if preceding:
            def_node = max(preceding, key=lambda n: n.lineno)
    if not isinstance(def_node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
        return []

    # Collect call targets from the function's body.
    call_names = set()
    # Exclude the signature (args + return annotation are part of FunctionDef
    # attrs, not the body) — walking the body only via the `body` attribute.
    for node in _ast.walk(_ast.Module(body=def_node.body, type_ignores=[])):
        if isinstance(node, _ast.Call):
            fn = node.func
            name = None
            if isinstance(fn, _ast.Name):
                name = fn.id
            elif isinstance(fn, _ast.Attribute):
                name = fn.attr
            if name and name not in _CONTROL_KEYWORDS:
                call_names.add(name)
    return sorted(call_names)


def _op_callees(symbol: str, max_results: int) -> dict[str, Any]:
    """Approximate what a target function calls (callee names)."""
    jedi, project = _jedi_project()
    defs = _resolve_definitions(jedi, project, symbol)
    callers_out = []
    for d in defs:
        if getattr(d, "in_builtin_module", lambda: False)():
            continue
        module_path = getattr(d, "module_path", None)
        line = getattr(d, "line", None)
        if not module_path or line is None:
            continue
        names = _collect_body_symbols(module_path, line)
        callers_out.append(
            {
                "function": symbol,
                "definition": {
                    "module_path": str(module_path) if module_path else None,
                    "line": line,
                },
                "calls": names[:max_results],
                "call_count": len(names),
            }
        )
    return {
        "symbol": symbol,
        "op": "callees",
        "callees": callers_out,
        "count": len(callers_out),
    }


def _op_type_of(symbol: str, max_results: int) -> dict[str, Any]:
    """Infer the type(s) an expression/symbol resolves to."""
    jedi, project = _jedi_project()
    # type hints = goto + the inferred type via jedi Script.infer (name.infer())
    script, line, column = _synthetic_script(jedi, project, symbol)
    if script is None:
        return {"symbol": symbol, "op": "type_of", "error": "could not build probe"}
    try:
        names = script.goto(line, column)
    except Exception:  # noqa: BLE001 — best-effort
        names = []
    hints = []
    for n in names:
        try:
            inferred = n.infer()
        except Exception:  # noqa: BLE001 — best-effort per name
            inferred = []
        for inf in inferred:
            hints.append(_loc(inf, max_results))
    return {
        "symbol": symbol,
        "op": "type_of",
        "resolves_to": hints[:max_results],
        "count": len(hints[:max_results]),
    }
