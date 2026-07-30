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
import json
from pathlib import Path
from typing import Any

from services import Services


async def send_progress(svc: Services, websocket, stage: str,
                        detail: dict[str, Any] | None = None) -> None:
    """Send a structured progress event to the live UI."""
    try:
        await svc.manager.send_personal_message(
            json.dumps({"type": "progress", "stage": stage,
                         "detail": detail or {}}),
            websocket, session_logger=svc.session_logger)
    except Exception:
        pass


async def heartbeat(svc: Services, websocket, label: str,
                    start_time: float, interval: float = 2.0) -> None:
    """Push a one-shot heartbeat so the UI can render elapsed time + a
    'still alive' pulse. Called periodically by long-running executors."""
    try:
        elapsed = asyncio.get_event_loop().time() - start_time
        await svc.manager.send_personal_message(
            json.dumps({"type": "heartbeat", "label": label,
                         "elapsed_ms": int(elapsed * 1000)}),
            websocket, session_logger=svc.session_logger)
    except Exception:
        pass


async def run_with_heartbeat(svc: Services, websocket, label: str,
                             coro_or_fn, *args, **kwargs) -> Any:
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
        except Exception:
            # Re-raise the real exception from the task.
            return task.result()
    result = task.result()
    await send_progress(svc, websocket, label + "_done", {
        "duration_ms": int((loop.time() - t0) * 1000)})
    return result


def truncate_tool_result(result: Any, max_chars: int = 10000) -> Any:
    """Truncate a tool result so it never overwhelms the conversation.

    Tool results (especially vault_research syntheses, code_read of large
    files, and vault_graph_analyzer dumps) can be 50K+ chars. Appended
    verbatim to the conversation, a single result balloons the payload past
    the compaction threshold, triggering mid-loop compaction that shreds the
    *recent* user/assistant turns to 200-char fragments while leaving the
    bloated result intact — the agent then loses the thread and redoes old
    work. Capping each result to a generous but bounded size (default 10K
    chars, ~2.5K tokens) keeps the agentic loop's context bounded without
    losing the actionable summary the model needs.

    Truncation messages are informative — they report how many chars were
    dropped and from which key, so the model knows what it's missing and can
    re-read with tighter parameters if needed.

    Returns a new object; never mutates the input. Preserves dict structure
    and truncates only string values that exceed a per-key cap, plus an
    overall serialized cap as a last resort.
    """
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
                    capped[k] = v[:per_key] + f"\n[...truncated: {dropped} chars dropped from '{k}' — re-read with narrower parameters if needed...]"
                    truncated_keys.append(k)
                elif isinstance(v, (list, tuple)) and len(json.dumps(v, default=str)) > per_key:
                    dropped = len(json.dumps(v, default=str)) - per_key
                    capped[k] = str(v)[:per_key] + f"\n[...truncated: ~{dropped} chars dropped from '{k}'...]"
                    truncated_keys.append(k)
                else:
                    capped[k] = v
            # Final serialized cap so the whole dict fits.
            s2 = json.dumps(capped, default=str)
            if len(s2) <= max_chars:
                if truncated_keys:
                    capped["_truncation_notice"] = f"Result was truncated. Keys affected: {truncated_keys}. Total original size: {len(serialized)} chars, cap: {max_chars} chars."
                return capped
            dropped = len(s2) - max_chars
            return s2[:max_chars] + f"\n[...truncated: {dropped} chars dropped from overall result — original size was {len(serialized)} chars...]"
        # Non-dict: cap the serialized form.
        dropped = len(serialized) - max_chars
        return serialized[:max_chars] + f"\n[...truncated: {dropped} chars dropped — original size was {len(serialized)} chars...]"
    except Exception:
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
        return (f"{result.get('source_count', 0)} sources, "
                f"{result.get('synthesis_facts', 0)} facts"
                + (f", note: {Path(result['note_path']).stem}"
                   if result.get("note_path") else ""))
    if tool_name == "vault_search":
        return f"{len(result.get('results', []))} notes found"
    if tool_name == "vault_gaps":
        return f"{result.get('count', 0)} gaps found"
    if tool_name == "vaultbot_status":
        st = result
        return ("running" if st.get("running") else "stopped") + \
               f", {st.get('history_count', 0)} cycles"
    if tool_name == "code_read":
        return f"{result.get('total_lines', 0)} lines from {result.get('file_path', '?')}"
    if tool_name == "code_run":
        return f"exit {result.get('exit_code', '?')}: {str(result.get('stdout', ''))[:80]!r}"
    if tool_name == "tool_create":
        return f"{result.get('status', '?')}: {result.get('tool_name', '?')}"
    if tool_name == "self_reflect":
        return f"reflection: {str(result.get('reflection', ''))[:80]!r}"
    if tool_name == "git_rollback":
        return f"restored {result.get('restored', '?')}"
    if tool_name == "safe_write":
        st = result.get("status", "?")
        if st == "written":
            return f"safe_write: wrote {result.get('bytes', 0)} bytes to {result.get('file_path', '?')} (verified)"
        if st == "dry_run_ok":
            return "safe_write dry_run: OK — would write safely"
        return f"safe_write {st}: {str(result.get('error', ''))[:80]}"
    if tool_name == "js_safe_write":
        st = result.get("status", "?")
        if st == "written":
            return f"js_safe_write: wrote {result.get('bytes', 0)} bytes to {result.get('file_path', '?')} (node --check passed)"
        if st == "dry_run_ok":
            return "js_safe_write dry_run: OK — node --check passed"
        return f"js_safe_write {st}: {str(result.get('error', ''))[:80]}"
    if tool_name == "capability_audit":
        return f"{result.get('total', 0)} tools ({result.get('kinds', {})})"
    # Custom tools: try to extract a meaningful key.
    if isinstance(result, dict) and result.get("result"):
        return str(result["result"])[:120]
    return str(result)[:200]