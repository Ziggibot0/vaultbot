"""Pure energy-estimate aggregation over invocation-level session events."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _energy_wh(data: dict[str, Any], profile: dict[str, Any]) -> float | None:
    input_rate = profile.get("wh_per_1k_input_tokens")
    output_rate = profile.get("wh_per_1k_output_tokens")
    if input_rate is None or output_rate is None:
        return None
    return (
        data.get("prompt_tokens", 0) * float(input_rate)
        + data.get("completion_tokens", 0) * float(output_rate)
    ) / 1000


def build_energy_report(
    sessions_dir: Path,
    profiles: dict[str, dict[str, Any]],
    big_model_id: str | None,
    *,
    days: int = 30,
    now: float | None = None,
) -> dict[str, Any]:
    """Build an estimate report without mutating logs or configuration."""
    if days < 1 or days > 3650:
        raise ValueError("days must be between 1 and 3650")
    now = time.time() if now is None else now
    cutoff = now - days * 86400
    big_profile = profiles.get(big_model_id or "")
    role_totals: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"invocations": 0, "tokens": 0, "actual_wh": 0.0}
    )
    model_totals: dict[str, dict[str, Any]] = {}
    daily: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"invocations": 0, "actual_wh": 0.0, "counterfactual_wh": 0.0}
    )
    coverage: dict[str, Any] = {
        "session_files_scanned": 0,
        "session_files_with_invocations": 0,
        "legacy_session_files_excluded": 0,
        "invocations_missing_profile": 0,
        "tokens_missing_profile": 0,
        "estimated_token_invocations": 0,
        "failed_or_cancelled_invocations": 0,
        "vision_invocations_excluded_from_savings": 0,
    }
    totals: dict[str, Any] = {
        "tracked_invocations": 0,
        "tracked_tokens": 0,
        "actual_wh": 0.0,
        "counterfactual_wh": 0.0,
        "saved_wh": 0.0,
    }
    first_tracked: float | None = None
    last_tracked: float | None = None

    for log_path in sessions_dir.glob("*.jsonl") if sessions_dir.exists() else []:
        coverage["session_files_scanned"] += 1
        file_has_invocations = False
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") != "llm_invocation":
                continue
            file_has_invocations = True
            timestamp = float(event.get("timestamp", 0) or 0)
            if timestamp < cutoff or timestamp > now:
                continue
            data = event.get("data") or {}
            tokens = int(data.get("total_tokens", 0) or 0)
            role = str(data.get("role") or "unknown")
            model_id = str(data.get("model_id") or "unknown")
            totals["tracked_invocations"] += 1
            totals["tracked_tokens"] += tokens
            role_totals[role]["invocations"] += 1
            role_totals[role]["tokens"] += tokens
            model = model_totals.setdefault(
                model_id,
                {
                    "model_id": model_id,
                    "model": data.get("model") or model_id,
                    "provider_id": data.get("provider_id") or "",
                    "roles": set(),
                    "invocations": 0,
                    "tokens": 0,
                    "actual_wh": 0.0,
                },
            )
            model["roles"].add(role)
            model["invocations"] += 1
            model["tokens"] += tokens
            first_tracked = (
                timestamp if first_tracked is None else min(first_tracked, timestamp)
            )
            last_tracked = (
                timestamp if last_tracked is None else max(last_tracked, timestamp)
            )
            day = datetime.fromtimestamp(timestamp, tz=UTC).date().isoformat()
            daily[day]["invocations"] += 1
            if data.get("token_source") == "estimated":
                coverage["estimated_token_invocations"] += 1
            if data.get("outcome") != "success":
                coverage["failed_or_cancelled_invocations"] += 1
                continue
            actual = _energy_wh(data, profiles.get(model_id, {}))
            if actual is None:
                coverage["invocations_missing_profile"] += 1
                coverage["tokens_missing_profile"] += tokens
            else:
                totals["actual_wh"] += actual
                role_totals[role]["actual_wh"] += actual
                model["actual_wh"] += actual
                daily[day]["actual_wh"] += actual
            if role == "vision":
                coverage["vision_invocations_excluded_from_savings"] += 1
            elif role != "big" and big_profile is not None and actual is not None:
                counterfactual = _energy_wh(data, big_profile)
                if counterfactual is not None:
                    totals["counterfactual_wh"] += counterfactual
                    daily[day]["counterfactual_wh"] += counterfactual
                    totals["saved_wh"] += counterfactual - actual
        if file_has_invocations:
            coverage["session_files_with_invocations"] += 1
        else:
            coverage["legacy_session_files_excluded"] += 1

    actual_wh = float(totals["actual_wh"])
    counterfactual_wh = float(totals["counterfactual_wh"])
    totals["actual_wh"] = round(actual_wh, 8)
    totals["counterfactual_wh"] = round(counterfactual_wh, 8)
    totals["saved_wh"] = round(float(totals["saved_wh"]), 8)
    totals["savings_percent"] = (
        round(float(totals["saved_wh"]) / counterfactual_wh * 100, 2)
        if counterfactual_wh > 0
        else None
    )
    coverage["first_tracked_at"] = (
        datetime.fromtimestamp(first_tracked, tz=UTC).isoformat()
        if first_tracked is not None
        else None
    )
    coverage["last_tracked_at"] = (
        datetime.fromtimestamp(last_tracked, tz=UTC).isoformat()
        if last_tracked is not None
        else None
    )
    models = []
    for value in model_totals.values():
        value["roles"] = sorted(value["roles"])
        value["actual_wh"] = round(value["actual_wh"], 8)
        models.append(value)
    return {
        "estimate": True,
        "range_days": days,
        "baseline_big_model_id": big_model_id,
        "totals": totals,
        "by_role": {key: value for key, value in sorted(role_totals.items())},
        "by_model": sorted(models, key=lambda item: item["model_id"]),
        "daily": [{"date": key, **value} for key, value in sorted(daily.items())],
        "coverage": coverage,
    }
