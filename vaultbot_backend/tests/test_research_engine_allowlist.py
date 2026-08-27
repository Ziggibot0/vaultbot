"""Focused tests for allowlist-aware research query construction."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
import research_engine as _research_engine_mod
from research_engine import ResearchEngine, _allowlist_site_ops

pytestmark = pytest.mark.unit


class _FakeSearchClient:
    is_configured = True
    name = "fake"

    def __init__(self):
        self.queries: list[str] = []

    def search(self, query: str, max_results: int = 5) -> dict:
        self.queries.append(query)
        return {
            "results": [
                {
                    "url": "https://jedi.readthedocs.io/en/latest/",
                    "title": "Jedi documentation",
                    "content": "Jedi static analysis library for Python.",
                    "raw_content": (
                        "Jedi is a static analysis tool for Python that supports "
                        "autocomplete, goto, and find references." * 3
                    ),
                }
            ]
        }

    def scrape(self, url: str, timeout: int = 12) -> str:
        return ""


def test_allowlist_site_ops_normalizes_and_deduplicates():
    ops = _allowlist_site_ops(
        [
            "jedi.readthedocs.io",
            "https://www.jedi.readthedocs.io/en/latest/",
            ".jedi.readthedocs.io",
            "",
        ]
    )
    assert ops == ["site:jedi.readthedocs.io"]


def test_research_appends_allowlist_site_ops_to_search_queries(monkeypatch):
    client = _FakeSearchClient()
    engine = ResearchEngine(max_rounds=2, search_client=client)

    monkeypatch.setitem(
        sys.modules,
        "web_source_store",
        SimpleNamespace(
            fetch_and_save=lambda *a, **k: None,
            save_source=lambda *a, **k: None,
        ),
    )
    monkeypatch.setattr(
        _research_engine_mod, "_source_relevance", lambda *a, **k: (1.0, "ok")
    )
    monkeypatch.setattr(_research_engine_mod, "_filter_dead_urls", None)
    monkeypatch.setattr(ResearchEngine, "_identify_gaps", lambda *a, **k: [])
    monkeypatch.setattr(
        ResearchEngine, "_extractive_synthesis", lambda *a, **k: ("- fact", {0})
    )

    engine.research(
        "jedi python library autocomplete",
        source_allowlist=["jedi.readthedocs.io"],
    )

    assert len(client.queries) == 2
    assert all("site:jedi.readthedocs.io" in q for q in client.queries)
    assert client.queries[0].startswith("jedi python library autocomplete")


def test_research_threads_allow_and_deny_lists_into_gap_fill(monkeypatch):
    engine = ResearchEngine(max_rounds=1, search_client=None)
    calls: list[dict[str, object]] = []

    def fake_search_round(
        query: str,
        round_idx: int,
        topic: str = "",
        source_allowlist: list[str] | None = None,
        source_denylist: list[str] | None = None,
    ) -> list[dict[str, str]]:
        calls.append(
            {
                "query": query,
                "round_idx": round_idx,
                "source_allowlist": source_allowlist,
                "source_denylist": source_denylist,
            }
        )
        return []

    monkeypatch.setattr(engine, "_search_round", fake_search_round)
    monkeypatch.setattr(engine, "_identify_gaps", lambda *a, **k: ["api documentation"])
    monkeypatch.setattr(engine, "_extractive_synthesis", lambda *a, **k: ("", set()))

    engine.research(
        "jedi python library",
        source_allowlist=["jedi.readthedocs.io"],
        source_denylist=["medium.com"],
    )

    assert len(calls) == 2
    assert "site:jedi.readthedocs.io" in str(calls[0]["query"])
    assert "site:jedi.readthedocs.io" in str(calls[1]["query"])
    assert calls[1]["source_allowlist"] == ["jedi.readthedocs.io"]
    assert calls[1]["source_denylist"] == ["medium.com"]
