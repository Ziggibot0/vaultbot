"""
FreeSearch — VaultBot's own rate-limit-resistant search engine.

No API keys. No signup. No Docker. No per-host quotas that ban a sustained
autonomous research loop. The vaultbot can use the internet as much as it
wants, the same way you can.

Design: a *multi-engine aggregator*. A query is fanned out in parallel to
several independent keyless backends, each with its own polite throttle and
its own independent ban-cooldown. When one engine rate-limits us, the others
keep answering — no single engine can starve the dig. Cooldowns self-heal as
they expire, so a ban at minute 0 doesn't permanently cripple minute 30.

Backends (all keyless, all no-signup):
  - DuckDuckGoLite   — lite.duckduckgo.com HTML scrape. General web.
  - Marginalia       — marginalia-search.com HTML scrape. Non-mainstream /
                       deep / non-commercial content; very rate-limit-friendly.
  - Arxiv            — export.arxiv.org Atom API. Polite (3s throttle per
                       arXiv ToU). Full abstracts inline (no scrape needed).
  - (scrape step)    — direct fetch of whatever URLs the engines return, with
                       realistic headers. This is the "direct docs fetch":
                       arxiv abs pages, MDN, docs.python.org, etc. all come
                       back as clean article text.

This module exposes one class, FreeSearch, with the exact minimal surface the
research engine expects from a search client:
  - search(query, max_results=...) -> {"results": [...], "unresponsive_engines": [...]}
  - scrape(url, timeout=...)       -> cleaned article text (or "")
  - is_configured                  -> True (always)
  - set_api_key(key)               -> no-op (interface compat)

Each result dict has: url, title, content (snippet), raw_content (full text
or "" if it must be fetched via scrape()).

Wikipedia is blocked at every layer per [[No-Wikipedia-Directive]].
"""

import re
import threading
import time
from typing import Any

import requests
from bs4 import BeautifulSoup

# --- Source blocklist --------------------------------------------------------
# The authoritative blocklist lives in source_classification.py.
# Only Wikipedia is hard-blocked; all other sources are quality-scored
# (see source_quality()) rather than blocked.
from source_classification import is_blocked_source as _is_blocked_source


# Realistic browser headers — many sites 403 the python-requests default UA.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
_DEFAULT_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "application/atom+xml,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


# ---------------------------------------------------------------------------
# Base backend
# ---------------------------------------------------------------------------
class _Backend:
    """Common scaffolding: throttle, ban-cooldown, logging hooks.

    Subclasses implement `_raw_search(query, max_results)` returning a list of
    result dicts (url, title, content, raw_content). All ban/cooldown logic,
    throttling, and exception isolation is handled here so subclasses stay
    tiny and focused on parsing one engine's response.
    """

    name = "base"
    min_interval = 1.0  # polite throttle between requests
    cooldown_seconds = 60.0  # how long to back off after a ban/error burst
    ban_threshold = 2  # consecutive failures before cooldown kicks in

    def __init__(self, session_logger=None, timeout: int = 20):
        self.session_logger = session_logger
        self.timeout = timeout
        self._last_request_time: float = 0.0
        self._lock = threading.Lock()
        self._cooldown_until: float = 0.0
        self._consecutive_failures: int = 0

    # -- logging ----------------------------------------------------------
    def _log(
        self, method: str, inputs=None, outputs=None, duration_ms=None, error=None
    ):
        if self.session_logger is None:
            return
        try:
            self.session_logger.log_tool_call(
                tool=self.name,
                method=method,
                inputs=inputs,
                outputs=outputs,
                duration_ms=duration_ms,
                error=error,
            )
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            pass

    # -- throttle ---------------------------------------------------------
    def _throttle(self) -> None:
        """Sleep so we never exceed one request per `min_interval` seconds."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_time = time.time()

    # -- cooldown ---------------------------------------------------------
    def _in_cooldown(self) -> bool:
        return time.time() < self._cooldown_until

    def _cooldown_remaining(self) -> float:
        return max(0.0, self._cooldown_until - time.time())

    def _mark_failure(self, reason: str) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.ban_threshold:
                self._cooldown_until = time.time() + self.cooldown_seconds
                self._log(
                    "cooldown", {"reason": reason, "seconds": self.cooldown_seconds}
                )

    def _mark_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0

    # -- public search ----------------------------------------------------
    def search(
        self, query: str, max_results: int = 5
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Return (results, error_or_None). Handles cooldown + throttle."""
        if self._in_cooldown():
            return [], f"cooldown:{int(self._cooldown_remaining())}s"
        self._throttle()
        t0 = time.time()
        try:
            raw = self._raw_search(query, max_results)
            # Filter blocked domains defense-in-depth.
            clean = [r for r in raw if not _is_blocked_source(r.get("url", ""))]
            self._mark_success()
            self._log(
                "search",
                {"query": query, "max": max_results},
                outputs={"count": len(clean)},
                duration_ms=(time.time() - t0) * 1000,
            )
            return clean, None
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            reason = f"http_{status}"
            # 429/403/503 are ban signals; anything else is a transient error.
            if status in (403, 429, 503):
                self._mark_failure(reason)
            else:
                self._mark_failure("http_error")
            self._log(
                "search",
                {"query": query},
                error=reason,
                duration_ms=(time.time() - t0) * 1000,
            )
            return [], reason
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            self._mark_failure("exception")
            self._log(
                "search",
                {"query": query},
                error=str(e),
                duration_ms=(time.time() - t0) * 1000,
            )
            return [], str(e)

    # -- subclass hook -----------------------------------------------------
    def _raw_search(self, query: str, max_results: int) -> list[dict[str, Any]]:
        raise NotImplementedError

    # -- interface compat -------------------------------------------------
    @property
    def is_configured(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# DuckDuckGo Lite — general web, keyless
# ---------------------------------------------------------------------------
class DuckDuckGoLite(_Backend):
    name = "duckduckgo"
    min_interval = 1.2
    cooldown_seconds = 90.0  # DDG ban windows are long

    SEARCH_URL = "https://lite.duckduckgo.com/lite/"

    def _raw_search(self, query: str, max_results: int) -> list[dict[str, Any]]:
        # lite.duckduckgo.com returns a minimal HTML table — far less brittle
        # than the JS-heavy main site and lighter on bandwidth.
        resp = requests.post(
            self.SEARCH_URL,
            data={"q": query, "kl": "us-en"},
            headers=_DEFAULT_HEADERS,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        # Detect DDG's botnet anomaly/challenge page. When DDG flags the IP
        # as suspicious, it returns a 202 status with an HTML page whose
        # forms point to //duckduckgo.com/anomaly.js (instead of results).
        # Without this check, the parser silently returns 0 results (no
        # result-link anchors on the challenge page) and reports success —
        # masking the ban from the aggregator. By raising here, the base
        # class marks a failure and enters cooldown so other backends carry
        # the load and the ban self-heals.
        _forms = soup.find_all("form")
        if _forms and any(
            "anomaly.js" in (f.get("action") or "") for f in _forms
        ):
            raise requests.HTTPError(
                "duckduckgo botnet challenge page (anomaly.js)",
                response=resp,
            )
        results: list[dict[str, Any]] = []
        # lite.duckduckgo.com lays results out as a series of <tr> rows:
        #   row A: <a class="result-link" href="ABSOLUTE_URL">Title</a>
        #   row B: <td class="result-snippet">snippet...</td>
        #   row C: <span class="link-text">display-url</span>
        # The link is already absolute (real URL, no DDG redirect). The
        # snippet is in the *next sibling* <tr>, not the same row.
        for a in soup.select("a.result-link"):
            if len(results) >= max_results:
                break
            href = a.get("href", "")
            title = a.get_text(strip=True)
            if not href or not title:
                continue
            url = self._unwrap(href)
            if not url or _is_blocked_source(url):
                continue
            snippet = ""
            row = a.find_parent("tr")
            if row is not None:
                sib = row.find_next_sibling("tr")
                if sib is not None:
                    snip = sib.find("td", class_="result-snippet")
                    if snip:
                        snippet = snip.get_text(" ", strip=True)
            results.append(
                {
                    "url": url,
                    "title": title,
                    "content": snippet,
                    "raw_content": "",
                }
            )
        # Fallback: older lite markup uses .result__a anchors.
        if not results:
            for a in soup.select("a.result__a"):
                if len(results) >= max_results:
                    break
                href = a.get("href", "")
                title = a.get_text(strip=True)
                url = self._unwrap(href)
                if not url or _is_blocked_source(url):
                    continue
                snippet = ""
                cell = a.find_parent("td")
                if cell:
                    snip = cell.find_parent("tr")
                    if snip:
                        s = snip.find("td", class_="result__snippet")
                        if s:
                            snippet = s.get_text(" ", strip=True)
                results.append(
                    {
                        "url": url,
                        "title": title,
                        "content": snippet,
                        "raw_content": "",
                    }
                )
        return results

    @staticmethod
    def _unwrap(href: str) -> str:
        """Extract the real URL from a DDG redirect wrapper."""
        m = re.search(r"uddg=([^&]+)", href)
        if m:
            return requests.utils.unquote(m.group(1))
        if href.startswith("//"):
            return "https:" + href
        if href.startswith("/"):
            return "https://lite.duckduckgo.com" + href
        return href


# ---------------------------------------------------------------------------
# Marginalia — keyless, AI-friendly, very tolerant of automated use
# ---------------------------------------------------------------------------
class MarginaliaBackend(_Backend):
    name = "marginalia"
    min_interval = 1.5
    cooldown_seconds = 120.0
    ban_threshold = 3

    SEARCH_URL = "https://marginalia-search.com/search"

    # Internal marginalia paths that are NOT search results (nav, crawler
    # info, submission, API docs). Any link pointing into these is junk.
    _JUNK_SUBSTRINGS = (
        "/crawler-ips",
        "/submit",
        "/api",
        "/about",
        "/login",
        "/search",
        "/explore",
        "/site/",
        "/profile/",
        "/tools",
        "/docs",
        "/help",
        ".txt",
        ".json",
        ".xml",
        ".rss",
    )
    # External boilerplate URLs that appear in the page chrome/footer of the
    # results page (license links, etc.) and are never real search hits.
    # NOTE: "marginalia" (bare) catches ALL marginalia-owned hosts, including
    # chat.marginalia.nu ("project discord"), marginalia-search.com, and any
    # other subdomain — they are the search engine's own chrome, not results.
    _JUNK_DOMAINS = (
        "creativecommons.org",
        "gnu.org",
        "w3.org",
        "github.com/marginalia",
        "marginalia.nu",
        "marginalia-search.com",
    )

    def _raw_search(self, query: str, max_results: int) -> list[dict[str, Any]]:
        resp = requests.get(
            self.SEARCH_URL,
            params={"query": query},
            headers=_DEFAULT_HEADERS,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        results: list[dict[str, Any]] = []
        seen = set()
        # Marginalia renders each hit as a card containing a heading link to the
        # external page plus a description. The stable hook is an <a> whose
        # href is an external http(s) URL (not a marginalia internal path).
        for a in soup.select("a[href]"):
            if len(results) >= max_results:
                break
            href = a.get("href", "")
            if not href or href in seen:
                continue
            if href.startswith("/"):
                href = "https://marginalia-search.com" + href
            # Must be an external http(s) link.
            if not href.startswith(("http://", "https://")):
                continue
            # Skip internal marginalia links.
            if "marginalia-search.com" in href:
                continue
            if _is_blocked_source(href):
                continue
            # Skip obvious non-result assets / internal paths of target sites.
            low = href.lower()
            if any(sub in low for sub in self._JUNK_SUBSTRINGS):
                continue
            if any(dom in low for dom in self._JUNK_DOMAINS):
                continue
            title = a.get_text(strip=True)
            if not title or len(title) < 6:
                continue
            # Skip if the "title" is just a bare URL (the display-url row).
            if title.startswith(("http://", "https://")):
                continue
            seen.add(href)
            # The card's snippet is the surrounding card text minus the title.
            snippet = ""
            card = a.find_parent(["div", "section", "article", "li", "td"])
            if card:
                snippet = card.get_text(" ", strip=True)
                if title in snippet:
                    snippet = snippet.replace(title, "", 1).strip()
                snippet = snippet[:300]
            results.append(
                {
                    "url": href,
                    "title": title,
                    "content": snippet,
                    "raw_content": "",
                }
            )
        return results


# ---------------------------------------------------------------------------
# arXiv — keyless Atom API, polite 3s throttle, abstracts inline
# ---------------------------------------------------------------------------
class ArxivBackend(_Backend):
    name = "arxiv"
    min_interval = 3.0  # arXiv ToU asks for >=3s between requests
    cooldown_seconds = 60.0
    ban_threshold = 3

    API_URL = "http://export.arxiv.org/api/query"

    def _raw_search(self, query: str, max_results: int) -> list[dict[str, Any]]:
        # arXiv expects a fielded query; "all:" matches title+abstract+...
        q = query.strip()
        # Escape colons/quotes that break arXiv query syntax.
        q = re.sub(r"[\"\\]", " ", q)
        params = {
            "search_query": f"all:{q}",
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        resp = requests.get(
            self.API_URL,
            params=params,
            headers={"User-Agent": _BROWSER_UA, "Accept": "application/atom+xml"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        # Atom feed. Parse with BeautifulSoup in xml mode if available, else
        # fall back to html.parser (the feed is well-formed enough).
        try:
            soup = BeautifulSoup(resp.text, "xml")
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            soup = BeautifulSoup(resp.text, "html.parser")
        results: list[dict[str, Any]] = []
        for entry in soup.find_all("entry"):
            title = entry.find("title")
            summary = entry.find("summary")
            # The abs page is the human-readable link (rel="alternate").
            url = ""
            for link in entry.find_all("link"):
                if link.get("rel") == "alternate":
                    url = link.get("href", "")
                    break
            if not url:
                id_tag = entry.find("id")
                url = id_tag.get_text(strip=True) if id_tag else ""
            if not url or _is_blocked_source(url):
                continue
            t = title.get_text(" ", strip=True) if title else ""
            s = summary.get_text(" ", strip=True) if summary else ""
            results.append(
                {
                    "url": url,
                    "title": t,
                    "content": s[:400],  # snippet
                    "raw_content": s,  # full abstract — no scrape needed
                }
            )
        return results


# ---------------------------------------------------------------------------
# SearXNG — self-hosted meta-search in Docker. Opt-in backend.
# ---------------------------------------------------------------------------
# SearXNG is *itself* a multi-engine aggregator (Google, Brave, DDG, arXiv,
# GitHub, etc.). Running it in Docker as a private backend — with
# `server.limiter: false` (so it never throttles our own agent) + JSON
# output enabled — gives the vaultbot a "limitless" general-web engine with
# no per-host API quotas. It covers the mainstream web (Google/Brave) that
# the keyless backends above don't.
#
# This is OPTIONAL: if the Docker container isn't running (no Docker, or
# `searxng_manager` import fails), the backend reports itself as cooling
# down and the aggregator skips it — the other backends keep answering.
# SearXNG's upstream-engine IP-bans are isolated to this one backend; when
# they trip, the cooldown self-heals and the keyless engines carry on.
class SearxngBackend(_Backend):
    name = "searxng"
    # Local container — no throttle, no cooldown. The operator runs SearXNG
    # as a private backend with `server.limiter: false`, so there is no
    # reason for VaultBot to rate-limit its own search engine. A genuine
    # failure (container down) is surfaced immediately rather than hidden
    # behind a cooldown window.
    min_interval = 0.0
    cooldown_seconds = 0.0
    ban_threshold = 10**9  # effectively never enter cooldown

    def __init__(self, searxng_manager=None, session_logger=None, timeout: int = 20):
        # searxng_manager: a SearxngManager instance (which manages the
        # Docker container + exposes search/scrape). If None, this backend
        # is disabled and always reports "no_manager" so the aggregator
        # skips it cleanly.
        self._searxng = searxng_manager
        super().__init__(session_logger=session_logger, timeout=timeout)

    @property
    def is_configured(self) -> bool:
        return self._searxng is not None

    def _raw_search(self, query: str, max_results: int) -> list[dict[str, Any]]:
        if self._searxng is None:
            raise RuntimeError("no searxng manager")
        # ensure_running() starts (or self-heals) the Docker container. If
        # Docker isn't available this raises — which the base class turns
        # into a cooldown, so the aggregator skips us. We don't try forever:
        # the cooldown caps retries.
        self._searxng.ensure_running()
        data = self._searxng.search(query, timeout=self.timeout)
        if not data:
            return []
        results: list[dict[str, Any]] = []
        for r in (data.get("results") or [])[:max_results]:
            url = r.get("url", "")
            if not url or _is_blocked_source(url):
                continue
            results.append(
                {
                    "url": url,
                    "title": r.get("title", ""),
                    "content": r.get("content", ""),
                    # SearXNG returns snippets, not full article text — leave
                    # raw_content empty so the research engine scrapes it.
                    "raw_content": "",
                }
            )
        return results


# ---------------------------------------------------------------------------
# The aggregator
# ---------------------------------------------------------------------------
class FreeSearch:
    """VaultBot's own search engine: a parallel multi-engine aggregator.

    Fans a query out to all configured backends concurrently, merges + dedupes
    the results, and reports which engines are currently cooling down. Each
    backend throttles and bans independently, so one engine's rate limit never
    starves the whole dig — the others keep answering, and the banned engine
    self-heals as its cooldown expires.

    No API keys, no signup, no Docker. The same interface the research engine
    expected from Tavily/SearXNG: search() + scrape() + is_configured.
    """

    def __init__(
        self,
        session_logger=None,
        timeout: int = 20,
        backends: list[_Backend] | None = None,
        searxng_manager: Any = None,
    ):
        self.session_logger = session_logger
        self.timeout = timeout
        if backends is None:
            # Default fleet: general web (DDG) + deep/non-mainstream (Marginalia)
            # + academic (arXiv). All keyless.
            fleet: list[_Backend] = [
                DuckDuckGoLite(session_logger=session_logger, timeout=timeout),
                MarginaliaBackend(session_logger=session_logger, timeout=timeout),
                ArxivBackend(session_logger=session_logger, timeout=timeout),
            ]
            # SearXNG (self-hosted Docker) is an OPT-IN backend: only added
            # when a SearxngManager is wired in. It covers mainstream web
            # (Google/Brave) the keyless engines don't. If Docker isn't
            # available it self-disables and the pool keeps the 3 keyless
            # engines. See the "limitless SearXNG" notes in searxng_settings.yml.
            if searxng_manager is not None:
                fleet.append(
                    SearxngBackend(
                        searxng_manager=searxng_manager,
                        session_logger=session_logger,
                        timeout=timeout,
                    )
                )
            self._backends = fleet
        else:
            self._backends = backends

    # -- interface compat -------------------------------------------------
    @property
    def is_configured(self) -> bool:
        # Always at least one keyless backend is available.
        return True

    def set_api_key(self, key: str) -> None:
        """No-op — FreeSearch uses no API keys."""
        pass

    # -- search -----------------------------------------------------------
    def search(
        self, query: str, max_results: int = 5, search_depth: str = "advanced"
    ) -> dict[str, Any]:
        """Fan out to all backends in parallel, merge + dedupe.

        Returns {"results": [...], "unresponsive_engines": [["name","reason"],...]}.
        Each result has url, title, content, raw_content.
        """
        t0 = time.time()
        results_by_backend: dict[str, tuple[list[dict[str, Any]], str | None]] = {}
        threads = []

        def _run(b: _Backend):
            try:
                results_by_backend[b.name] = b.search(query, max_results)
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                # Should never happen (backend.search catches), but be safe.
                results_by_backend[b.name] = ([], f"agg_error:{e}")

        for b in self._backends:
            th = threading.Thread(target=_run, args=(b,), daemon=True)
            th.start()
            threads.append(th)
        # Wait for all; backends throttle internally so total wall time is
        # ~max(min_interval across backends), not the sum.
        for th in threads:
            th.join(timeout=max(self.timeout + 5, 30))

        unresponsive: list[list[str]] = []
        merged: list[dict[str, Any]] = []
        seen_urls: set = set()
        # Interleave so no single engine dominates the top of the list.
        buckets: dict[str, list[dict[str, Any]]] = {}
        for name, (res, err) in results_by_backend.items():
            if err:
                unresponsive.append([name, err])
            if res:
                buckets[name] = res
        # Round-robin merge: take one from each engine in turn.
        indices: dict[str, int] = {n: 0 for n in buckets}
        while any(indices[n] < len(buckets[n]) for n in buckets):
            for name in buckets:  # preserve a stable order
                i = indices[name]
                if i >= len(buckets[name]):
                    continue
                r = buckets[name][i]
                indices[name] = i + 1
                url = r.get("url", "")
                if not url or url in seen_urls or _is_blocked_source(url):
                    continue
                seen_urls.add(url)
                # Tag the source engine for provenance/debugging.
                r = dict(r, engine=name)
                merged.append(r)
                if len(merged) >= max_results * 2:
                    break
            if len(merged) >= max_results * 2:
                break

        out = merged[:max_results]
        if self.session_logger is not None:
            try:
                self.session_logger.log_tool_call(
                    tool="freesearch",
                    method="search",
                    inputs={"query": query, "max": max_results},
                    outputs={
                        "count": len(out),
                        "engines_up": len(results_by_backend) - len(unresponsive),
                        "engines_down": len(unresponsive),
                    },
                    duration_ms=(time.time() - t0) * 1000,
                )
            except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                pass
        return {"results": out, "unresponsive_engines": unresponsive}

    # -- scrape (direct docs fetch) ---------------------------------------
    def scrape(self, url: str, timeout: int = 12) -> str:
        """Return cleaned article text for a URL via direct fetch.

        Realistic headers + BeautifulSoup cleanup. This is the "direct docs
        fetch" — arxiv abs pages, MDN, docs.python.org, and any URL the
        engines returned come back as clean article text. Wikipedia is
        refused per [[No-Wikipedia-Directive]].
        """
        if _is_blocked_source(url):
            return ""
        headers = dict(_DEFAULT_HEADERS)
        try:
            resp = requests.get(
                url, headers=headers, timeout=timeout, allow_redirects=True
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            for tag in soup(
                [
                    "script",
                    "style",
                    "nav",
                    "footer",
                    "header",
                    "aside",
                    "form",
                    "noscript",
                    "svg",
                ]
            ):
                tag.decompose()
            # Strip Stack Exchange "Related" / "Hot Network Questions" sidebars
            # and inline related-question blocks BEFORE extracting text.
            # These inject dozens of off-topic questions (e.g. "How can I
            # remove a key from a Python dictionary?") into the scraped
            # body, where the synthesis then ranks them high because they
            # contain generic terms ("remove", "python", "vector") from the
            # query. They are navigation, not content.
            for sel in (
                "div.related",
                "div.module",
                "div.sidebar",
                "aside.related",
                "div.hot-network-questions",
                "div.js-related-questions",
                "div[data-tracker]",
                "div[id^='hot-network']",
                "div[id^='related']",
                "section.related",
                # SO inline "Related questions" card under answers.
                "div.related-questions",
                # Generic: any element whose class/id screams sidebar.
                "[class*='related']",
                "[id*='related']",
                "[class*='sidebar']",
                "[id*='sidebar']",
                "[class*='hot-network']",
                "[id*='hot-network']",
            ):
                for el in soup.select(sel):
                    el.decompose()
            # Also drop elements whose visible text is a "Related"/"Hot Network"
            # heading — catches variants the selectors miss.
            for heading in soup.find_all(
                ["h2", "h3", "h4", "div", "span"],
                string=re.compile(
                    r"\s*(Related|Hot Network Questions|Linked)\s*", re.I
                ),
            ):
                parent = heading.parent
                if parent is not None and parent.name in (
                    "div",
                    "section",
                    "aside",
                    "li",
                ):
                    parent.decompose()
            main = soup.find("article") or soup.find("main") or soup.find("body")
            text = (
                main.get_text(separator="\n", strip=True)
                if main
                else soup.get_text(separator="\n", strip=True)
            )
            # Collapse runs of blank lines.
            text = re.sub(r"\n{3,}", "\n\n", text)
            return text[:20000]
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            return ""
