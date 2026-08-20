"""
Chat helpers — truncation, formatting, and UI summaries for tool results.

These helpers were originally module-level functions in main.py
(``_send_progress``, ``_heartbeat``, ``_run_with_heartbeat``,
``_tool_result_summary``) and referenced two module globals — ``manager``
(the websocket ConnectionManager) and ``default_session_logger`` (a
fallback SessionLogger used when a request has no live session logger).

After extraction they no longer see those globals as free variables, so
the three that touch ``manager`` / ``default_session_logger`` accept a
``Services`` instance (see ``services.py``) as their first parameter and
read the singletons off it. ``tool_result_summary`` is pure (only uses
built-ins) and needs no ``svc`` param.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any

from services import Services


async def send_progress(
    svc: Services, websocket, stage: str, detail: dict[str, Any] | None = None
) -> None:
    """Send a structured progress event to the live UI."""
    with contextlib.suppress(Exception):  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        await svc.manager.send_personal_message(
            json.dumps({"type": "progress", "stage": stage, "detail": detail or {}}),
            websocket,
            session_logger=svc.session_logger,
        )


# ───────────────────────────────────────────────────────────────────────────
# User-facing problem / info notification helpers
# ───────────────────────────────────────────────────────────────────────────
# These are the single chokepoint for surfacing failures and degradation
# to the user via the WebSocket.  Every background task crash, every
# graceful-degradation fallback, and every raw-error site should call one
# of these instead of silently swallowing or leaking a raw exception.
#
# Both helpers:
#   - wrap the WS send in try/except (a dead websocket must never cascade)
#   - log to session_logger so the problem is in the JSONL even if the WS
#     is gone
#   - rate-limit: the same user_message within 60s is suppressed so a
#     failing background loop doesn't spam the chat with identical cards

_NOTIFY_DEDUP_WINDOW = 60.0  # seconds
_notify_dedup: dict[str, float] = {}  # user_message -> last-sent epoch


def _should_dedup(key: str) -> bool:
    """True if this exact key was sent within the dedup window."""
    now = time.time()
    last = _notify_dedup.get(key)
    if last is not None and (now - last) < _NOTIFY_DEDUP_WINDOW:
        return True
    # Prune stale entries occasionally so the dict doesn't grow unbounded.
    if len(_notify_dedup) > 100:
        cutoff = now - _NOTIFY_DEDUP_WINDOW
        for k in [k for k, v in _notify_dedup.items() if v <= cutoff]:
            del _notify_dedup[k]
    _notify_dedup[key] = now
    return False


async def notify_problem(
    svc: Services,
    websocket,
    exc_or_diagnosis: Any,
    *,
    context: dict[str, Any] | None = None,
    user_message: str = "",
    remedy_hint: str = "",
) -> None:
    """Send a typed ``type:"problem"`` WS event to the user.

    This is the function every background-task except block and every
    degraded-path fallback should call. It translates a raw exception
    into a ``Diagnosis`` via ``classify_error`` (so the frontend renders
    a remedy card, not a stack trace) or accepts a pre-built ``Diagnosis``.

    Args:
        svc: the Services registry (for manager + session_logger).
        websocket: the live WS connection (may be None for broadcasts —
            use ``notify_problem_broadcast`` instead).
        exc_or_diagnosis: a raw Exception (classified via classify_error)
            or a pre-built ``Diagnosis`` instance.
        context: optional hints passed to ``classify_error`` (e.g.
            ``{"stage": "weaving"}``).
        user_message: optional override for the Diagnosis user_message.
            When non-empty, replaces the classified message so callers
            can provide a more specific human description.
        remedy_hint: optional override for the Diagnosis remedy_hint.

    A dead websocket, a missing manager, or a classification failure never
    raises — the problem is always logged to ``session_logger``.
    """
    from diagnostics import classify_error
    from error_types import Diagnosis as _Diagnosis

    try:
        if isinstance(exc_or_diagnosis, _Diagnosis):
            diag = exc_or_diagnosis
        else:
            diag = classify_error(exc_or_diagnosis, context or {})
        if user_message:
            diag = _Diagnosis(
                category=diag.category,
                user_message=user_message,
                remedy_hint=remedy_hint or diag.remedy_hint,
                action=diag.action,
                severity=diag.severity,
                raw_for_log=diag.raw_for_log,
            )
        elif remedy_hint:
            diag = _Diagnosis(
                category=diag.category,
                user_message=diag.user_message,
                remedy_hint=remedy_hint,
                action=diag.action,
                severity=diag.severity,
                raw_for_log=diag.raw_for_log,
            )

        # Rate-limit: don't spam the same card repeatedly.
        dedup_key = diag.user_message
        if _should_dedup(dedup_key):
            return

        payload = json.dumps({"type": "problem", "diagnosis": diag.to_dict()})
        if websocket is not None and svc.manager is not None:
            await svc.manager.send_personal_message(
                payload, websocket, session_logger=getattr(svc, "session_logger", None)
            )
        # Also log so the problem is traceable even if the WS is dead.
        slog = getattr(svc, "session_logger", None)
        if slog is not None:
            slog.log(
                "problem_notified",
                {
                    "category": diag.category.value,
                    "user_message": diag.user_message,
                    "context": context or {},
                },
            )
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        # This helper must never be the source of a cascade.
        pass


async def notify_info(svc: Services, websocket, message: str) -> None:
    """Send a quiet ``type:"system_info"`` WS event — for degradation
    signals (e.g. "using a simpler search") that are informational, not
    errors.

    The plugin renders these as a low-key bubble. Rate-limited identically
    to ``notify_problem``.
    """
    try:
        if _should_dedup(message):
            return
        payload = json.dumps({"type": "system_info", "content": message})
        if websocket is not None and svc.manager is not None:
            await svc.manager.send_personal_message(
                payload, websocket, session_logger=getattr(svc, "session_logger", None)
            )
        slog = getattr(svc, "session_logger", None)
        if slog is not None:
            slog.log("info_notified", {"message": message})
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        pass


async def notify_console_failure(
    svc: Services,
    websocket,
    message: str,
    *,
    context: str = "",
) -> None:
    """Send a ``type:"console_error"`` WS event — a RED console line only.

    This is the lightweight counterpart to ``notify_problem`` for failures
    that don't break the turn but the user must NOT be blind to: a
    background condense crash, a procedure-tracking log failure, a step-RAG
    retrieval miss, a drift-feedback error. These don't warrant a full
    problem card (the turn still succeeds), but per the operator's
    directive ("any failure of any kind is immediately reported to the user
    in the console") they appear as a red line in the chat console so the
    user knows degradation happened.

    The plugin renders ``type:"console_error"`` as a single red console
    line (no card, no turn interruption). Rate-limited per message so a
    failing background loop doesn't flood the console.

    Args:
        svc: the Services registry (for manager + session_logger).
        websocket: the live WS connection (may be None — then logged only).
        message: the human-readable failure description.
        context: optional short tag (e.g. "lazy_condense") included in the
            console line for traceability.
    """
    try:
        dedup_key = message
        if _should_dedup(dedup_key):
            return
        line = f"[{context}] {message}" if context else message
        payload = json.dumps({"type": "console_error", "content": line})
        if websocket is not None and svc.manager is not None:
            await svc.manager.send_personal_message(
                payload, websocket, session_logger=getattr(svc, "session_logger", None)
            )
        slog = getattr(svc, "session_logger", None)
        if slog is not None:
            slog.log(
                "console_failure_notified", {"message": message, "context": context}
            )
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        pass


def notify_problem_broadcast(
    svc: Services,
    exc_or_diagnosis: Any,
    *,
    context: dict[str, Any] | None = None,
    user_message: str = "",
    remedy_hint: str = "",
) -> None:
    """Synchronous broadcast variant for non-async contexts (daemon threads).

    Schedules ``notify_problem`` on all active websocket connections via
    ``manager.broadcast``. Use from background threads that don't own the
    event loop (e.g. the autonomous researcher's crash callback).
    """
    from diagnostics import classify_error
    from error_types import Diagnosis as _Diagnosis

    try:
        if isinstance(exc_or_diagnosis, _Diagnosis):
            diag = exc_or_diagnosis
        else:
            diag = classify_error(exc_or_diagnosis, context or {})
        if user_message:
            diag = _Diagnosis(
                category=diag.category,
                user_message=user_message,
                remedy_hint=remedy_hint or diag.remedy_hint,
                action=diag.action,
                severity=diag.severity,
                raw_for_log=diag.raw_for_log,
            )
        elif remedy_hint:
            diag = _Diagnosis(
                category=diag.category,
                user_message=diag.user_message,
                remedy_hint=remedy_hint,
                action=diag.action,
                severity=diag.severity,
                raw_for_log=diag.raw_for_log,
            )

        dedup_key = diag.user_message
        if _should_dedup(dedup_key):
            return

        payload = json.dumps({"type": "problem", "diagnosis": diag.to_dict()})
        manager = getattr(svc, "manager", None)
        if manager is not None:
            loop = getattr(svc, "_main_loop", None)
            if loop is not None and loop.is_running():
                asyncio.run_coroutine_threadsafe(manager.broadcast(payload), loop)
            elif manager.active_connections:
                # No loop reference — try a fire-and-forget via a new loop.
                # This is a last resort; the normal path is via _main_loop.
                pass
        slog = getattr(svc, "session_logger", None)
        if slog is not None:
            slog.log(
                "problem_notified",
                {
                    "category": diag.category.value,
                    "user_message": diag.user_message,
                    "context": context or {},
                    "broadcast": True,
                },
            )
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        pass


async def heartbeat(
    svc: Services, websocket, label: str, start_time: float, interval: float = 2.0
) -> None:
    """Push a one-shot heartbeat so the UI can render elapsed time + a
    'still alive' pulse. Called periodically by long-running executors."""
    try:
        elapsed = asyncio.get_event_loop().time() - start_time
        await svc.manager.send_personal_message(
            json.dumps(
                {"type": "heartbeat", "label": label, "elapsed_ms": int(elapsed * 1000)}
            ),
            websocket,
            session_logger=svc.session_logger,
        )
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        pass


async def run_with_heartbeat(
    svc: Services, websocket, label: str, coro_or_fn, *args, **kwargs
) -> Any:
    """Run a blocking call in an executor while emitting heartbeats so the
    user is never staring at a frozen 'Calling X...' line.

    `coro_or_fn` is a plain callable (run in the default executor). Heartbeats
    fire every `interval` seconds with the elapsed time, and a final
    progress event fires when the call returns."""
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    interval = kwargs.pop("interval", 2.0)
    task = loop.run_in_executor(None, lambda: coro_or_fn(*args, **kwargs))
    while not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=interval)
        except TimeoutError:
            await heartbeat(svc, websocket, label, t0, interval)
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            # Re-raise the real exception from the task.
            return task.result()
    result = task.result()
    await send_progress(
        svc, websocket, label + "_done", {"duration_ms": int((loop.time() - t0) * 1000)}
    )
    return result


def truncate_tool_result(result: Any, max_chars: int = 0) -> Any:
    """Truncate a tool result so it never overwhelms the conversation.

    Tool results (especially vault_research syntheses, code_read of large
    files, and vault_graph_analyzer dumps) can be 50K+ chars. Appended
    verbatim to the conversation, a single result can push the payload
    past the sliding window boundary, causing older messages to be dropped
    — potentially losing the *recent* user/assistant turns while leaving
    the bloated result intact. Capping each result to a generous but
    bounded size keeps the agentic loop's context bounded without losing
    the actionable summary the model needs.

    The cap is configurable via VAULTBOT_TOOL_RESULT_CAP (default 10000).
    Truncation messages are informative — they report how many chars were
    dropped and from which key, so the model knows what it's missing and can
    re-read with tighter parameters if needed.

    Returns a new object; never mutates the input. Preserves dict structure
    and truncates only string values that exceed a per-key cap, plus an
    overall serialized cap as a last resort.
    """
    if max_chars <= 0:
        max_chars = int(os.getenv("VAULTBOT_TOOL_RESULT_CAP", "10000"))
    try:
        serialized = json.dumps(result, default=str)
        if len(serialized) <= max_chars:
            return result
        # Per-key truncation for dict results: keep keys, cap long string
        # values. This preserves the structure the model reasons over.
        if isinstance(result, dict):
            capped: dict[str, Any] = {}
            per_key = max(1000, max_chars // max(1, len(result)))
            truncated_keys: list[str] = []
            for k, v in result.items():
                if isinstance(v, str) and len(v) > per_key:
                    dropped = len(v) - per_key
                    capped[k] = (
                        v[:per_key]
                        + f"\n[...truncated: {dropped} chars dropped from '{k}' — "
                        "re-read with narrower parameters if needed...]"
                    )
                    truncated_keys.append(k)
                elif (
                    isinstance(v, (list, tuple))
                    and len(json.dumps(v, default=str)) > per_key
                ):
                    dropped = len(json.dumps(v, default=str)) - per_key
                    capped[k] = (
                        str(v)[:per_key]
                        + f"\n[...truncated: ~{dropped} chars dropped from '{k}'...]"
                    )
                    truncated_keys.append(k)
                else:
                    capped[k] = v
            # Final serialized cap so the whole dict fits.
            s2 = json.dumps(capped, default=str)
            if len(s2) <= max_chars:
                if truncated_keys:
                    capped["_truncation_notice"] = (
                        f"Result was truncated. Keys affected: {truncated_keys}. "
                        f"Total original size: {len(serialized)} chars, "
                        f"cap: {max_chars} chars."
                    )
                return capped
            dropped = len(s2) - max_chars
            return (
                s2[:max_chars]
                + f"\n[...truncated: {dropped} chars dropped from overall result "
                f"— original size was {len(serialized)} chars...]"
            )
        # Non-dict: cap the serialized form.
        dropped = len(serialized) - max_chars
        return (
            serialized[:max_chars]
            + f"\n[...truncated: {dropped} chars dropped — original size was "
            f"{len(serialized)} chars...]"
        )
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        return str(result)[:max_chars]


def tool_result_summary(tool_name: str, result: Any) -> str:
    """Human-readable one-line summary of a tool result for the UI.

    NEVER returns None — every branch returns a string so callers that
    slice the result (``summary[:200]``) can't crash with
    ``'NoneType' object is not subscriptable`` when a tool returns a dict
    the per-tool branches below don't explicitly handle.
    """
    if not isinstance(result, dict):
        return str(result)[:200]
    if result.get("error"):
        return f"error: {str(result['error'])[:150]}"
    if tool_name == "vault_research":
        return (
            f"{result.get('source_count', 0)} sources, "
            f"{result.get('synthesis_facts', 0)} facts"
            + (
                f", note: {Path(result['note_path']).stem}"
                if result.get("note_path")
                else ""
            )
        )
    if tool_name == "vault_search":
        return f"{len(result.get('results', []))} notes found"
    if tool_name == "vault_gaps":
        return f"{result.get('count', 0)} gaps found"
    if tool_name == "vaultbot_status":
        st = result
        return (
            "running" if st.get("running") else "stopped"
        ) + f", {st.get('history_count', 0)} cycles"
    if tool_name == "code_read":
        return (
            f"{result.get('total_lines', 0)} lines from {result.get('file_path', '?')}"
        )
    if tool_name == "code_run":
        return (
            f"exit {result.get('exit_code', '?')}: "
            f"{str(result.get('stdout', ''))[:80]!r}"
        )
    if tool_name == "tool_create":
        return f"{result.get('status', '?')}: {result.get('tool_name', '?')}"
    if tool_name == "self_reflect":
        return f"reflection: {str(result.get('reflection', ''))[:80]!r}"
    if tool_name == "git_rollback":
        return f"restored {result.get('restored', '?')}"
    if tool_name == "safe_write":
        st = result.get("status", "?")
        if st == "written":
            return (
                f"safe_write: wrote {result.get('bytes', 0)} bytes to "
                f"{result.get('file_path', '?')} (verified)"
            )
        if st == "dry_run_ok":
            return "safe_write dry_run: OK — would write safely"
        return f"safe_write {st}: {str(result.get('error', ''))[:80]}"
    if tool_name == "js_safe_write":
        st = result.get("status", "?")
        if st == "written":
            return (
                f"js_safe_write: wrote {result.get('bytes', 0)} bytes to "
                f"{result.get('file_path', '?')} (node --check passed)"
            )
        if st == "dry_run_ok":
            return "js_safe_write dry_run: OK — node --check passed"
        return f"js_safe_write {st}: {str(result.get('error', ''))[:80]}"
    if tool_name == "capability_audit":
        return f"{result.get('total', 0)} tools ({result.get('kinds', {})})"
    # Custom tools: try to extract a meaningful key.
    if isinstance(result, dict) and result.get("result"):
        return str(result["result"])[:120]
    return str(result)[:200]
