"""Tests for cooldown mechanics in free_search.py.

Grounding:
- pytest tmp_path, monkeypatch fixtures:
  https://docs.pytest.org/en/stable/reference/reference.html
- Anatomy of a test:
  https://docs.pytest.org/en/stable/explanation/anatomy.html

Cooldown fields (read from free_search._Backend):
- _cooldown_until: float       epoch-style timestamp (via time.time()) until
                               which the backend is considered in cooldown.
- cooldown_seconds: float      class attr; how long to back off after a ban.
- min_interval: float          polite throttle between requests.
- ban_threshold: int           consecutive failures before cooldown kicks in.
- _consecutive_failures: int   running failure counter.
- _last_request_time: float    timestamp of last request (for throttle).

_Backend.search() returns Tuple[List[Dict], Optional[str]] = (results, error):
  - If in cooldown: returns ([], "cooldown:{int}s")
  - On success:     returns (clean_results, None)
  - On exception:   returns ([], reason_str)

FreeSearch.search() returns:
  {"results": [...],
   "unresponsive_engines": [["name", "reason"], ...]}
  (unresponsive_engines is a list of [name, reason] pairs.)

No real network, no Ollama, no Docker, no vault. Time is controlled by
monkeypatching free_search.time.time / free_search.time.sleep so cooldown
windows can be advanced deterministically.
"""

import pytest

pytestmark = pytest.mark.unit

import free_search as fs


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------
class _FakeClock:
    """Deterministic clock replacing time.time for cooldown tests.

    Callable so it can be dropped in for the time.time function.
    """

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _CountingBackend(fs._Backend):
    """Backend whose _raw_search we can spy on; no real network."""

    name = "fake"
    min_interval = 0.0
    cooldown_seconds = 30.0

    def __init__(self):
        super().__init__()
        self.calls = 0

    def _raw_search(self, query, max_results):
        self.calls += 1
        return [
            {
                "url": "http://fake/result",
                "title": "Fake",
                "content": "c",
                "raw_content": "",
            }
        ]


class _BoomBackend(fs._Backend):
    """Backend that always raises — simulates a down engine."""

    name = "boom"
    min_interval = 0.0

    def _raw_search(self, query, max_results):
        raise RuntimeError("boom-down")


class _GoodBackend(fs._Backend):
    """Backend that always returns one canned hit."""

    name = "good"
    min_interval = 0.0

    def _raw_search(self, query, max_results):
        return [
            {"url": "http://b", "title": "B", "content": "content", "raw_content": ""}
        ]


# ---------------------------------------------------------------------------
# Test 1: cooldown self-heals once the window expires
# ---------------------------------------------------------------------------
def test_cooldown_self_heals(monkeypatch):
    """A backend in cooldown is skipped, but after the cooldown window
    expires it is used again — the cooldown self-heals with no manual
    intervention. See _Backend._in_cooldown / _cooldown_remaining_s.
    """
    clock = _FakeClock(start=1000.0)
    # Control time + neutralize sleep so the test never really blocks.
    monkeypatch.setattr(fs.time, "time", clock)
    monkeypatch.setattr(fs.time, "sleep", lambda _s: None)

    backend = _CountingBackend()
    search = fs.FreeSearch(backends=[backend])

    # Put the backend into cooldown: ban window ends 30s in the future.
    backend._cooldown_until = clock() + backend.cooldown_seconds  # 1030

    # Act part 1: search while in cooldown -> backend must be skipped.
    res1 = search.search("query")
    assert backend.calls == 0, "cooling backend's _raw_search must NOT run"
    down_names = [entry[0] for entry in res1["unresponsive_engines"]]
    assert "fake" in down_names
    assert res1["results"] == []

    # Act part 2: advance the clock past the cooldown window (+60s).
    clock.advance(60)  # now 1060 > 1030 -> out of cooldown

    # Assert part 2: backend is used again and returns results.
    res2 = search.search("query")
    assert backend.calls == 1, "backend should have been used after healing"
    up_names = [entry[0] for entry in res2["unresponsive_engines"]]
    assert "fake" not in up_names
    assert any(r["url"] == "http://fake/result" for r in res2["results"])


# ---------------------------------------------------------------------------
# Test 2: one backend failing does not kill the whole search
# ---------------------------------------------------------------------------
def test_unresponsive_engines_reported(monkeypatch):
    """A failing backend is reported in unresponsive_engines while the
    healthy backend's results still come through. The search does NOT
    raise — one engine going down never starves the whole dig.
    """
    # Neutralize sleep so the throttle never blocks the test.
    monkeypatch.setattr(fs.time, "sleep", lambda _s: None)

    search = fs.FreeSearch(backends=[_BoomBackend(), _GoodBackend()])

    res = search.search("query")

    # Backend A (boom) is reported as unresponsive; search did not raise.
    down_names = [entry[0] for entry in res["unresponsive_engines"]]
    assert "boom" in down_names
    assert "good" not in down_names

    # Backend B's result is returned in results.
    assert any(r["url"] == "http://b" for r in res["results"])
