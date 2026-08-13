"""
DuckDuckGo search client — free, no API key, no signup. Zero setup.

Scrapes DuckDuckGo's HTML endpoint (https://html.duckduckgo.com/html/)
for search results, then fetches article content directly via requests +
BeautifulSoup. No API key, no signup, no rate-limit quota.

Implements a clean search interface for the research engine:
  - search(query) -> {"results": [{"url","title","content","raw_content"}], ...}
  - scrape(url)   -> cleaned article text
  - is_configured -> True (always)
  - set_api_key() -> no-op (interface compat)
"""

import re
import time
from typing import Any

import requests
from bs4 import BeautifulSoup

# --- Source blocklist (the operator's directive: never use Wikipedia) ---------------
# Any URL containing one of these substrings is filtered out of search
# results before they ever reach the research engine.
_BLOCKED_DOMAINS = {
    "wikipedia.org",
    "en.m.wikipedia.org",
    "simple.wikipedia.org",
}


def _is_blocked_source(url: str) -> bool:
    """Return True if the URL points to a blocked domain."""
    if not url:
        return False
    url_lower = url.lower()
    return any(domain in url_lower for domain in _BLOCKED_DOMAINS)


class DuckDuckGoClient:
    """Thin wrapper around DuckDuckGo's HTML search endpoint.

    No API key required. Self-rate-limits to ~1 request/second to avoid
    getting IP-banned. The research engine uses this as its sole search backend.
    """

    SEARCH_URL = "https://html.duckduckgo.com/html/"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def __init__(
        self, api_key: str | None = None, session_logger=None, timeout: int = 20
    ):
        # api_key accepted for interface compat but not used.
        self.api_key = None
        self.session_logger = session_logger
        self.timeout = timeout
        self._last_request_time: float = 0.0

    def _log(
        self,
        method: str,
        inputs: dict[str, Any] | None = None,
        outputs: Any = None,
        duration_ms: float | None = None,
        error: str | None = None,
    ):
        if self.session_logger is None:
            return
        self.session_logger.log_tool_call(
            tool="duckduckgo",
            method=method,
            inputs=inputs,
            outputs=outputs,
            duration_ms=duration_ms,
            error=error,
        )

    def set_api_key(self, key: str) -> None:
        """No-op — DuckDuckGo doesn't use API keys."""
        pass

    @property
    def is_configured(self) -> bool:
        return True

    def _throttle(self) -> None:
        """Self-rate-limit to ~1 request/second."""
        elapsed = time.time() - self._last_request_time
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        self._last_request_time = time.time()

    def search(
        self, query: str, max_results: int = 5, search_depth: str = "advanced"
    ) -> dict[str, Any]:
        """Search DuckDuckGo and return a result dict.

        Returns {"results": [...], "unresponsive_engines": [...]}.
        Each result has url, title, content (snippet), raw_content (empty —
        content is fetched separately via scrape()).

        Wikipedia and other blocked sources are filtered out per the operator's
        directive (see [[No-Wikipedia-Directive]]).
        """
        t0 = time.time()
        self._throttle()
        try:
            resp = requests.post(
                self.SEARCH_URL,
                data={"q": query},
                headers=self.HEADERS,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            results = []
            for result_div in soup.select(".result"):
                if len(results) >= max_results:
                    break
                title_tag = result_div.select_one(".result__a")
                snippet_tag = result_div.select_one(".result__snippet")
                if not title_tag:
                    continue
                title = title_tag.get_text(strip=True)
                raw_url = title_tag.get("href", "")
                # DuckDuckGo wraps URLs in a redirect; extract the real URL.
                url_match = re.search(r"uddg=([^&]+)", raw_url)
                actual_url = (
                    requests.utils.unquote(url_match.group(1)) if url_match else raw_url
                )
                # --- Source blocklist: skip Wikipedia and other banned domains ---
                if _is_blocked_source(actual_url):
                    self._log(
                        "search_source_blocked", {"url": actual_url, "title": title}
                    )
                    continue
                snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
                results.append(
                    {
                        "url": actual_url,
                        "title": title,
                        "content": snippet,
                        "raw_content": "",  # Fetched on demand via scrape()
                    }
                )

            self._log(
                "search",
                {"query": query},
                outputs={"result_count": len(results)},
                duration_ms=(time.time() - t0) * 1000,
            )
            return {"results": results, "unresponsive_engines": []}
        except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
            self._log(
                "search",
                {"query": query},
                error=str(e),
                duration_ms=(time.time() - t0) * 1000,
            )
            return {"results": [], "unresponsive_engines": [["duckduckgo", str(e)]]}

    def scrape(self, url: str, timeout: int = 12) -> str:
        """Return cleaned article text for a URL (direct fetch).

        Realistic headers,         BeautifulSoup cleanup, 20K char cap.
        """
        # Defense-in-depth: refuse to scrape blocked domains.
        if _is_blocked_source(url):
            self._log("scrape_blocked", {"url": url})
            return ""
        headers = {
            "User-Agent": self.HEADERS["User-Agent"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            resp = requests.get(
                url, headers=headers, timeout=timeout, allow_redirects=True
            )
            resp.raise_for_status()
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(resp.text, "lxml")
            for tag in soup(
                ["script", "style", "nav", "footer", "header", "aside", "form"]
            ):
                tag.decompose()
            main = soup.find("article") or soup.find("main") or soup.find("body")
            text = (
                main.get_text(separator="\n", strip=True)
                if main
                else soup.get_text(separator="\n", strip=True)
            )
            return text[:20000]
        except Exception:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
            return ""
