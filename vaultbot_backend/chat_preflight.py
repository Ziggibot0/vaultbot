"""Pre-LLM routing layer for the agentic chat loop.

Extracted from ``chat_handler.py`` — these functions run BEFORE the agentic
loop starts. They decide whether to shortcut (trivial-turn classifier), how
to pre-route (deterministic + small-model procedure hints), and execute
procedures directly from the framework (preflight chain steps).

Also includes ``_check_cancelled`` — called at every phase boundary so the
stop button works at ANY point in the agentic loop, not just at await points.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import WebSocket
from procedure_surface import status_allows_execution
from services import Services

# ---------------------------------------------------------------------------
# Cancel check — called at every phase boundary so the stop button works
# at ANY point in the agentic loop, not just at await points.
# ---------------------------------------------------------------------------


def check_cancelled(websocket: WebSocket) -> None:
    """Raise CancelledError if the user pressed Stop.

    task.cancel() only raises CancelledError at await points. This function
    is called at every phase boundary (before/after tool execution, between
    rounds, etc.) so the stop button interrupts the loop immediately even
    during long sync operations.
    """
    if getattr(websocket, "_cancelled", False):
        raise asyncio.CancelledError("user stopped")


# ---------------------------------------------------------------------------
# Deterministic procedure routing hint
# ---------------------------------------------------------------------------


def deterministic_procedure_hint(
    results: list[dict],
    proc_index: dict[str, dict[str, Any]] | None,
    user_message: str,
) -> str:
    """Deterministically pick the best-matching procedure for the query.

    Replaces the small-model procedure-routing hint. FUSED retrieval already
    ranked these same procedure notes by embedding + graph + backlink
    similarity to the query, so the best hint is the highest-scored surfaced
    procedure that is executable (not flagged). This reuses a score already
    computed — zero LLM calls, zero new embeddings, never worse than the
    small model's pick (it was choosing from the same surface set).

    Skipped for trivial/greeting messages (no procedure is the right answer
    there) and for flagged procedures (cannot run).
    """
    if not results:
        return ""
    # Skip very short messages — no procedure is the right hint for a bare
    # greeting or fragment. (No lexical keyword list: FUSED retrieval and the
    # model decide relevance, not literal string matching.)
    _msg_low = user_message.strip().lower()
    if len(_msg_low) < 5:
        return ""

    _best_stem = ""
    _best_score = -1.0
    for r in results:
        if not isinstance(r, dict):
            continue
        fp = r.get("file_path", "")
        if not fp:
            continue
        stem = Path(fp).stem
        # Only consider actual procedures — the proc_index is the
        # authoritative map of stem -> {path, frontmatter}. A non-procedure
        # note with a higher FUSED score must not be hinted as a procedure.
        if not proc_index or stem not in proc_index:
            continue
        fm = proc_index[stem].get("frontmatter") or {}
        status = (fm.get("status") or "").strip().lower()
        # Skip flagged procedures — they're blocked from execution.
        if status == "flagged":
            continue
        score = float(r.get("score", 0.0) or 0.0)
        if score > _best_score:
            _best_score = score
            _best_stem = stem

    # Require a minimum similarity so we don't hint a procedure for a query
    # it's only weakly related to (the small model returned 'none' in that
    # case; we mirror that here). FUSED scores are normalized to [0,1].
    if _best_stem and _best_score >= 0.20:
        return _best_stem
    return ""


# ---------------------------------------------------------------------------
# Small-model procedure hint (LLM-based fallback)
# ---------------------------------------------------------------------------


def small_model_procedure_hint(
    user_message: str,
    procedure_lines: list[str],
    session_logger: Any = None,
) -> str:
    """Use the small model to pre-classify which procedures match the task.

    Given the user message and the procedure surface lines (name + description
    + status), the small model picks the best-matching procedure name. The
    framework validates that the returned name exists in the real procedure
    list — a hallucinated name is silently dropped. The big model still makes
    the final execute_procedure call; this is just a hint that reduces its
    reasoning load (it doesn't have to read and evaluate each procedure line).
    """
    if not procedure_lines:
        return ""
    try:
        from llm_client import get_small_client_or_big

        client = get_small_client_or_big(session_logger)
        # Extract just the procedure names from the surface lines for the
        # prompt so the small model picks from a known set.
        names = []
        for line in procedure_lines:
            # Surface lines look like "- Verify-Claims — desc [status]"
            stripped = line.lstrip("- ").strip()
            if stripped:
                name = stripped.split("—")[0].split("[")[0].strip()
                if name:
                    names.append(name)
        if not names:
            return ""

        prompt = (
            "Given the user's message and a list of procedures, determine "
            "which procedure (if any) is most relevant. Output ONLY the "
            "procedure name, or 'none' if none are relevant.\n\n"
            f"User message: {user_message[:300]}\n\n"
            f"Procedures:\n" + "\n".join(f"- {n}" for n in names) + "\n\n"
            "Most relevant procedure:"
        )
        resp = client.chat(
            [{"role": "user", "content": prompt}], temperature=0.1, stream=False
        )
        text = ""
        if isinstance(resp, dict):
            msg = resp.get("message", {})
            if isinstance(msg, dict):
                text = msg.get("content", "") or ""
            if not text:
                text = resp.get("response", "") or resp.get("content", "")
        text = (text or "").strip().split("\n")[0].strip()
        # Guard: only accept a name that's in the real procedure list.
        if text and text.lower() != "none":
            _text_low = text.lower()
            for n in names:
                if n.lower() == _text_low:
                    return n  # return the real-cased name
            # No exact match — hallucinated name. Drop it.
            if session_logger:
                session_logger.log(
                    "procedure_hint_hallucinated",
                    {
                        "returned": text,
                        "real_names": names,
                    },
                )
        return ""
    except Exception:  # noqa: BLE001 — best-effort, returns "" on any error
        if session_logger:
            session_logger.log(
                "procedure_hint_error", {"error": "exception in _match_procedure"}
            )
        return ""


# ---------------------------------------------------------------------------
# Small-model query helper (deterministic — no LLM call)
# ---------------------------------------------------------------------------


def small_model_query(goal: str, step_content: str, session_logger: Any = None) -> str:
    """Deterministically build a search query from the step description.

    Replaces the old small-model call that turned a step description into a
    search query. The small model was just rephrasing the step text — a
    bounded rewrite that doesn't need LLM reasoning. The FUSED retriever
    already does keyword extraction and topic focusing internally, so it
    doesn't need a pre-rephrased query. The raw step text (prefixed with the
    goal for context) is a better query because it preserves specific terms
    the small model would often drop.

    Zero LLM calls. The old fallback path (when the small model was
    unavailable) was literally this — ``(goal + " " + step_content).strip()``
    — and it worked fine.
    """
    query = (goal + " " + step_content).strip()
    if session_logger:
        session_logger.log(
            "deterministic_step_query",
            {
                "query": query[:120],
            },
        )
    return query


# ---------------------------------------------------------------------------
# Small-model digest (compress non-code tool results)
# ---------------------------------------------------------------------------


def small_model_digest(
    result: dict[str, Any], session_logger: Any = None
) -> dict[str, Any]:
    """Use the small model to digest a non-code tool result into a compact summary.

    Extends digest_code_read (which only handles .py files) to any tool result.
    The small model writes a 2-3 sentence structural summary: what the file
    contains, key sections, and what the agent should know. Hallucination
    guard: every word in the summary must appear in the source content. If
    the summary fails the guard, fall back to a deterministic truncation.
    """
    content = result.get("content", "")
    if not isinstance(content, str) or len(content) < 200:
        return result  # too short to need digesting

    from small_model_filters import _breaker_reset, _breaker_trip, _breaker_tripped

    if _breaker_tripped("digest"):
        return result

    try:
        from llm_client import get_small_client_or_big

        client = get_small_client_or_big(session_logger)
        file_path = result.get("file_path", "?")
        prompt = (
            "Summarize the following content in 2-3 sentences. "
            "State what it is, its key sections, and the main points. "
            "Use ONLY words that appear in the source — do NOT add "
            "information that isn't in the text below.\n\n"
            f"File: {file_path}\n\n"
            f"Content:\n{content[:6000]}\n\nSummary:"
        )
        resp = client.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
            stream=False,
            think=False,
            max_predict=256,
        )
        text = ""
        if isinstance(resp, dict):
            msg = resp.get("message", {})
            if isinstance(msg, dict):
                text = msg.get("content", "") or ""
            if not text:
                text = resp.get("response", "") or resp.get("content", "")
        text = (text or "").strip()

        if not text or len(text) < 20:
            return result

        # Hallucination guard: check that the summary's content words all
        # appear in the source. If the small model invented facts, the word
        # overlap will be low and we fall back to deterministic truncation.
        _src_words = set(content.lower().split())
        _sum_words = set(text.lower().split())
        _stop = {
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "be",
            "to",
            "of",
            "in",
            "on",
            "at",
            "and",
            "or",
            "it",
            "this",
            "that",
            "for",
            "with",
            "as",
            "by",
            "its",
            "has",
            "have",
            "from",
            "which",
            "not",
            "but",
            "can",
            "will",
            "do",
            "does",
            "did",
            "you",
            "your",
            "we",
            "our",
            "they",
            "their",
            "he",
            "she",
        }
        _content_words = _sum_words - _stop
        if _content_words:
            _overlap = len(_content_words & _src_words) / len(_content_words)
            if _overlap < 0.6:
                if session_logger:
                    session_logger.log(
                        "small_model_digest_hallucination",
                        {
                            "file_path": file_path,
                            "overlap": round(_overlap, 2),
                        },
                    )
                return result  # fall back — don't use the summary

        new = dict(result)
        new["content"] = (
            f"[DIGESTED by small model — full body omitted to protect "
            f"reasoning budget]\n{text}\n\n"
            f"The raw body is NOT included. If you need specific details, "
            f"call the tool again with narrower parameters."
        )
        new["digested"] = True
        new["original_chars"] = len(content)
        _breaker_reset("digest")
        return new
    except Exception:  # noqa: BLE001 — fall back to raw result on any error
        _breaker_trip("digest")
        return result  # fall back to raw result on any error


# ---------------------------------------------------------------------------
# Framework-forced procedure execution (preflight routing)
# ---------------------------------------------------------------------------
# The framework runs Route-Task BEFORE the big model sees the conversation,
# then auto-executes small-cartridge chain steps. The big model becomes a
# step-worker that receives pre-computed results and only handles big-cartridge
# procedures + final synthesis. This removes the "decide what to do" cognitive
# load from the big model — a local LLM can follow a chain much more reliably
# than it can read a 1000-word prompt and self-route.


# ---------------------------------------------------------------------------
# Shared procedure dispatch core
# ---------------------------------------------------------------------------
# Both run_procedure_direct (preflight framework path) and the
# execute_procedure tool handler (model-driven path) share the same core:
# file resolution → status gate → compile → cartridge selection →
# tracker log forwarding → execute_procedure call.  This function
# encapsulates that core so the two paths can't drift apart.  Callers
# keep their own progress callbacks, drift feedback, return-dict shape,
# and logging — those are the parts that legitimately differ.


async def dispatch_procedure_core(
    svc: Services,
    proc_name: str,
    proc_args: dict[str, Any] | None = None,
    session_logger: Any = None,
    progress_callback: Any = None,
) -> dict[str, Any]:
    """Shared core for procedure execution from the chat handler.

    Resolves the procedure file, checks the status gate, compiles it,
    selects the LLM cartridge, forwards the tracker log path, and calls
    ``execute_procedure``.  Returns a dict with either ``{"error": ...}``
    on failure or the ExecutionResult fields + cartridge on success.

    Callers are responsible for:
    - Building their own progress_callback (different payload shapes).
    - Post-execution drift feedback.
    - Shaping the return dict for their specific caller (tool result vs
      preflight result).
    - Extra logging (procedure_cartridge, procedure_result_full, etc.).
    """
    from paths import VAULT_ROOT
    from procedure_compiler import compile_procedure as _compile_proc
    from step_gate_runtime import execute_procedure as _run_proc

    vault_root = VAULT_ROOT

    # --- Resolve via stem index (O(1)) with refresh-on-miss + rglob fallback ---
    proc_file = None
    try:
        idx = getattr(svc.procedure_tracker, "_stem_index", None)
        if idx is None:
            idx = svc.procedure_tracker.get_procedure_index(str(vault_root))
            svc.procedure_tracker._stem_index = idx
        entry = idx.get(proc_name)
        if entry:
            proc_file = Path(entry["path"])
        else:
            idx = svc.procedure_tracker.refresh_procedure_index(str(vault_root))
            entry = idx.get(proc_name)
            if entry:
                proc_file = Path(entry["path"])
    except Exception:  # noqa: BLE001 — best-effort; rglob fallback below
        pass

    if not proc_file:
        for candidate in vault_root.rglob("*.md"):
            if candidate.stem == proc_name:
                proc_file = candidate
                break

    if not proc_file:
        return {"error": f"procedure not found: {proc_name}"}

    # --- Status gate: flagged procedures are blocked ---
    _proc_caution = False
    try:
        _idx = getattr(svc.procedure_tracker, "_stem_index", None) or {}
        _entry = _idx.get(proc_name) or {}
        _status = str((_entry.get("frontmatter") or {}).get("status", ""))
        _allowed, _gate_reason = status_allows_execution(_status)
        if not _allowed:
            return {
                "error": f"procedure blocked: {proc_name}",
                "status": _status,
                "blocked": True,
            }
        _proc_caution = _gate_reason == "experimental"
    except Exception:  # noqa: BLE001 — best-effort; gate failure must not block execution
        pass

    # --- Compile ---
    proc = _compile_proc(str(proc_file))
    if not proc:
        return {"error": f"not a procedure note: {proc_name}"}

    # --- Cartridge selection ---
    _cartridge = getattr(proc, "model_cartridge", "big") or "big"
    _proc_llm_client = svc.ollama_client
    try:
        if _cartridge == "small":
            from llm_client import get_small_client

            _small = get_small_client(session_logger)
            if _small is not None:
                _proc_llm_client = _small
        elif _cartridge == "vision":
            from llm_client import get_vision_client

            _vision = get_vision_client(session_logger)
            if _vision is not None:
                _proc_llm_client = _vision
    except Exception:  # noqa: BLE001 — best-effort; fall back to big cartridge
        pass

    # --- Forward tracker log path for sub-procedure grading ---
    try:
        _tracker_log_path = str(svc.procedure_tracker.log_path)
        if _tracker_log_path:
            os.environ["PROCEDURE_TRACKER_LOG"] = _tracker_log_path
    except Exception:  # noqa: BLE001 — best-effort; sub-procedure logging is a bonus
        pass

    # --- Execute ---
    result = await _run_proc(
        procedure=proc,
        context="",
        llm_client=_proc_llm_client,
        vault_path=str(vault_root),
        procedure_tracker=svc.procedure_tracker,
        progress_callback=progress_callback,
        procedure_args=proc_args or {},
    )

    return {
        "result": result,
        "proc_file": proc_file,
        "cartridge": _cartridge,
        "proc_caution": _proc_caution,
        "vault_root": vault_root,
    }


async def run_procedure_direct(
    svc: Services,
    proc_name: str,
    proc_args: dict[str, Any] | None = None,
    session_logger: Any = None,
    user_message: str = "",
    websocket: Any = None,
) -> dict[str, Any]:
    """Run a procedure directly from the framework (not from a model tool call).

    Used in the preflight to run Route-Task and auto-execute small-cartridge
    chain steps before the big model ever sees the conversation. Returns a
    dict with procedure, overall_passed, final_output, cartridge, etc.
    On any error, returns {"error": ...} — the caller handles fallback.

    When ``websocket`` is provided, per-step progress events are streamed
    to the UI so the user can see what the procedure is doing in real time
    (instead of staring at a frozen "checking premises..." line with no
    GPU activity indicator).
    """

    # Build a progress callback that streams per-step visibility to the UI.
    # Without this, preflight procedures are a black box — the user sees
    # "checking premises" and then 30-60s of silence with no GPU activity
    # indicator, leaving them guessing whether the bot is working or hung.
    async def _proc_progress(
        step_num: int,
        total: int,
        output: str,
        instruction: str,
        step_type: str,
        status: str,
        input_preview: str = "",
        elapsed_s: float | None = None,
        error: str = "",
    ) -> None:
        if websocket is None:
            return
        try:
            _payload: dict[str, Any] = {
                "type": "procedure_step",
                "procedure": proc_name,
                "step": step_num,
                "total": total,
                "instruction": instruction[:200],
                "step_type": step_type,
                "status": status,
                "timestamp": time.time(),
            }
            if output:
                _payload["output_preview"] = output[:500]
            if input_preview:
                _payload["input_preview"] = input_preview[:500]
            if elapsed_s is not None:
                _payload["elapsed_s"] = elapsed_s
            if error:
                _payload["error"] = error[:500]
            await svc.manager.send_personal_message(
                json.dumps(_payload),
                websocket,
                session_logger=session_logger,
            )
        except Exception:  # noqa: BLE001 — best-effort UI; must not break procedure execution
            pass

    # --- Shared core: resolve, gate, compile, cartridge, execute ---
    core = await dispatch_procedure_core(
        svc,
        proc_name,
        proc_args=proc_args,
        session_logger=session_logger,
        progress_callback=_proc_progress if websocket else None,
    )
    if "error" in core:
        return core

    result = core["result"]
    proc_file = core["proc_file"]
    _cartridge = core["cartridge"]

    # Drift feedback: nudge the procedure embedding toward/away from the query.
    if user_message:
        try:
            _loop = asyncio.get_event_loop()
            q_emb = await _loop.run_in_executor(
                None, svc.vault_indexer._get_embedding, user_message
            )
            svc.embedding_drift.record_feedback(
                str(proc_file), q_emb, helpful=result.overall_passed
            )
        except Exception:  # noqa: BLE001 — best-effort; drift feedback is a bonus
            pass

    return {
        "procedure": proc_name,
        "overall_passed": result.overall_passed,
        "failed_step": result.failed_step,
        "steps_executed": len(result.steps),
        "final_output": result.final_output,
        "child_procedures": result.child_procedures,
        "cartridge": _cartridge,
    }
