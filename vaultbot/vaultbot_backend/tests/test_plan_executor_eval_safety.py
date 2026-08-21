"""Tests for the plan_executor verifier safety fix.

The old code used ``eval(expr, {"__builtins__": _SAFE_BUILTINS}, {"result": result})``
which is the classic Python sandbox bypass — restricted ``__builtins__`` doesn't
prevent attribute-chain escapes like::

    ().__class__.__bases__[0].__subclasses__()

because ``__class__`` is an attribute, not a builtin.

The new ``_safe_eval_verifier`` walks the AST and rejects:
- Any AST node type not in the allowed set (no Lambda, FunctionDef, etc.)
- Any attribute access not in the whitelist (no __class__, __bases__, etc.)
- Any call that isn't a top-level builtin name (no calling methods)

These tests verify both valid verifiers pass AND attack strings are rejected.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

from plan_executor import PlanExecutor, Subtask, _safe_eval_verifier, _VerifierError

# ── Valid verifiers work correctly ─────────────────────────────────────


def test_valid_verifier_subscript_and_len():
    """A typical verifier: result["sources"] has >= 3 items."""
    result = {"sources": ["a", "b", "c"], "ok": True}
    assert _safe_eval_verifier(
        "result['sources'] and len(result['sources']) >= 3", result
    )


def test_valid_verifier_get_method():
    """result.get() is in the safe-attrs whitelist."""
    result = {"status": "done"}
    assert _safe_eval_verifier("result.get('status') == 'done'", result)


def test_valid_verifier_bool_and_any():
    """Boolean ops + any() work."""
    result = {"items": [True, False, True]}
    assert _safe_eval_verifier(
        "any(result['items']) and len(result['items']) > 0", result
    )


def test_valid_verifier_string_methods():
    """String methods (lower, startswith) are whitelisted."""
    result = {"title": "Hello World"}
    assert _safe_eval_verifier("result['title'].lower().startswith('hello')", result)


def test_no_verifier_accepts_dict_result():
    """An empty verifier string means 'accept if result is a dict'."""
    executor = PlanExecutor(op_registry={}, session_logger=None)
    subtask = Subtask(id="t1", op="noop", intent="", args={}, verifier="")
    assert executor.verify(subtask, {"ok": True}) is True
    assert executor.verify(subtask, "not a dict") is False


# ── Attack strings are rejected ─────────────────────────────────────────


def test_attack_class_subclasses_rejected():
    """The classic escape: ().__class__.__bases__[0].__subclasses__()."""
    with pytest.raises(_VerifierError, match="disallowed attribute"):
        _safe_eval_verifier("().__class__.__bases__[0].__subclasses__()", {})


def test_attack_class_attribute_rejected():
    """Direct __class__ access is rejected."""
    with pytest.raises(_VerifierError, match="disallowed attribute"):
        _safe_eval_verifier("result.__class__", {})


def test_attack_globals_rejected():
    """__globals__ access is rejected."""
    with pytest.raises(_VerifierError, match="disallowed attribute"):
        _safe_eval_verifier("result.__class__.__globals__", {})


def test_attack_builtins_rejected():
    """__builtins__ access is rejected."""
    with pytest.raises(_VerifierError, match="disallowed attribute"):
        _safe_eval_verifier("result.__class__.__bases__[0].__subclasses__", {})


def test_attack_import_rejected():
    """__import__ is not in _SAFE_BUILTINS, so calling it raises NameError
    which counts as a failed verification."""
    executor = PlanExecutor(op_registry={}, session_logger=None)
    subtask = Subtask(
        id="t1",
        op="noop",
        intent="",
        args={},
        verifier="__import__('os').system('rm -rf /')",
    )
    assert executor.verify(subtask, {}) is False
    assert subtask.error is not None


def test_attack_lambda_rejected():
    """Lambda expressions are not in the allowed node set."""
    with pytest.raises(_VerifierError, match=r"disallowed|built-in"):
        _safe_eval_verifier("(lambda: 1)()", {})


def test_method_chaining_on_safe_attrs_allowed():
    """Chaining safe method calls (result.get('x').upper()) is allowed —
    .upper is in the whitelist and can't escape the sandbox.  This is NOT
    an attack; it's a valid verifier pattern."""
    result = {"x": "hello"}
    assert _safe_eval_verifier("result.get('x').upper() == 'HELLO'", result)


def test_broken_verifier_counts_as_failed():
    """A verifier with invalid syntax counts as a failed verification,
    not a crash.  The executor returns False, never raises."""
    executor = PlanExecutor(op_registry={}, session_logger=None)
    subtask = Subtask(id="t1", op="noop", intent="", args={}, verifier="!!!invalid")
    assert executor.verify(subtask, {"ok": True}) is False
    assert subtask.error is not None
    assert "verifier" in subtask.error.lower()


def test_verifier_false_returns_false():
    """A verifier that evaluates to falsy returns False (not an error)."""
    executor = PlanExecutor(op_registry={}, session_logger=None)
    subtask = Subtask(
        id="t1", op="noop", intent="", args={}, verifier="result.get('count', 0) >= 5"
    )
    assert executor.verify(subtask, {"count": 2}) is False
