"""
Tournament runner — pit any number of models from the provider pot OR the
tournament staging pot against vaultbot-specific benchmarks and score them
with LLM-as-judge.

Architecture:
1. User selects a role ("big" or "small") — this determines which benchmark
   suite to use.
2. User selects N models from the tournament staging pot (models they want to
   evaluate before adding to the main pot) OR from the main registry pot.
3. The runner instantiates each model's client via the provider's connection
   info, sends each benchmark prompt, and collects responses.
4. Each response is scored by the current big model (the judge) against the
   benchmark's rubric. A fast keyword pre-filter catches obvious passes/fails
   before invoking the judge.
5. Results are aggregated per-model and per-benchmark, with pass/fail counts,
   scores, and latencies.

The runner is async so the API can stream progress to the frontend.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from tournament_benchmarks import Benchmark, get_benchmarks

# ═══════════════════════════════════════════════════════════════════════════
# Result types
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class BenchmarkResult:
    """One model's result on one benchmark."""

    benchmark_id: str
    benchmark_name: str
    category: str
    passed: bool
    score: float  # 0.0 - 1.0
    response: str  # the model's raw response (truncated)
    judge_reasoning: str = ""  # why the judge scored this way
    latency_ms: float = 0.0
    error: str | None = None


@dataclass
class ModelResult:
    """One model's aggregate tournament results."""

    model_id: str  # registry model id, e.g. "ollama-local:qwen3.6:27b"
    model_name: str  # display name
    provider_id: str  # provider id
    role: str  # "big" or "small"
    benchmarks: list[BenchmarkResult] = field(default_factory=list)
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    overall_score: float = 0.0  # average of all benchmark scores (accuracy)
    total_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0  # average latency per benchmark
    combined_score: float = 0.0  # accuracy (70%) + speed (30%), 0-1
    error: str | None = None  # fatal error that prevented running


@dataclass
class TournamentResults:
    """Complete tournament results."""

    role: str
    models: list[ModelResult] = field(default_factory=list)
    benchmarks: list[dict[str, str]] = field(
        default_factory=list
    )  # {id, name, category}
    judge_model: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Keyword pre-filter — fast pass/fail before invoking the judge
# ═══════════════════════════════════════════════════════════════════════════


def _keyword_prefilter(benchmark: Benchmark, response: str) -> bool | None:
    """Fast keyword check. Returns True (pass), False (fail), or None (uncertain).

    Only returns a definitive answer when ALL expected keywords are present
    (pass) or NONE are present (fail). Returns None for borderline cases
    that need the judge.
    """
    if not benchmark.expected_keywords:
        return None
    lower = response.lower()
    hits = [kw.lower() in lower for kw in benchmark.expected_keywords]
    if all(hits):
        return True
    if not any(hits):
        return False
    return None  # partial match — let the judge decide


# ═══════════════════════════════════════════════════════════════════════════
# LLM judge
# ═══════════════════════════════════════════════════════════════════════════

_JUDGE_SYSTEM = (
    "You are a tournament judge scoring model responses against a rubric. "
    "Be objective and strict. Reply with a JSON object: "
    '{"passed": true/false, "score": 0.0-1.0, "reasoning": "one sentence"}. '
    "Nothing else."
)


def _build_judge_prompt(benchmark: Benchmark, response: str) -> str:
    """Build the judge prompt for one benchmark response."""
    return (
        f"Benchmark: {benchmark.name}\n"
        f"Prompt: {benchmark.prompt}\n\n"
        f"Model response: {response}\n\n"
        f"Rubric: {benchmark.rubric}\n\n"
        f"Score this response."
    )


async def _judge_response(
    judge_client: Any,
    benchmark: Benchmark,
    response: str,
) -> tuple[bool, float, str]:
    """Have the big model judge a contestant's response.

    Returns (passed, score, reasoning).
    """
    # Fast keyword pre-filter
    pre = _keyword_prefilter(benchmark, response)
    if pre is True:
        return True, 1.0, "keyword match: all expected keywords present"
    if pre is False:
        return False, 0.0, "keyword mismatch: no expected keywords found"

    # LLM judge
    try:
        result = await asyncio.to_thread(
            judge_client.chat,
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": _build_judge_prompt(benchmark, response)},
            ],
            tools=None,
            temperature=0.0,
            stream=False,
            max_predict=128,
        )
        # Parse the judge's JSON
        if isinstance(result, dict):
            text = result.get("response", "") or ""
        else:
            text = str(result)
        # Try to extract JSON from the response
        text = text.strip()
        # Find the first { and last }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            parsed = json.loads(text[start : end + 1])
            passed = bool(parsed.get("passed", False))
            score = float(parsed.get("score", 0.0))
            reasoning = str(parsed.get("reasoning", ""))
            return passed, max(0.0, min(1.0, score)), reasoning
        # Fallback: if the judge didn't return JSON, use keyword match
        return (
            pre or False,
            0.5 if pre is None else (1.0 if pre else 0.0),
            "judge returned non-JSON; used keyword fallback",
        )
    except Exception as e:
        return False, 0.0, f"judge error: {e}"


# ═══════════════════════════════════════════════════════════════════════════
# Single-model runner
# ═══════════════════════════════════════════════════════════════════════════


async def _run_model_benchmarks(
    model_id: str,
    model_name: str,
    provider_id: str,
    role: str,
    benchmarks: list[Benchmark],
    contestant_client: Any,
    judge_client: Any,
    progress_callback: callable | None = None,
) -> ModelResult:
    """Run all benchmarks for one model.

    Args:
        model_id: registry model id
        model_name: display name (the model field from ModelEntry)
        provider_id: provider id
        role: "big" or "small"
        benchmarks: the benchmark suite to run
        contestant_client: LLMClient for the model being tested
        judge_client: LLMClient for the big model acting as judge
        progress_callback: optional async (benchmark_id, status) -> None
    """
    result = ModelResult(
        model_id=model_id,
        model_name=model_name,
        provider_id=provider_id,
        role=role,
    )

    for i, bench in enumerate(benchmarks):
        if progress_callback:
            await progress_callback(bench.id, "running")

        t0 = time.time()
        try:
            # Send the benchmark prompt to the contestant
            messages = []
            if bench.system:
                messages.append({"role": "system", "content": bench.system})
            messages.append({"role": "user", "content": bench.prompt})

            raw = await asyncio.to_thread(
                contestant_client.chat,
                messages=messages,
                tools=None,
                temperature=bench.temperature,
                stream=False,
                max_predict=bench.max_tokens,
            )

            if isinstance(raw, dict):
                response = raw.get("response", "") or ""
            else:
                response = str(raw)

            latency = (time.time() - t0) * 1000

            # Truncate very long responses for the judge
            judge_response_text = response[:2000]

            # Judge the response
            passed, score, reasoning = await _judge_response(
                judge_client,
                bench,
                judge_response_text,
            )

            br = BenchmarkResult(
                benchmark_id=bench.id,
                benchmark_name=bench.name,
                category=bench.category,
                passed=passed,
                score=score,
                response=response[:500],
                judge_reasoning=reasoning,
                latency_ms=latency,
            )
            result.benchmarks.append(br)
            result.total += 1
            if passed:
                result.passed += 1
            else:
                result.failed += 1
            result.total_latency_ms += latency

            if progress_callback:
                await progress_callback(bench.id, "done")

        except Exception as e:
            latency = (time.time() - t0) * 1000
            br = BenchmarkResult(
                benchmark_id=bench.id,
                benchmark_name=bench.name,
                category=bench.category,
                passed=False,
                score=0.0,
                response="",
                judge_reasoning="",
                latency_ms=latency,
                error=str(e),
            )
            result.benchmarks.append(br)
            result.total += 1
            result.errors += 1
            result.total_latency_ms += latency

            if progress_callback:
                await progress_callback(bench.id, "error")

    # Compute overall score and average latency
    if result.total > 0:
        result.overall_score = sum(b.score for b in result.benchmarks) / result.total
        result.avg_latency_ms = result.total_latency_ms / result.total

    return result


def _compute_combined_scores(models: list[ModelResult]) -> None:
    """Compute combined score (accuracy + speed) for all models in a tournament.

    Combined = 0.7 × accuracy + 0.3 × speed_score
    Speed score is normalized: fastest model gets 1.0, slowest gets 0.0.
    Models with errors get speed_score = 0.0.
    """
    if not models:
        return

    # Find min/max avg latency among models that actually ran
    latencies = [m.avg_latency_ms for m in models if m.total > 0 and m.errors < m.total]
    if not latencies:
        for m in models:
            m.combined_score = m.overall_score
        return

    min_lat = min(latencies)
    max_lat = max(latencies)
    latency_range = max_lat - min_lat if max_lat > min_lat else 1.0

    for m in models:
        if m.total == 0 or m.errors >= m.total:
            m.combined_score = m.overall_score
            continue
        # Speed score: 1.0 for fastest, 0.0 for slowest
        speed_score = 1.0 - ((m.avg_latency_ms - min_lat) / latency_range)
        speed_score = max(0.0, min(1.0, speed_score))
        m.combined_score = 0.7 * m.overall_score + 0.3 * speed_score


# ═══════════════════════════════════════════════════════════════════════════
# Main tournament runner
# ═══════════════════════════════════════════════════════════════════════════


async def run_tournament(
    contestants: list[dict[str, str]],
    role: str,
    registry: Any,
    judge_client: Any,
    progress_callback: callable | None = None,
) -> TournamentResults:
    """Run a tournament: pit N models against the role's benchmark suite.

    Args:
        contestants: list of {"model_id": str, "model_name": str, "provider_id": str}.
            These can come from the main registry pot OR the tournament staging pot.
        role: "big" or "small" — determines which benchmarks to run
        registry: ProviderRegistry instance (for provider connection info)
        judge_client: LLMClient for the big model (the judge)
        progress_callback: optional async (model_id, benchmark_id, status) -> None

    Returns:
        TournamentResults with per-model and per-benchmark scores.
    """
    from llm_client import _client_for_model_entry
    from providers import ModelEntry

    benchmarks = get_benchmarks(role)
    if not benchmarks:
        raise ValueError(f"No benchmarks defined for role '{role}'")

    results = TournamentResults(
        role=role,
        benchmarks=[
            {"id": b.id, "name": b.name, "category": b.category} for b in benchmarks
        ],
        judge_model=getattr(judge_client, "llm_model", "unknown"),
        started_at=time.time(),
    )

    for c in contestants:
        mid = c["model_id"]
        model_name = c["model_name"]
        provider_id = c["provider_id"]

        provider = registry.get_provider(provider_id)
        if provider is None:
            mr = ModelResult(
                model_id=mid,
                model_name=model_name,
                provider_id=provider_id,
                role=role,
                error=f"Provider '{provider_id}' not found in registry",
            )
            results.models.append(mr)
            continue

        # Build a synthetic ModelEntry for the contestant (may not be in the
        # main registry — it could be a staging-only model).
        entry = registry.get_model(mid)
        if entry is None:
            # Staging model — build a temporary ModelEntry from the provider
            entry = ModelEntry(id=mid, model=model_name, provider=provider_id)

        # Build a client for this specific model
        try:
            contestant = _client_for_model_entry(entry, provider)
        except Exception as e:
            mr = ModelResult(
                model_id=mid,
                model_name=model_name,
                provider_id=provider_id,
                role=role,
                error=f"Failed to create client: {e}",
            )
            results.models.append(mr)
            continue

        # Wrap the progress callback to include model_id
        async def model_progress(benchmark_id: str, status: str, mid=mid) -> None:
            if progress_callback:
                await progress_callback(mid, benchmark_id, status)

        mr = await _run_model_benchmarks(
            model_id=mid,
            model_name=model_name,
            provider_id=provider_id,
            role=role,
            benchmarks=benchmarks,
            contestant_client=contestant,
            judge_client=judge_client,
            progress_callback=model_progress,
        )
        results.models.append(mr)

    results.finished_at = time.time()
    _compute_combined_scores(results.models)
    return results


# ═══════════════════════════════════════════════════════════════════════════
# Streaming tournament runner (for WebSocket progress)
# ═══════════════════════════════════════════════════════════════════════════


async def run_tournament_streaming(
    contestants: list[dict[str, str]],
    role: str,
    registry: Any,
    judge_client: Any,
) -> AsyncIterator[dict[str, Any]]:
    """Run a tournament, yielding progress events as they happen.

    Args:
        contestants: list of {"model_id": str, "model_name": str, "provider_id": str}
        role: "big" or "small"
        registry: ProviderRegistry instance
        judge_client: LLMClient for the big model (the judge)

    Yields dicts with type:
      - "start": tournament starting
      - "model_start": beginning a model's run
      - "model_done": one model finished
      - "done": tournament complete with full results
    """
    from llm_client import _client_for_model_entry
    from providers import ModelEntry

    benchmarks = get_benchmarks(role)
    if not benchmarks:
        yield {"type": "error", "message": f"No benchmarks for role '{role}'"}
        return

    yield {
        "type": "start",
        "role": role,
        "model_count": len(contestants),
        "benchmark_count": len(benchmarks),
        "benchmarks": [
            {"id": b.id, "name": b.name, "category": b.category} for b in benchmarks
        ],
    }

    all_results: list[ModelResult] = []

    for i, c in enumerate(contestants):
        mid = c["model_id"]
        model_name = c["model_name"]
        provider_id = c["provider_id"]

        yield {
            "type": "model_start",
            "model_id": mid,
            "model_name": model_name,
            "index": i,
            "total": len(contestants),
        }

        provider = registry.get_provider(provider_id)
        if provider is None:
            yield {
                "type": "model_error",
                "model_id": mid,
                "error": f"Provider '{provider_id}' not found",
            }
            continue

        entry = registry.get_model(mid)
        if entry is None:
            entry = ModelEntry(id=mid, model=model_name, provider=provider_id)

        try:
            contestant = _client_for_model_entry(entry, provider)
        except Exception as e:
            yield {
                "type": "model_error",
                "model_id": mid,
                "error": f"Failed to create client: {e}",
            }
            continue

        mr = await _run_model_benchmarks(
            model_id=mid,
            model_name=model_name,
            provider_id=provider_id,
            role=role,
            benchmarks=benchmarks,
            contestant_client=contestant,
            judge_client=judge_client,
            progress_callback=None,
        )
        all_results.append(mr)

        yield {
            "type": "model_done",
            "model_id": mid,
            "model_name": model_name,
            "passed": mr.passed,
            "failed": mr.failed,
            "errors": mr.errors,
            "total": mr.total,
            "overall_score": mr.overall_score,
            "total_latency_ms": mr.total_latency_ms,
            "benchmarks": [
                {
                    "benchmark_id": b.benchmark_id,
                    "benchmark_name": b.benchmark_name,
                    "passed": b.passed,
                    "score": b.score,
                    "latency_ms": b.latency_ms,
                    "error": b.error,
                }
                for b in mr.benchmarks
            ],
        }

    yield {
        "type": "done",
        "role": role,
        "models": [
            {
                "model_id": m.model_id,
                "model_name": m.model_name,
                "provider_id": m.provider_id,
                "passed": m.passed,
                "failed": m.failed,
                "errors": m.errors,
                "total": m.total,
                "overall_score": m.overall_score,
                "total_latency_ms": m.total_latency_ms,
                "error": m.error,
            }
            for m in all_results
        ],
    }
