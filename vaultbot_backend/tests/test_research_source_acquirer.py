from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest
import research_engine
import web_source_store
from research_engine import ResearchEngine

pytestmark = pytest.mark.unit


def _engine(monkeypatch, results):
    client = MagicMock()
    client.is_configured = True
    client.name = "test-search"
    client.search.return_value = {"results": results}
    engine = ResearchEngine(search_client=client)
    monkeypatch.setattr(research_engine, "_filter_dead_urls", None)
    monkeypatch.setattr(web_source_store, "save_source", MagicMock())
    monkeypatch.setattr(web_source_store, "fetch_and_save", MagicMock())
    return engine, client


def _accept_all(monkeypatch):
    monkeypatch.setattr(
        research_engine, "_source_relevance", lambda *a, **k: (1.5, "accepted")
    )
    monkeypatch.setattr(research_engine, "_is_github_issue_or_pr", lambda url: False)


def test_policy_filters_run_in_order_before_archive_or_scrape(monkeypatch):
    results = [
        {"url": "https://blocked.test/topic", "raw_content": "topic " * 40},
        {"url": "https://outside.test/topic", "raw_content": "topic " * 40},
        {"url": "https://denied.test/topic", "raw_content": "topic " * 40},
    ]
    engine, client = _engine(monkeypatch, results)
    checks = []
    monkeypatch.setattr(
        research_engine,
        "_is_blocked_source",
        lambda url: checks.append(("blocked", url)) or "blocked" in url,
    )
    monkeypatch.setattr(
        research_engine,
        "_is_allowlisted",
        lambda url, domains: checks.append(("allow", url)) or "outside" not in url,
    )
    monkeypatch.setattr(
        research_engine,
        "_is_denylisted",
        lambda url, domains: checks.append(("deny", url)) or "denied" in url,
    )

    assert engine._source_acquirer.search_round("topic", 2, topic="topic") == []
    assert checks == [
        ("blocked", results[0]["url"]),
        ("blocked", results[1]["url"]),
        ("allow", results[1]["url"]),
        ("blocked", results[2]["url"]),
        ("allow", results[2]["url"]),
        ("deny", results[2]["url"]),
    ]
    web_source_store.save_source.assert_not_called()
    web_source_store.fetch_and_save.assert_not_called()
    client.scrape.assert_not_called()


def test_archive_raw_content_and_fetch_short_content_paths(monkeypatch):
    raw_text = "topic " * 40
    engine, client = _engine(
        monkeypatch,
        [
            {"url": "https://raw.test", "title": "Raw", "raw_content": raw_text},
            {"url": "https://short.test", "title": "Short", "raw_content": "tiny"},
        ],
    )
    _accept_all(monkeypatch)
    client.scrape.return_value = "topic " * 40

    sources = engine._source_acquirer.search_round("topic", 0, topic="topic")

    assert [source["url"] for source in sources] == [
        "https://raw.test",
        "https://short.test",
    ]
    web_source_store.save_source.assert_called_once_with(
        "https://raw.test", raw_text, title="Raw", topic="topic"
    )
    web_source_store.fetch_and_save.assert_called_once_with(
        "https://short.test", title="Short", topic="topic"
    )
    client.scrape.assert_called_once_with("https://short.test", timeout=12)


def test_scrape_threshold_and_no_snippet_fallback(monkeypatch):
    engine, client = _engine(
        monkeypatch,
        [
            {
                "url": "https://short.test",
                "content": "topic " * 100,
                "raw_content": "x" * 79,
            }
        ],
    )
    _accept_all(monkeypatch)
    client.scrape.return_value = "too short"

    assert engine._source_acquirer.search_round("topic", 0, topic="topic") == []
    client.scrape.assert_called_once_with("https://short.test", timeout=12)


def test_rejection_reason_and_github_planning_event_are_preserved(monkeypatch):
    engine, _ = _engine(
        monkeypatch,
        [
            {
                "url": "https://irrelevant.test",
                "title": "Nope",
                "raw_content": "text " * 50,
            },
            {
                "url": "https://github.com/org/repo/issues/1",
                "title": "Topic issue",
                "raw_content": "topic " * 50,
            },
        ],
    )
    logger = MagicMock()
    engine.session_logger = logger
    monkeypatch.setattr(
        research_engine,
        "_source_relevance",
        lambda title, *a, **k: (
            (0.25, "missing signal") if title == "Nope" else (2.0, "accepted")
        ),
    )

    assert engine._source_acquirer.search_round("topic", 3, topic="topic") == []
    assert (
        call(
            "research_source_rejected",
            {
                "round": 3,
                "url": "https://irrelevant.test",
                "title": "Nope",
                "score": 0.25,
                "reason": "missing signal",
            },
        )
        in logger.log.call_args_list
    )
    assert (
        call(
            "research_source_skipped_github_issue",
            {
                "round": 3,
                "url": "https://github.com/org/repo/issues/1",
                "title": "Topic issue",
            },
        )
        in logger.log.call_args_list
    )


def test_dead_urls_are_filtered_with_live_session_logger(monkeypatch):
    engine, _ = _engine(
        monkeypatch,
        [
            {"url": "https://alive.test", "raw_content": "topic " * 40},
            {"url": "https://dead.test", "raw_content": "topic " * 40},
        ],
    )
    _accept_all(monkeypatch)
    logger = MagicMock()
    engine.session_logger = logger
    filter_urls = MagicMock(
        return_value=(["https://alive.test"], [("https://dead.test", "status_404")])
    )
    monkeypatch.setattr(research_engine, "_filter_dead_urls", filter_urls)

    sources = engine._source_acquirer.search_round("topic", 1, topic="topic")

    assert [source["url"] for source in sources] == ["https://alive.test"]
    filter_urls.assert_called_once_with(
        ["https://alive.test", "https://dead.test"],
        timeout=5.0,
        max_workers=5,
        session_logger=logger,
    )
    assert logger.log.call_args_list[-1] == call(
        "research_dead_urls_filtered",
        {
            "round": 1,
            "checked": 2,
            "alive": 1,
            "dead": 1,
            "dead_urls": [{"url": "https://dead.test", "reason": "status_404"}],
        },
    )


def test_live_owner_state_controls_limits_timeout_credibility_and_progress(monkeypatch):
    engine, client = _engine(
        monkeypatch,
        [
            {"url": "https://one.test", "raw_content": "short"},
            {"url": "https://two.test", "raw_content": "short"},
        ],
    )
    _accept_all(monkeypatch)
    engine.max_sources_per_round = 1
    engine.scrape_timeout = 7.9
    engine.credibility = MagicMock()
    engine.credibility.get.return_value = 0.83
    engine.credibility.get_label.return_value = "high"
    engine._progress = MagicMock()
    client.scrape.return_value = "topic " * 40

    sources = engine._source_acquirer.search_round("topic", 4, topic="topic")

    client.search.assert_called_once_with("topic", max_results=1)
    client.scrape.assert_called_once_with("https://one.test", timeout=7)
    engine._progress.assert_called_once_with(
        "scraping", {"round": 4, "url": "https://one.test", "title": ""}
    )
    assert sources[0]["_credibility"] == 0.83
    assert sources[0]["_credibility_label"] == "high"


def test_search_events_and_allowlist_fanout_contract(monkeypatch):
    logger = MagicMock()
    engine = ResearchEngine(session_logger=logger, search_client=None)
    assert engine._source_acquirer.search_round("topic", 5) == []
    logger.log.assert_called_once_with(
        "research_search_unconfigured", {"round": 5, "query": "topic"}
    )

    client = MagicMock()
    client.is_configured = True
    client.name = "broken"
    client.search.side_effect = RuntimeError("offline")
    engine.search_client = client
    assert engine._source_acquirer.search_round("topic", 6) == []
    assert logger.log.call_args_list[-2] == call(
        "research_search_failed",
        {"round": 6, "query": "topic", "backend": "broken", "error": "offline"},
    )
    assert logger.log.call_args_list[-1][0][0] == "research_search"

    engine._search_round = MagicMock(side_effect=[[{"url": "a"}], [{"url": "b"}]])
    assert engine._source_acquirer.search_with_source_policy(
        "q", 0, "topic", ["a.test", "b.test"], ["deny.test"]
    ) == [{"url": "a"}, {"url": "b"}]
    assert engine._search_round.call_args_list == [
        call(
            "q site:a.test",
            0,
            topic="topic",
            source_allowlist=["a.test", "b.test"],
            source_denylist=["deny.test"],
        ),
        call(
            "q site:b.test",
            0,
            topic="topic",
            source_allowlist=["a.test", "b.test"],
            source_denylist=["deny.test"],
        ),
    ]
