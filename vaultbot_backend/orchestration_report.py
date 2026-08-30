"""Lightweight orchestration attribution report for a single session.

Reads a session JSONL log and returns a structured summary of per-turn
route decisions, token costs, and efficiency metrics.  Designed to answer:
"why did each turn use small model / procedure / big model, and what did
it cost?"

Usage (CLI)::

    python -m orchestration_report <session-uuid>
    python -m orchestration_report <session-uuid> --json

Usage (library)::

    from orchestration_report import session_orchestration_report
    report = session_orchestration_report(Path("sessions/<uuid>.jsonl"))
    print(report["summary"])

See ``docs/ORCHESTRATION-METRICS.md`` for metric definitions.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# ── Public API ────────────────────────────────────────────────────────────


def session_orchestration_report(
    log_path: Path,
    *,
    baselines: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Parse *log_path* and return a structured orchestration attribution report.

    Parameters
    ----------
    log_path:
        Path to a ``.jsonl`` session log produced by
        :class:`~session_logger.SessionLogger`.
    baselines:
        Optional mapping of metric names to reference values used to
        compute deltas in the ``comparisons`` section.  Recognised keys:
        ``cost_usd_per_turn``, ``tool_latency_ms_per_turn``,
        ``tool_rounds_per_turn``.

    Returns
    -------
    dict with keys:

    ``session_id``
        UUID string from the log header.
    ``turns``
        List of per-turn attribution dicts (see below).
    ``summary``
        Session-level rollup: route distribution, total tokens/cost/latency.
    ``comparisons``
        Delta vs *baselines* when provided, empty dict otherwise.
    """
    baselines = baselines or {}
    turns: list[dict[str, Any]] = []
    session_id = ""

    # Accumulate per-turn state keyed by turn_index (or sequential order).
    _by_turn: dict[int, dict[str, Any]] = {}
    # Fallback sequence counter used when an event omits ``turn_index``.
    # ``_seq`` starts at 0 and is incremented on every ``chat_begin`` event,
    # so after the first user message it equals 1 — matching the 1-based
    # convention used by callers that explicitly supply ``turn_index``.
    # Attribution events that arrive before any ``chat_begin`` (unusual) will
    # be grouped under index 0, which is visually distinct in the report.
    _seq = 0

    def _ensure_turn(idx: int) -> dict[str, Any]:
        if idx not in _by_turn:
            _by_turn[idx] = {
                "turn_index": idx,
                "route": None,
                "confidence": None,
                "reason": None,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost_usd": None,
                "tool_latency_ms": 0.0,
                "model": None,
                "tool_rounds": 0,
                "completion_outcome": None,
                "repeated_tool_calls": [],
                "llm_invocations": [],
            }
        return _by_turn[idx]

    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {
            "error": f"cannot read log: {exc}",
            "session_id": "",
            "turns": [],
            "summary": {},
            "comparisons": {},
        }

    for line in lines:
        if not line.strip():
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue

        ev = evt.get("event", "")
        data = evt.get("data") or {}

        if ev == "session_start":
            session_id = evt.get("session_id", "")
            continue

        if ev == "route_decision":
            ti = data.get("turn_index", _seq)
            t = _ensure_turn(ti)
            t["route"] = data.get("route")
            t["confidence"] = data.get("confidence")
            t["reason"] = data.get("reason")
            continue

        if ev == "turn_cost":
            ti = data.get("turn_index", _seq)
            t = _ensure_turn(ti)
            t["prompt_tokens"] = data.get("prompt_tokens", 0)
            t["completion_tokens"] = data.get("completion_tokens", 0)
            t["total_tokens"] = data.get("total_tokens", 0)
            t["tool_latency_ms"] = data.get("tool_latency_ms", 0.0)
            if data.get("cost_usd") is not None:
                t["cost_usd"] = data["cost_usd"]
            if data.get("model"):
                t["model"] = data["model"]
            continue

        if ev == "turn_efficiency":
            ti = data.get("turn_index", _seq)
            t = _ensure_turn(ti)
            t["tool_rounds"] = data.get("tool_rounds", 0)
            t["completion_outcome"] = data.get("completion_outcome")
            t["repeated_tool_calls"] = data.get("repeated_tool_calls", [])
            continue

        if ev == "llm_invocation":
            context = data.get("context") or {}
            ti = context.get("turn_index", _seq)
            t = _ensure_turn(ti)
            t["llm_invocations"].append(data)
            continue

        # Advance the sequential counter on chat_begin so events without
        # explicit turn_index still group correctly.
        if ev == "chat_begin":
            _seq += 1

    turns = sorted(_by_turn.values(), key=lambda t: t["turn_index"])

    # ── Session-level summary ─────────────────────────────────────────────
    route_counts: dict[str, int] = {}
    total_prompt = total_completion = 0
    total_cost: float | None = None
    total_tool_latency = 0.0
    total_tool_rounds = 0
    repeated_flag_count = 0
    outcome_counts: dict[str, int] = {}
    invocation_count = 0
    invocation_tokens = 0

    for t in turns:
        r = t["route"] or "unknown"
        route_counts[r] = route_counts.get(r, 0) + 1
        total_prompt += t["prompt_tokens"]
        total_completion += t["completion_tokens"]
        if t["cost_usd"] is not None:
            total_cost = (total_cost or 0.0) + t["cost_usd"]
        total_tool_latency += t["tool_latency_ms"]
        total_tool_rounds += t["tool_rounds"]
        if t["repeated_tool_calls"]:
            repeated_flag_count += 1
        oc = t["completion_outcome"] or "unknown"
        outcome_counts[oc] = outcome_counts.get(oc, 0) + 1
        invocation_count += len(t["llm_invocations"])
        invocation_tokens += sum(
            int(invocation.get("total_tokens", 0) or 0)
            for invocation in t["llm_invocations"]
        )

    n_turns = len(turns)
    summary: dict[str, Any] = {
        "session_id": session_id,
        "turn_count": n_turns,
        "route_distribution": route_counts,
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "total_tokens": total_prompt + total_completion,
        "total_tool_latency_ms": round(total_tool_latency, 2),
        "turns_with_repeated_tool_calls": repeated_flag_count,
        "completion_outcomes": outcome_counts,
        "llm_invocation_count": invocation_count,
        "llm_invocation_tokens": invocation_tokens,
    }
    if n_turns:
        summary["avg_tool_latency_ms_per_turn"] = round(total_tool_latency / n_turns, 2)
        summary["avg_tool_rounds_per_turn"] = round(total_tool_rounds / n_turns, 3)
    if total_cost is not None and n_turns:
        summary["total_cost_usd"] = round(total_cost, 8)
        summary["avg_cost_usd_per_turn"] = round(total_cost / n_turns, 8)

    # ── Baseline comparisons ──────────────────────────────────────────────
    comparisons: dict[str, Any] = {}
    if baselines:
        if "cost_usd_per_turn" in baselines and "avg_cost_usd_per_turn" in summary:
            comparisons["cost_usd_per_turn_delta"] = round(
                summary["avg_cost_usd_per_turn"] - baselines["cost_usd_per_turn"], 8
            )
        if (
            "tool_latency_ms_per_turn" in baselines
            and "avg_tool_latency_ms_per_turn" in summary
        ):
            comparisons["tool_latency_ms_per_turn_delta"] = round(
                summary["avg_tool_latency_ms_per_turn"]
                - baselines["tool_latency_ms_per_turn"],
                2,
            )
        if (
            "tool_rounds_per_turn" in baselines
            and "avg_tool_rounds_per_turn" in summary
        ):
            comparisons["tool_rounds_per_turn_delta"] = round(
                summary["avg_tool_rounds_per_turn"] - baselines["tool_rounds_per_turn"],
                3,
            )

    return {
        "session_id": session_id,
        "turns": turns,
        "summary": summary,
        "comparisons": comparisons,
    }


# ── CLI ───────────────────────────────────────────────────────────────────


def _find_log(session_ref: str, sessions_dir: Path) -> Path | None:
    """Resolve a session UUID or partial title to a JSONL path."""
    direct = sessions_dir / f"{session_ref}.jsonl"
    if direct.exists():
        return direct
    # Partial match on filename
    for f in sessions_dir.glob("*.jsonl"):
        if session_ref.lower() in f.stem.lower():
            return f
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Orchestration attribution report for a VaultBot session."
    )
    parser.add_argument("session", help="Session UUID or partial filename.")
    parser.add_argument(
        "--sessions-dir",
        default=str(Path(__file__).parent / "sessions"),
        help="Directory containing session JSONL files.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output raw JSON instead of human-readable text.",
    )
    args = parser.parse_args(argv)

    log_path = _find_log(args.session, Path(args.sessions_dir))
    if log_path is None:
        print(
            f"[orchestration_report] Session not found: {args.session}",
            file=sys.stderr,
        )
        return 1

    report = session_orchestration_report(log_path)

    if args.as_json:
        print(json.dumps(report, indent=2, default=str))
        return 0

    s = report["summary"]
    print(f"Session: {report['session_id']}")
    print(f"Turns  : {s.get('turn_count', 0)}")
    print(f"Routes : {s.get('route_distribution', {})}")
    print(
        f"Tokens : {s.get('total_tokens', 0)}  "
        f"(prompt {s.get('total_prompt_tokens', 0)}  "
        f"completion {s.get('total_completion_tokens', 0)})"
    )
    if "total_cost_usd" in s:
        print(
            f"Cost   : ${s['total_cost_usd']:.6f}  "
            f"(avg ${s.get('avg_cost_usd_per_turn', 0):.6f}/turn)"
        )
    print(
        f"Tool latency: {s.get('total_tool_latency_ms', 0):.1f} ms total  "
        f"({s.get('avg_tool_latency_ms_per_turn', 0):.1f} ms/turn avg)"
    )
    print(f"Tool rounds : {s.get('avg_tool_rounds_per_turn', 0):.2f}/turn avg")
    print(f"Repeated-tool flags: {s.get('turns_with_repeated_tool_calls', 0)} turns")
    print(f"Outcomes: {s.get('completion_outcomes', {})}")
    if report["comparisons"]:
        print("--- Baseline deltas ---")
        for k, v in report["comparisons"].items():
            sign = "+" if v >= 0 else ""
            print(f"  {k}: {sign}{v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
