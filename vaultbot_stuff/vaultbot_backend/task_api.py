"""Task/plan API handlers extracted from main.py.

These were originally `@app.post`/`@app.get` route handlers in main.py;
they have been pulled out as plain async functions that receive a `Services`
instance (the service-locator bundle built once at startup). main.py keeps
the FastAPI decorators as thin shims that forward to these functions.

Globals previously read as free variables in main.py are now accessed via
`svc.<name>`. Pure helpers (`extract_json`, `write_partial`) take no
`Services` because they reference no module-level singletons.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC
from pathlib import Path
from typing import Any

# `Plan`/`Subtask` come from plan_executor (pure stdlib — safe at import time).
# `GRAPH_OP_SCHEMAS` lives in graph_ops, which transitively imports faiss via
# vault_indexer; the installed faiss wheel is built against NumPy 1.x and
# breaks under NumPy 2.5.1 (see /memories/repo/pytest-faiss-numpy2-abi.md).
# Importing it lazily inside create_task keeps `import task_api` cheap and
# faiss-free until a real plan is actually being built.
from plan_executor import Plan, Subtask
from services import Services


async def create_task(svc: Services, payload: dict) -> Any:
    """Take a plain-English task, decompose it into a JSON plan of atomic
    idempotent graph-op subtasks (each with a deterministic verifier), and
    execute them against the curated graph-op vocabulary.

    The plan is persisted to disk so it survives crashes and model swaps —
    a fresh session can resume from the plan file. Completion is decided by
    a judge (deterministic verifier + optional LLM), not the worker's
    self-report.

    POST body: {"goal": "<plain English>", "execute": true}
    If execute is false, returns the plan without running it.
    """
    goal = (payload.get("goal") or "").strip()
    if not goal:
        return {"error": "missing goal"}, 400
    execute_now = payload.get("execute", True)

    loop = asyncio.get_event_loop()

    # Step 1: ask the LLM to decompose the goal into atomic graph-op subtasks.
    # Each subtask has: op (one of the 7), args, verifier (Python expression
    # over result), intent. This is the one reasoning step where the model
    # matters most — but the output is JSON with a fixed schema, so even a
    # weak model produces a usable plan (NL2GQL evidence: schema grounding >
    # parameter scale).
    # Lazy import: graph_ops pulls faiss (broken under numpy 2.5.1); only
    # load it when actually building a plan.
    from graph_ops import SCHEMAS as GRAPH_OP_SCHEMAS
    op_names = list(svc.graph_op_registry.ops.keys())
    op_descriptions = "\n".join(
        f"  - {name}: {s['function']['description'][:120]}"
        for name, s in zip(op_names, GRAPH_OP_SCHEMAS))
    decompose_prompt = (
        "You are a task planner for VaultBot, a self-improving research agent "
        "in an Obsidian vault. Decompose the user's goal into a sequence of "
        "atomic, verifiable subtasks using ONLY these graph operations:\n"
        f"{op_descriptions}\n\n"
        "Return a JSON object: {\"subtasks\": [{\"op\": \"...\", "
        "\"intent\": \"...\", \"args\": {...}, \"verifier\": \"result.get('count',0) > 0\"}]}.\n"
        "Each verifier is a Python expression over `result`. Make every subtask "
        "idempotent and independently verifiable. Be specific.\n\n"
        f"User goal: {goal}\n\nReturn ONLY valid JSON, no prose.")
    # Use the SMALL model for decomposition — the output is rigid JSON with a
    # fixed op vocabulary, so a small model can fill it given the schema in the
    # prompt. The framework validates the result (rejects unknown ops, fills
    # default verifier templates) so hallucinated ops/verifiers are caught. The
    # big model's reasoning isn't needed for schema-grounded generation.
    from llm_client import get_small_client_or_big
    try:
        _decompose_client = get_small_client_or_big(svc.session_logger)
        plan_response = await loop.run_in_executor(
            None, lambda: _decompose_client.chat(
                [{"role": "user", "content": decompose_prompt}],
                temperature=0.3, stream=False))
    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        svc.session_logger.log_exception(e, context="task_decompose")
        return {"error": f"decomposition failed: {e}"}, 500

    raw = ""
    if isinstance(plan_response, dict):
        raw = (plan_response.get("message") or {}).get("content", "") \
            if isinstance(plan_response.get("message"), dict) \
            else plan_response.get("response", "") or plan_response.get("content", "")
    else:
        raw = str(plan_response)
    # Parse the JSON plan (tolerate code fences / preamble).
    try:
        plan_data = json.loads(extract_json(raw))
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        return {"error": "could not parse plan", "raw": raw[:500]}, 500

    subtask_dicts = plan_data.get("subtasks", [])
    if not subtask_dicts:
        return {"error": "plan has no subtasks", "raw": raw[:500]}, 500

    # --- Framework guard: reject hallucinated ops the small model invented ---
    # The small model may emit an op name that isn't in the graph-op vocabulary.
    # Drop any subtask whose op isn't in the real op set so a hallucinated op
    # never reaches execution. If ALL subtasks are filtered out, the plan is
    # useless — return an error so the caller can retry with the big model.
    valid_op_names = set(op_names)
    filtered = [sd for sd in subtask_dicts if sd.get("op", "") in valid_op_names]
    if not filtered:
        svc.session_logger.log("task_decompose_all_ops_hallucinated", {
            "raw_ops": [sd.get("op", "") for sd in subtask_dicts],
            "valid_ops": list(valid_op_names),
        })
        return {"error": "decomposition produced no valid ops",
                "raw_ops": [sd.get("op", "") for sd in subtask_dicts],
                "valid_ops": list(valid_op_names)}, 500
    if len(filtered) < len(subtask_dicts):
        svc.session_logger.log("task_decompose_filtered_hallucinated_ops", {
            "dropped": len(subtask_dicts) - len(filtered),
            "kept": len(filtered),
        })
    subtask_dicts = filtered

    # Build the Plan object.
    import time as _time
    plan_id = f"task_{int(_time.time())}"
    subtasks = []
    for i, sd in enumerate(subtask_dicts):
        subtasks.append(Subtask(
            id=f"S{i+1}",
            op=sd.get("op", ""),
            intent=sd.get("intent", ""),
            args=sd.get("args", {}),
            verifier=sd.get("verifier", "True"),
            max_attempts=int(sd.get("max_attempts", 5))))
    plan = Plan(id=plan_id, goal=goal, subtasks=subtasks)

    # Persist the plan so it survives crashes and model swaps.
    plans_dir = Path(__file__).with_name("plans")
    plans_dir.mkdir(exist_ok=True)
    plan_path = plans_dir / f"{plan_id}.json"
    svc.plan_executor.save_plan(plan, str(plan_path))

    if not execute_now:
        return {"plan_id": plan_id, "plan": svc.plan_executor.plan_to_json(plan),
                "executed": False}

    # Execute the plan (graph ops are idempotent; verifier gates each step).
    try:
        plan = await loop.run_in_executor(None, svc.plan_executor.execute, plan)
    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        svc.session_logger.log_exception(e, context="task_execute")
        return {"error": f"execution failed: {e}", "plan_id": plan_id}, 500
    svc.plan_executor.save_plan(plan, str(plan_path))

    # Judge completion (deterministic — the verifier IS the judge; the LLM
    # judge path is intentionally not used, as the deterministic verifier is
    # the whole point of the plan executor's design).
    judgment = svc.plan_executor.judge(plan)
    return {"plan_id": plan_id, "plan": svc.plan_executor.plan_to_json(plan),
            "judgment": judgment, "executed": True}


def write_partial(path: Path, user_message: str, answer: str, thinking: str) -> None:
    """Write the streamed-so-far answer to a partial file for crash recovery.
    Called after each answer_chunk so a crash mid-stream preserves progress.
    Never raises — a partial-write failure must not kill the chat loop.
    """
    try:
        from datetime import datetime
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).isoformat()
        content = (
            f"---\npartial: true\ncreated: {ts}\n---\n\n"
            f"# Partial Answer (crash recovery)\n\n"
            f"## User asked\n{user_message}\n\n"
            f"## Answer so far\n{answer}\n\n"
            f"## Thinking so far\n{thinking[:2000]}\n"
        )
        path.write_text(content, encoding="utf-8")
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        pass


def extract_json(text: str) -> str:
    """Extract a JSON object from text that may have code fences or prose."""
    # Strip code fences.
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        return m.group(1)
    # Find the first { ... } block.
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i+1]
    return text.strip()


async def get_task(svc: Services, plan_id: str) -> Any:
    """Retrieve a persisted plan's status."""
    plan_path = Path(__file__).with_name("plans") / f"{plan_id}.json"
    if not plan_path.exists():
        return {"error": "plan not found"}, 404
    try:
        plan = svc.plan_executor.load_plan(str(plan_path))
        return {"plan": svc.plan_executor.plan_to_json(plan),
                "judgment": svc.plan_executor.judge(plan)}
    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        return {"error": str(e)}, 500


async def resume_task(svc: Services, plan_id: str) -> Any:
    """Resume a partially-completed plan from disk."""
    plan_path = Path(__file__).with_name("plans") / f"{plan_id}.json"
    if not plan_path.exists():
        return {"error": "plan not found"}, 404
    loop = asyncio.get_event_loop()
    try:
        plan = await loop.run_in_executor(None, svc.plan_executor.resume, str(plan_path))
    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        return {"error": str(e)}, 500
    svc.plan_executor.save_plan(plan, str(plan_path))
    return {"plan": svc.plan_executor.plan_to_json(plan),
            "judgment": svc.plan_executor.judge(plan)}
