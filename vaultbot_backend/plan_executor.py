"""plan_executor.py — model-robust execution layer for VaultBot plans.

WHY THIS EXISTS
---------------
"Plain English -> vault carries it out" only works if the execution layer is
agnostic to *which* model produced the plan. A worker model (any of them) emits
a JSON plan of atomic, idempotent subtasks; this module executes each one
against graph operations wired in by the caller, verifies each result with a
*deterministic verifier* (a Python expression, not the worker's self-report),
and closes the loop with an optional LLM judge.

The four pillars of model-robustness baked in here:

1. **JSON, not Markdown.** Plans are structured subtask dicts. No regex-parsing
   of prose, no "the model said it did it". The plan is a contract.

2. **Verifier, not self-report.** Every subtask carries a `verifier` expression
   string evaluated against the *actual op result*. The worker model is NOT
   trusted to say "yes I did it" — a deterministic gate decides `done`.

3. **Idempotent ops.** The executor itself does NOT enforce idempotency — the
   registered graph ops must be idempotent by design (re-running them yields the
   same vault state). The executor *does* log every attempt + result so that
   duplicate/retried runs are visible in the plan log. Idempotent ops make
   retries safe and make "resume a half-finished plan" trivial.

4. **Judge closes the loop.** A `judge()` method reads subtask results and
   returns whether the whole goal is complete, with reasoning and a list of
   any missing subtask ids. Falls back to a deterministic check
   (all done + verifier passed) when no LLM client is supplied. The judge is the
   final word — not the worker model's self-report.

This module is deliberately self-contained: it does NOT import other VaultBot
modules. The caller (e.g. main.py) constructs an `op_registry` mapping op-name
to a callable `(args: dict) -> dict` and passes it in. That keeps the executor
decoupled from the concrete graph implementation.

Pure stdlib only: dataclasses, json, ast (manual walk — no eval),
pathlib, datetime, typing. No new dependencies.
"""

from __future__ import annotations

import ast
import contextlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Subtask:
    """A single atomic, idempotent subtask with a deterministic verifier.

    Attributes:
        id: stable identifier used for resume / dedupe / judge references.
        op: name of the graph op to call; must exist in the op_registry.
        intent: human-readable description of what this subtask accomplishes.
        args: dict passed straight to the op callable as its only argument.
        verifier: a Python expression string evaluated with `result` in scope.
            Must return truthy/falsy. Example:
            ``"result.get('sources') and len(result['sources']) >= 3"``
        status: pending | running | done | failed | skipped.
        attempts: how many times this subtask has been attempted so far.
        max_attempts: cap before a subtask is marked permanently failed.
        result: the dict returned by the op on the last attempt (None until run).
        error: exception message if the op or verifier raised; None otherwise.
        verifier_passed: outcome of the verifier on the last attempt.
    """

    id: str
    op: str
    intent: str
    args: dict
    verifier: str
    status: str = "pending"
    attempts: int = 0
    max_attempts: int = 5
    result: dict | None = None
    error: str | None = None
    verifier_passed: bool | None = None


@dataclass
class Plan:
    """A full plan: an ordered list of Subtasks plus bookkeeping."""

    id: str
    goal: str
    subtasks: list[Subtask]
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    status: str = "pending"  # pending | running | done | failed | partial
    log: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def subtask_to_json(subtask: Subtask) -> dict:
    return {
        "id": subtask.id,
        "op": subtask.op,
        "intent": subtask.intent,
        "args": subtask.args,
        "verifier": subtask.verifier,
        "status": subtask.status,
        "attempts": subtask.attempts,
        "max_attempts": subtask.max_attempts,
        "result": subtask.result,
        "error": subtask.error,
        "verifier_passed": subtask.verifier_passed,
    }


def subtask_from_json(data: dict) -> Subtask:
    return Subtask(
        id=data["id"],
        op=data["op"],
        intent=data.get("intent", ""),
        args=data.get("args", {}) or {},
        verifier=data.get("verifier", "True"),
        status=data.get("status", "pending"),
        attempts=data.get("attempts", 0),
        max_attempts=data.get("max_attempts", 5),
        result=data.get("result"),
        error=data.get("error"),
        verifier_passed=data.get("verifier_passed"),
    )


def plan_to_json(plan: Plan) -> dict:
    return {
        "id": plan.id,
        "goal": plan.goal,
        "subtasks": [subtask_to_json(s) for s in plan.subtasks],
        "created_at": plan.created_at,
        "status": plan.status,
        "log": plan.log,
    }


def plan_from_json(data: dict) -> Plan:
    return Plan(
        id=data["id"],
        goal=data.get("goal", ""),
        subtasks=[subtask_from_json(s) for s in data.get("subtasks", [])],
        created_at=data.get("created_at", ""),
        status=data.get("status", "pending"),
        log=data.get("log", []),
    )


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


# Safe verifier evaluator — replaces eval() with an AST-walking interpreter.
#
# The old code used eval(expr, {"__builtins__": _SAFE_BUILTINS}, {"result": result})
# which is the classic Python sandbox bypass: restricted __builtins__ doesn't
# prevent attribute-chain escapes like ().__class__.__bases__[0].__subclasses__()
# because __class__ is an attribute, not a builtin.  This safe evaluator walks
# the AST and rejects any node type or attribute access that could escape.
#
# Allowed node types: Expression, BoolOp, BinOp, UnaryOp, Compare, Constant,
# Name, Subscript, Attribute (only whitelisted attrs), List, Tuple, Dict, Set,
# Call (only to whitelisted builtins), IfExp.
#
# Allowed builtins (callable): len, all, any, min, max, sum, bool, int, str,
# float, list, dict, tuple, set, round, sorted, enumerate, zip, range.
#
# Allowed attributes (read-only): .get, .keys, .values, .items, .lower, .upper,
# .strip, .startswith, .endswith, .split, .join, .find, .count, .replace,
# .isdigit, .isalpha, .append, .__len__.


class _VerifierError(Exception):
    """Raised when a verifier expression is rejected by the safe evaluator."""


# Builtins the verifier can call — deliberately tiny, all pure functions.
_SAFE_BUILTINS = {
    "len": len,
    "all": all,
    "any": any,
    "min": min,
    "max": max,
    "sum": sum,
    "bool": bool,
    "int": int,
    "str": str,
    "float": float,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "set": set,
    "round": round,
    "sorted": sorted,
    "enumerate": enumerate,
    "zip": zip,
    "range": range,
    "abs": abs,
    "True": True,
    "False": False,
    "None": None,
}

# Attribute names the verifier can access on objects — read-only methods
# that can't be used to escape the sandbox.  No __dunder__ except __len__
# (used by len()).  Crucially, __class__, __bases__, __subclasses__,
# __globals__, __builtins__, __dict__ are NOT here.
_SAFE_ATTRS = frozenset(
    {
        "get",
        "keys",
        "values",
        "items",
        "lower",
        "upper",
        "strip",
        "lstrip",
        "rstrip",
        "startswith",
        "endswith",
        "split",
        "rsplit",
        "join",
        "find",
        "rfind",
        "count",
        "replace",
        "isdigit",
        "isalpha",
        "append",
        "extend",
        "__len__",
    }
)

# AST node types the evaluator allows.  Anything not in this set is rejected.
# Includes operator nodes (And, Or, Eq, Add, etc.) and Load/Store contexts
# that ast.walk visits but are harmless.
_ALLOWED_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Constant,
    ast.Name,
    ast.Subscript,
    ast.Attribute,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.Set,
    ast.Call,
    ast.IfExp,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,  # comprehensions (read-only)
    ast.comprehension,
    # Boolean + comparison operators
    ast.And,
    ast.Or,
    ast.Not,
    ast.Eq,
    ast.NotEq,
    ast.Gt,
    ast.GtE,
    ast.Lt,
    ast.LtE,
    ast.In,
    ast.NotIn,
    ast.Is,
    ast.IsNot,
    # Binary arithmetic operators
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.Pow,
    ast.FloorDiv,
    ast.LShift,
    ast.RShift,
    ast.BitOr,
    ast.BitAnd,
    ast.BitXor,
    # Unary operators
    ast.USub,
    ast.UAdd,
    ast.Invert,
    # Load/Store contexts (harmless — just reference modes)
    ast.Load,
    ast.Store,
    ast.Del,
)


def _safe_eval_verifier(expr: str, result: Any) -> Any:
    """Safely evaluate a verifier expression with `result` in scope.

    Walks the AST and rejects any node type or attribute access that
    could escape the sandbox (e.g. __class__, __subclasses__, __globals__).
    Raises _VerifierError on rejection.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise _VerifierError(f"invalid syntax: {exc}") from exc

    # Pre-walk: reject any node type not in the allowed set.
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise _VerifierError(
                f"disallowed AST node: {type(node).__name__} — "
                f"verifiers may only use comparisons, subscripts, "
                f"attribute access, and function calls"
            )

        # Reject dangerous attribute access FIRST — before the Call check,
        # so __class__/__subclasses__ etc. are caught regardless of whether
        # they're called or just accessed.
        if isinstance(node, ast.Attribute):
            attr = node.attr
            if attr not in _SAFE_ATTRS:
                raise _VerifierError(
                    f"disallowed attribute access: .{attr} — "
                    f"only read-only methods are permitted"
                )

        # Calls: allow (a) top-level builtin names (len, any, etc.) and
        # (b) method calls on whitelisted attributes (result.get(...),
        # result['x'].lower()).  The attribute check above already
        # vetted the attribute name, so a method call on a safe attr
        # is fine.  Reject calls to anything else (e.g. calling a
        # subscript result or a lambda).
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                # Top-level builtin call — fine (eval will KeyError if
                # the name isn't in _SAFE_BUILTINS).
                pass
            elif isinstance(func, ast.Attribute):
                # Method call — the attribute was already checked above.
                pass
            else:
                raise _VerifierError(
                    "only built-in function calls and method calls "
                    "on safe attributes are permitted"
                )

    # Walk the vetted AST directly with our own interpreter — no eval().
    # The pre-walk above already rejected every dangerous node type and
    # attribute, so _ast_eval only needs to handle the whitelisted subset.
    return _ast_eval(tree.body, result)


# ---------------------------------------------------------------------------
# Pure AST interpreter — replaces eval() so there is no builtin-eval surface
# at all.  The AST was already vetted by _safe_eval_verifier's pre-walk
# (only whitelisted node types + attribute names), so this interpreter only
# needs to handle the allowed subset.  It cannot escape because it never
# touches Python's eval machinery — it walks the tree manually.
# ---------------------------------------------------------------------------

# Map AST operator nodes to the Python functions that implement them.
_BIN_OPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a**b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.LShift: lambda a, b: a << b,
    ast.RShift: lambda a, b: a >> b,
    ast.BitOr: lambda a, b: a | b,
    ast.BitAnd: lambda a, b: a & b,
    ast.BitXor: lambda a, b: a ^ b,
}

_UNARY_OPS = {
    ast.USub: lambda a: -a,
    ast.UAdd: lambda a: +a,
    ast.Invert: lambda a: ~a,
    ast.Not: lambda a: not a,
}

_CMP_OPS = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
    ast.Is: lambda a, b: a is b,
    ast.IsNot: lambda a, b: a is not b,
}


def _ast_eval(node: ast.AST, result: Any, names: dict | None = None) -> Any:
    """Walk a vetted AST node and return its value.

    No eval(), no compile(), no exec() — pure tree walk.  The caller
    (_safe_eval_verifier) already rejected any node type or attribute not
    in the whitelist, so this only handles the allowed subset.  ``result``
    is the dict exposed to the verifier expression as the name ``result``.
    ``names`` is an optional dict of extra name bindings (used by
    comprehensions for loop variables).
    """
    local_names = names or {}

    # ── Leaf / container nodes ─────────────────────────────────────────
    if isinstance(node, ast.Expression):
        return _ast_eval(node.body, result, local_names)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        # Look up in _SAFE_BUILTINS first (for True/False/None/len/etc.),
        # then loop-variable scope, then `result`.
        if node.id in _SAFE_BUILTINS:
            return _SAFE_BUILTINS[node.id]
        if node.id in local_names:
            return local_names[node.id]
        if node.id == "result":
            return result
        raise _VerifierError(f"unknown name: {node.id!r}")
    if isinstance(node, ast.List):
        return [_ast_eval(e, result, local_names) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_ast_eval(e, result, local_names) for e in node.elts)
    if isinstance(node, ast.Set):
        return {_ast_eval(e, result, local_names) for e in node.elts}
    if isinstance(node, ast.Dict):
        return {
            _ast_eval(k, result, local_names): _ast_eval(v, result, local_names)
            for k, v in zip(node.keys, node.values, strict=False)
        }

    # ── Operators ──────────────────────────────────────────────────────
    if isinstance(node, ast.BoolOp):
        values = [_ast_eval(v, result, local_names) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        return any(values)
    if isinstance(node, ast.BinOp):
        left = _ast_eval(node.left, result, local_names)
        right = _ast_eval(node.right, result, local_names)
        op_fn = _BIN_OPS.get(type(node.op))
        if op_fn is None:
            raise _VerifierError(f"unsupported binary op: {type(node.op).__name__}")
        return op_fn(left, right)
    if isinstance(node, ast.UnaryOp):
        operand = _ast_eval(node.operand, result, local_names)
        op_fn = _UNARY_OPS.get(type(node.op))
        if op_fn is None:
            raise _VerifierError(f"unsupported unary op: {type(node.op).__name__}")
        return op_fn(operand)
    if isinstance(node, ast.Compare):
        left = _ast_eval(node.left, result, local_names)
        for op, comparator in zip(node.ops, node.comparators, strict=False):
            right = _ast_eval(comparator, result, local_names)
            op_fn = _CMP_OPS.get(type(op))
            if op_fn is None:
                raise _VerifierError(f"unsupported comparison op: {type(op).__name__}")
            if not op_fn(left, right):
                return False
            left = right
        return True

    # ── Subscript + attribute access ───────────────────────────────────
    if isinstance(node, ast.Subscript):
        value = _ast_eval(node.value, result, local_names)
        # Python 3.9+: slice is a single expression node.
        slice_val = _ast_eval(node.slice, result, local_names)
        return value[slice_val]
    if isinstance(node, ast.Attribute):
        # The pre-walk already verified attr ∈ _SAFE_ATTRS.
        value = _ast_eval(node.value, result, local_names)
        return getattr(value, node.attr)

    # ── Calls ──────────────────────────────────────────────────────────
    if isinstance(node, ast.Call):
        func = _ast_eval(node.func, result, local_names)
        args = [_ast_eval(a, result, local_names) for a in node.args]
        kwargs = {
            kw.arg: _ast_eval(kw.value, result, local_names)
            for kw in node.keywords
            if kw.arg is not None
        }
        return func(*args, **kwargs)

    # ── Conditional expression ─────────────────────────────────────────
    if isinstance(node, ast.IfExp):
        if _ast_eval(node.test, result, local_names):
            return _ast_eval(node.body, result, local_names)
        return _ast_eval(node.orelse, result, local_names)

    # ── Comprehensions ─────────────────────────────────────────────────
    if isinstance(node, (ast.ListComp, ast.SetComp)):
        return _eval_comp(node, result, local_names)
    if isinstance(node, ast.DictComp):
        return _eval_dict_comp(node, result, local_names)

    # Should never reach here — the pre-walk rejected everything else.
    raise _VerifierError(
        f"unhandled AST node in interpreter: {type(node).__name__} — "
        f"this is a bug: the pre-walk should have rejected it"
    )


def _eval_comp(
    comp_node: ast.ListComp | ast.SetComp,
    result: Any,
    names: dict,
) -> list | set:
    """Evaluate a ListComp or SetComp into a list or set."""
    is_set = isinstance(comp_node, ast.SetComp)
    out: list = []

    def _run(clauses, scope):
        if not clauses:
            out.append(_ast_eval(comp_node.elt, result, scope))
            return
        clause = clauses[0]
        rest = clauses[1:]
        iterable = _ast_eval(clause.iter, result, scope)
        for item in iterable:
            new_scope = dict(scope)
            _bind_target(clause.target, item, new_scope)
            if all(_ast_eval(cond, result, new_scope) for cond in clause.ifs):
                _run(rest, new_scope)

    _run(comp_node.generators, dict(names))
    return set(out) if is_set else out


def _eval_dict_comp(comp_node: ast.DictComp, result: Any, names: dict) -> dict:
    """Evaluate a DictComp into a dict."""
    out: dict = {}

    def _run(clauses, scope):
        if not clauses:
            k = _ast_eval(comp_node.key, result, scope)
            v = _ast_eval(comp_node.value, result, scope)
            out[k] = v
            return
        clause = clauses[0]
        rest = clauses[1:]
        iterable = _ast_eval(clause.iter, result, scope)
        for item in iterable:
            new_scope = dict(scope)
            _bind_target(clause.target, item, new_scope)
            if all(_ast_eval(cond, result, new_scope) for cond in clause.ifs):
                _run(rest, new_scope)

    _run(comp_node.generators, dict(names))
    return out


def _bind_target(target: ast.AST, value: Any, scope: dict) -> None:
    """Bind a comprehension target (Name or Tuple/List of Names) to value."""
    if isinstance(target, ast.Name):
        scope[target.id] = value
    elif isinstance(target, (ast.Tuple, ast.List)):
        for i, elt in enumerate(target.elts):
            _bind_target(elt, value[i], scope)
    else:
        raise _VerifierError(
            f"unsupported comprehension target: {type(target).__name__}"
        )


class PlanExecutor:
    """Execute a Plan of atomic idempotent subtasks against graph ops.

    The executor is model-robust: it trusts the verifier (a deterministic
    expression), not the worker model's self-report, and optionally closes the
    loop with an LLM judge.
    """

    def __init__(
        self,
        op_registry: dict[str, Callable[[dict], dict]],
        session_logger=None,
        max_attempts_per_subtask: int = 5,
    ) -> None:
        """Wire up the graph ops.

        Args:
            op_registry: maps op-name -> callable (args: dict) -> dict. The
                caller (main.py) registers the real graph operations here.
            session_logger: optional logger with a `.log(...)` style API. If
                None, attempts to import and use VaultBot's session_logger are
                NOT made (kept decoupled) — messages go only to the plan log.
            max_attempts_per_subtask: default cap for subtasks that don't
                specify their own `max_attempts`.
        """
        self.op_registry = op_registry
        self.session_logger = session_logger
        self.max_attempts_per_subtask = max_attempts_per_subtask

    # -- logging -----------------------------------------------------------

    def _log(self, plan: Plan, level: str, msg: str, **extra: Any) -> None:
        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "level": level,
            "msg": msg,
        }
        entry.update(extra)
        plan.log.append(entry)
        if self.session_logger is not None:
            with contextlib.suppress(Exception):
                # Be tolerant of differing logger signatures.
                self.session_logger.log(f"[{level}] {msg}")  # type: ignore[attr-defined]

    # -- verification ------------------------------------------------------

    def verify(self, subtask: Subtask, result: dict | None) -> bool:
        """Safely evaluate `subtask.verifier` with `result` in scope.

        Returns True/False. A broken verifier expression counts as a *failed*
        verification (returns False) — never raises.
        """
        expr = subtask.verifier
        if not expr:
            # No verifier means "accept the result as-is if op returned a dict."
            return isinstance(result, dict)
        try:
            value = _safe_eval_verifier(expr, result)
            return bool(value)
        except _VerifierError as exc:
            subtask.error = f"verifier rejected: {exc}"
            return False
        except Exception as exc:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            # A broken verifier = failed verification, not a crash.
            subtask.error = f"verifier error: {exc}"
            return False

    # -- persistence -------------------------------------------------------

    def load_plan(self, path: str) -> Plan:
        """Load a plan.json from disk."""
        p = Path(path)
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return plan_from_json(data)

    def save_plan(self, plan: Plan, path: str) -> None:
        """Persist plan state to disk as JSON."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as fh:
            json.dump(plan_to_json(plan), fh, indent=2, ensure_ascii=False)

    # -- execution ---------------------------------------------------------

    def _execute_one(self, plan: Plan, subtask: Subtask) -> None:
        """Execute a single subtask in place, mutating `subtask` and logging."""
        max_attempts = subtask.max_attempts or self.max_attempts_per_subtask

        # Skip already-finished subtasks.
        if subtask.status == "done":
            self._log(plan, "info", f"skip subtask {subtask.id}: already done")
            return
        if subtask.status == "skipped":
            return

        # Unknown op -> permanent fail.
        op_callable = self.op_registry.get(subtask.op)
        if op_callable is None:
            subtask.status = "failed"
            subtask.error = "unknown op"
            subtask.verifier_passed = False
            self._log(
                plan,
                "error",
                f"subtask {subtask.id}: unknown op '{subtask.op}'",
            )
            return

        subtask.status = "running"
        self._log(
            plan,
            "info",
            f"subtask {subtask.id}: running op '{subtask.op}' (attempt "
            f"{subtask.attempts + 1}/{max_attempts})",
        )

        # Call the op, robustly.
        try:
            result = op_callable(subtask.args)
            if not isinstance(result, dict):
                result = {"_raw": result}
        except Exception as exc:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            subtask.attempts += 1
            subtask.error = f"op error: {exc}"
            subtask.result = None
            if subtask.attempts >= max_attempts:
                subtask.status = "failed"
                self._log(
                    plan,
                    "error",
                    f"subtask {subtask.id}: op raised (attempt "
                    f"{subtask.attempts}/{max_attempts}) — permanently failed",
                )
            else:
                subtask.status = "failed"
                self._log(
                    plan,
                    "warn",
                    f"subtask {subtask.id}: op raised (attempt "
                    f"{subtask.attempts}/{max_attempts}) — will retry later",
                )
            return

        subtask.error = None

        # Verify the result deterministically.
        passed = self.verify(subtask, result)
        subtask.verifier_passed = passed
        subtask.result = result

        if passed:
            subtask.status = "done"
            self._log(
                plan,
                "info",
                f"subtask {subtask.id}: verifier passed — done",
            )
            return

        # Verifier failed.
        subtask.attempts += 1
        if subtask.attempts >= max_attempts:
            subtask.status = "failed"
            self._log(
                plan,
                "error",
                f"subtask {subtask.id}: verifier failed (attempt "
                f"{subtask.attempts}/{max_attempts}) — permanently failed",
            )
        else:
            subtask.status = "failed"
            self._log(
                plan,
                "warn",
                f"subtask {subtask.id}: verifier failed (attempt "
                f"{subtask.attempts}/{max_attempts}) — will retry later",
            )

    def execute(self, plan: Plan) -> Plan:
        """Run all pending subtasks in order. Resumes if some are already done."""
        plan.status = "running"
        self._log(plan, "info", f"executing plan '{plan.id}' ({plan.goal!r})")

        for subtask in plan.subtasks:
            self._execute_one(plan, subtask)

        # Aggregate plan status.
        statuses = [s.status for s in plan.subtasks]
        if all(st == "done" for st in statuses):
            plan.status = "done"
        elif any(st == "done" for st in statuses):
            plan.status = "partial"
        else:
            plan.status = "failed"
        self._log(plan, "info", f"plan '{plan.id}' finished: {plan.status}")
        return plan

    def execute_subtask(self, plan: Plan, subtask_id: str) -> Plan:
        """Execute ONE subtask by id (for incremental / resume)."""
        for subtask in plan.subtasks:
            if subtask.id == subtask_id:
                plan.status = "running"
                self._execute_one(plan, subtask)
                # Recompute aggregate status.
                statuses = [s.status for s in plan.subtasks]
                if all(st == "done" for st in statuses):
                    plan.status = "done"
                elif any(st == "done" for st in statuses):
                    plan.status = "partial"
                else:
                    plan.status = "failed"
                return plan
        # Not found.
        self._log(plan, "error", f"execute_subtask: unknown id {subtask_id!r}")
        return plan

    def resume(self, plan_path: str) -> Plan:
        """Load + continue a partially-done plan from disk."""
        plan = self.load_plan(plan_path)
        return self.execute(plan)

    # -- status ------------------------------------------------------------

    def status(self, plan: Plan) -> dict:
        """Return a compact summary suitable for a status endpoint."""
        subtasks = [
            {
                "id": s.id,
                "op": s.op,
                "status": s.status,
                "attempts": s.attempts,
                "verifier_passed": s.verifier_passed,
            }
            for s in plan.subtasks
        ]
        done = sum(1 for s in plan.subtasks if s.status == "done")
        failed = sum(1 for s in plan.subtasks if s.status == "failed")
        pending = sum(1 for s in plan.subtasks if s.status == "pending")
        running = sum(1 for s in plan.subtasks if s.status == "running")
        return {
            "plan_id": plan.id,
            "goal": plan.goal,
            "status": plan.status,
            "total": len(plan.subtasks),
            "done": done,
            "failed": failed,
            "pending": pending,
            "running": running,
            "subtasks": subtasks,
        }

    # -- judge -------------------------------------------------------------

    def judge(self, plan: Plan, ollama_client=None) -> dict:
        """LLM-judge (or deterministic fallback) over the plan's results.

        Returns ``{"complete": bool, "reasoning": str, "missing": [ids]}``.

        If `ollama_client` is None, fall back to a deterministic rule: the plan
        is complete iff every subtask is `done` *and* `verifier_passed` is True.
        Otherwise the supplied client is asked to read the results and judge.
        """
        # Deterministic fallback.
        if ollama_client is None:
            missing = [
                s.id
                for s in plan.subtasks
                if s.status != "done" or s.verifier_passed is not True
            ]
            complete = len(missing) == 0
            reasoning = (
                "deterministic: all subtasks done and verifier_passed"
                if complete
                else f"deterministic: {len(missing)} subtask(s) not verified done"
            )
            return {"complete": complete, "reasoning": reasoning, "missing": missing}

        # LLM judge path — tolerant of differing client shapes.
        # Use the SMALL model — judging plan completion is a simple
        # classification task (are all subtasks done?) that doesn't need
        # the big model's reasoning power. Saves cloud tokens.
        try:
            from llm_client import get_small_client_or_big

            _judge_client = get_small_client_or_big()
            prompt = self._build_judge_prompt(plan)
            # Prefer a .chat/.generate style method if present.
            if hasattr(_judge_client, "chat"):
                response = _judge_client.chat(prompt)  # type: ignore[attr-defined]
            elif hasattr(_judge_client, "generate"):
                response = _judge_client.generate(prompt)  # type: ignore[attr-defined]
            elif callable(_judge_client):
                response = _judge_client(prompt)
            else:
                raise TypeError("judge client has no usable interface")
            return self._parse_judge_response(response, plan)
        except Exception as exc:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            # If the judge itself fails, fall back to the deterministic rule
            # rather than letting the loop die.
            self._log(plan, "warn", f"judge LLM failed ({exc}); using fallback")
            return self.judge(plan, ollama_client=None)

    def _build_judge_prompt(self, plan: Plan) -> str:
        lines = [
            "You are a strict judge evaluating whether a plan has been completed.",
            "Goal: " + plan.goal,
            "",
            "Subtask results:",
        ]
        for s in plan.subtasks:
            lines.append(
                f"- id={s.id} status={s.status} verifier_passed="
                f"{s.verifier_passed} intent={s.intent!r}"
            )
            if s.error:
                lines.append(f"    error: {s.error}")
        lines += [
            "",
            "Return ONLY a JSON object:",
            '{"complete": <bool>, "reasoning": "<str>", "missing": ["<id>", ...]}',
        ]
        return "\n".join(lines)

    def _parse_judge_response(self, response: Any, plan: Plan) -> dict:
        """Best-effort parse of an LLM judge response into the judge schema."""
        text = response if isinstance(response, str) else str(response)
        # Try to locate a JSON object in the response.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                obj = json.loads(text[start : end + 1])
                complete = bool(obj.get("complete", False))
                reasoning = str(obj.get("reasoning", ""))
                missing = obj.get("missing", [])
                if not isinstance(missing, list):
                    missing = list(missing)
                return {
                    "complete": complete,
                    "reasoning": reasoning,
                    "missing": [str(m) for m in missing],
                }
            except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                pass
        # Parse failed — fall back.
        return self.judge(plan, ollama_client=None)
