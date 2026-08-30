from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest
from research_engine import ResearchEngine
from source_classification import normalize_source_domains

pytestmark = pytest.mark.unit


def test_normalize_source_domains_keeps_domain_contract():
    assert normalize_source_domains(
        [" WWW.Docs.Python.org. ", "docs.python.org", "peps.python.org"],
        field_name="source_allowlist",
    ) == ["docs.python.org", "peps.python.org"]


@pytest.mark.parametrize(
    "value",
    ["https://docs.python.org/3/", "docs.python.org/3", "docs.python.org:443"],
)
def test_normalize_source_domains_rejects_urls(value):
    with pytest.raises(ValueError, match="domains only"):
        normalize_source_domains([value], field_name="source_allowlist")


def test_allowlisted_domains_are_searched_independently(monkeypatch):
    engine = ResearchEngine(search_client=None)
    search_round = MagicMock(
        side_effect=[
            [{"url": "https://docs.python.org/3/"}],
            [{"url": "https://peps.python.org/pep-0008/"}],
        ]
    )
    monkeypatch.setattr(engine, "_search_round", search_round)

    sources = engine._search_with_source_policy(
        "jedi python library",
        0,
        "jedi",
        ["docs.python.org", "peps.python.org"],
        [],
    )

    assert [source["url"] for source in sources] == [
        "https://docs.python.org/3/",
        "https://peps.python.org/pep-0008/",
    ]
    assert search_round.call_args_list == [
        call(
            "jedi python library site:docs.python.org",
            0,
            topic="jedi",
            source_allowlist=["docs.python.org", "peps.python.org"],
            source_denylist=[],
        ),
        call(
            "jedi python library site:peps.python.org",
            0,
            topic="jedi",
            source_allowlist=["docs.python.org", "peps.python.org"],
            source_denylist=[],
        ),
    ]


def test_search_backend_cannot_bypass_allowlist():
    search_client = MagicMock()
    search_client.is_configured = True
    search_client.search.return_value = {
        "results": [
            {
                "url": "https://example.com/jedi",
                "title": "Unrelated Jedi",
                "raw_content": "jedi " * 50,
            }
        ]
    }
    engine = ResearchEngine(search_client=search_client)

    assert (
        engine._search_round(
            "jedi site:docs.python.org",
            0,
            topic="jedi",
            source_allowlist=["docs.python.org"],
        )
        == []
    )


def test_gap_fill_reuses_canonical_source_policy(monkeypatch):
    engine = ResearchEngine(max_rounds=1, search_client=None)
    search = MagicMock(
        side_effect=[
            [
                {
                    "url": "https://docs.python.org/3/",
                    "title": "Python documentation",
                    "text": "jedi completion documentation " * 20,
                    "_credibility": 0.9,
                    "_credibility_label": "high",
                }
            ],
            [],
        ]
    )
    monkeypatch.setattr(engine, "_search_with_source_policy", search)
    monkeypatch.setattr(engine, "_identify_gaps", lambda *args: ["completion"])
    monkeypatch.setattr(
        engine, "_extractive_synthesis", lambda *args: ("summary", set())
    )

    engine.research("jedi", source_allowlist=["WWW.Docs.Python.org."])

    assert search.call_args_list == [
        call("jedi", 0, "jedi", ["docs.python.org"], []),
        call("completion", 1, "jedi", ["docs.python.org"], []),
    ]


def test_constrained_research_fails_visibly_when_no_sources():
    engine = ResearchEngine(max_rounds=1, max_follow_ups=0, search_client=None)

    with pytest.raises(RuntimeError, match=r"docs\.python\.org"):
        engine.research("jedi", source_allowlist=["docs.python.org"])


def test_allowlist_cannot_be_fully_blocked_by_denylist():
    engine = ResearchEngine(max_rounds=1, search_client=None)

    with pytest.raises(ValueError, match="blocked by source_denylist"):
        engine.research(
            "jedi",
            source_allowlist=["docs.python.org"],
            source_denylist=["python.org"],
        )
