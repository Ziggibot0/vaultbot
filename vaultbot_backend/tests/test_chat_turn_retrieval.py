"""Tests for the turn-retrieval phase extracted from chat_turn_prep.py (#451).

Pins the behavior contract of ``chat_turn_retrieval.retrieve_turn_context``:
single-query and expanded-query paths, partial/failing retrieval, dedup/
rerank, the top-five contract, fail-loud notification, and telemetry. These
tests guard the extraction — they must pass with the SAME behavior the
inline code in ``prepare_turn`` had before the refactor.
"""

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_expand_breaker():
    """The expand circuit breaker is module-global and persists across tests
    in one process. A prior failing run (or a test that intentionally breaks
    the small client) trips it, which would silently skip expansion for every
    later test. Reset it before each test so expansion behavior is
    deterministic."""
    import small_model_filters as smf

    smf._breaker.pop("expand", None)
    yield
    smf._breaker.pop("expand", None)


class _RecordingManager:
    """Fake ConnectionManager: records every send_personal_message payload."""

    def __init__(self):
        self.calls = []

    async def send_personal_message(self, message, websocket, session_logger=None):
        self.calls.append(message)


class _Log:
    """Fake session logger: records (event_name, data) tuples."""

    def __init__(self):
        self.events = []

    def log(self, name, data=None, **kwargs):
        self.events.append((name, data))

    def log_exception(self, exc, context=None):
        self.events.append(("exception", {"context": context, "error": str(exc)}))


def _note(path: str, score: float = 1.0) -> dict:
    return {"file_path": path, "score": score, "content": f"body of {path}"}


class _FakeSmallClient:
    """Fake small model client: returns canned expanded queries from .chat().

    ``expand_query`` calls ``client.chat(msgs, temperature=..., stream=False,
    think=False, max_predict=...)`` and reads ``message.content`` from the
    returned dict. Each expanded line must share a content word with the
    original query or it is dropped by the guard.
    """

    def __init__(self, expanded: list[str]):
        self.expanded = expanded

    def chat(self, msgs, **kwargs):
        return {"message": {"content": "\n".join(self.expanded)}}


def _recording_svc(
    *,
    results_by_query: dict[str, list[dict]] | None = None,
    small_client: Any = None,
    graph_nodes: int = 3,
) -> Any:
    """Services-like fake with a recording manager + fused retriever.

    ``results_by_query`` maps a query string to the list of notes that
    ``fused_retriever.retrieve`` returns for it. When omitted, retrieval
    returns a single default note.
    """
    manager = _RecordingManager()
    results_by_query = results_by_query or {}

    def _retrieve(query, k=15, _=1):
        if query in results_by_query:
            return {"results": results_by_query[query]}
        return {"results": [_note("default.md")]}

    return SimpleNamespace(
        manager=manager,
        session_logger=None,
        fused_retriever=SimpleNamespace(retrieve=_retrieve),
        vault_graph=SimpleNamespace(
            refresh=lambda: None, nodes=[object() for _ in range(graph_nodes)]
        ),
        small_client=small_client,
    )


def _stages(svc):
    return [json.loads(m).get("stage") for m in svc.manager.calls]


def _payloads(svc):
    return [json.loads(m) for m in svc.manager.calls]


def _run(svc, user_message="what is the vault?", websocket=None):
    from chat_turn_retrieval import retrieve_turn_context

    return asyncio.run(
        retrieve_turn_context(svc, websocket or object(), _Log(), user_message)
    )


# ── Single-query path (no small client → no expansion) ────────────────────


def test_single_query_returns_top_five_and_rewritten_query():
    """With no small client, the query is not expanded: one retrieve call,
    results capped at five, and the rewritten query defaults to the raw
    user message."""
    svc = _recording_svc(results_by_query={"what is the vault?": [_note("a.md")] * 8})

    result = _run(svc)

    assert result.rewritten_query == "what is the vault?"
    assert result.queries == ["what is the vault?"]
    assert len(result.results) == 5, "single-query path must cap at top five"
    assert result.results[0]["file_path"] == "a.md"


def test_single_query_emits_retrieval_progress_stages():
    """The single-query path must emit the 'retrieving vault' heartbeat and
    its closing '_done' stage so the UI never idles. The bare label only
    fires on a slow call (heartbeat); the guaranteed event is the '_done'
    closer."""
    svc = _recording_svc()

    _run(svc)

    stages = _stages(svc)
    assert "retrieving vault_done" in stages


# ── Expanded-query path (small client present) ───────────────────────────


def test_expanded_query_retrieves_all_queries_and_dedups():
    """With a small client, the query is expanded and every query is
    retrieved in parallel; overlapping results are deduped."""
    svc = _recording_svc(
        small_client=_FakeSmallClient(expanded=["vault definition", "vault retrieval"]),
        results_by_query={
            "vault search": [_note("a.md")],
            "vault definition": [_note("a.md"), _note("b.md")],
            "vault retrieval": [_note("c.md")],
        },
    )

    result = _run(svc, user_message="vault search")

    # The raw user message is always included, so the query set has >= 2.
    assert len(result.queries) >= 2
    assert "vault search" in result.queries
    # Dedup collapses the shared 'a.md' across queries.
    paths = [r["file_path"] for r in result.results]
    assert len(paths) == len(set(paths)), "results must be deduped across queries"


def test_expanded_query_emits_expanding_and_reranking_stages():
    """The expanded path must emit 'expanding query' and, when more than
    five results survive dedup, the 'reranking results'/'reranking_done'
    pair."""
    svc = _recording_svc(
        small_client=_FakeSmallClient(expanded=["vault definition"]),
        results_by_query={
            "vault search": [_note(f"n{i}.md") for i in range(4)],
            "vault definition": [_note(f"m{i}.md") for i in range(4)],
        },
    )

    _run(svc, user_message="vault search")

    stages = _stages(svc)
    assert "expanding query" in stages
    assert "reranking results" in stages
    assert "reranking_done" in stages


# ── Partial / failing retrieval ────────────────────────────────────────────


def test_retrieval_failure_is_fail_loud_and_returns_empty():
    """If fused retrieval raises, the user must get a 'problem' card (never
    silent) and the result must be an empty list so the turn degrades
    gracefully."""

    class _BrokenRetriever:
        def retrieve(self, *a, **k):
            raise RuntimeError("vault index corrupt")

    svc = _recording_svc()
    svc.fused_retriever = SimpleNamespace(retrieve=_BrokenRetriever().retrieve)

    result = _run(svc)

    assert result.results == []
    kinds = [p.get("type") for p in _payloads(svc)]
    assert "problem" in kinds, "retrieval failure must surface a problem card"
    assert any(
        p.get("type") == "problem"
        and "couldn't search" in str(p.get("diagnosis", {}).get("user_message", ""))
        for p in _payloads(svc)
    )


def test_retrieval_failure_is_logged_not_swallowed():
    """The retrieval exception must be logged with context so the operator
    can diagnose it."""
    from chat_turn_retrieval import retrieve_turn_context

    class _BrokenRetriever:
        def retrieve(self, *a, **k):
            raise RuntimeError("boom")

    svc = _recording_svc()
    svc.fused_retriever = SimpleNamespace(retrieve=_BrokenRetriever().retrieve)
    log = _Log()

    asyncio.run(retrieve_turn_context(svc, object(), log, "hi"))

    assert any(name == "exception" for name, _ in log.events)


# ── Telemetry ──────────────────────────────────────────────────────────────


def test_vault_search_telemetry_logged():
    """The phase must log a 'vault_search' event with query, k, result
    count, retriever, and the rewritten query when it differs."""
    from chat_turn_retrieval import retrieve_turn_context

    svc = _recording_svc()
    log = _Log()

    asyncio.run(retrieve_turn_context(svc, object(), log, "my question"))

    names = [n for n, _ in log.events]
    assert "vault_search" in names
    (_, data) = next(e for e in log.events if e[0] == "vault_search")
    assert data["query"] == "my question"
    assert data["k"] == 5
    assert data["retriever"] == "fused"
    assert data["result_count"] == 1


def test_graph_refresh_logged():
    """The graph refresh must log a 'graph_refreshed' event with the node
    count."""
    from chat_turn_retrieval import retrieve_turn_context

    svc = _recording_svc(graph_nodes=7)
    log = _Log()

    asyncio.run(retrieve_turn_context(svc, object(), log, "hi"))

    names = [n for n, _ in log.events]
    assert "graph_refreshed" in names
    (_, data) = next(e for e in log.events if e[0] == "graph_refreshed")
    assert data["node_count"] == 7


# ── Cancellation point ─────────────────────────────────────────────────────


def test_cancellation_check_is_called():
    """The phase must call the shared cancellation check so a cancelled
    turn stops before retrieval work."""
    from chat_turn_retrieval import retrieve_turn_context

    called = {"n": 0}

    class _CancellingWS:
        conversation_history: list = []  # noqa: RUF012 -- test-only mutable default

    def _check_cancelled(websocket):
        called["n"] += 1

    import chat_turn_retrieval as mod

    original = mod._check_cancelled
    mod._check_cancelled = _check_cancelled
    try:
        asyncio.run(
            retrieve_turn_context(_recording_svc(), _CancellingWS(), _Log(), "hi")
        )
    finally:
        mod._check_cancelled = original

    assert called["n"] == 1
